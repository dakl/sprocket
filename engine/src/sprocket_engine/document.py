"""The live document: features, parameters, rollback index, edit log.

M0's document was a JSON file that `replay()` read. M1 makes it a thing that
several clients edit at once - the Swift app, the browser viewer, an MCP agent
and the test harness - so it needs three things a plain dict does not have.

**A revision.** Every mutation bumps it, and every edit names the revision it
was based on. An edit based on a stale revision is rejected with the current
state rather than silently applied on top of somebody else's work. That is the
whole concurrency story: no locks, no leases, the loser re-reads and retries.

**An edit log.** Who changed what, in order. The timeline shows it, and a
crashed engine replays from it.

**Parameters.** A feature field may hold `"=width * 2"` instead of a number.
Parameters resolve to a plain document before replay, so `replay()` and every
op schema still only ever see numbers.
"""

import ast
import copy
import hashlib
import json
import operator
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import TypedDict, cast, final

import jsonschema
from build123d import Compound

from sprocket_engine.materials import PRESETS
from sprocket_engine.model import (
    JSON,
    EntityReference,
    JSONObject,
    ReplayDocument,
    SolveResult,
    SolverSketch,
    json_objects,
    parse_json,
    sketch_feature,
    to_json,
)
from sprocket_engine.ops import REGISTRY
from sprocket_engine.ops.reference import set_document_loader
from sprocket_engine.replay import replay
from sprocket_engine.sketch_edit import assign_constraint_ids, edit_sketch
from sprocket_engine.sketch_reference import (
    Project,
    Projected,
    has_references,
    projector,
    with_references_updated,
)

FORMAT_VERSION = 2

# A feature field or a parameter holding a string that starts with this is an
# expression over the parameters, not a literal. A prefix rather than "try to
# parse every string as an expression", because a plane name like "XY" is a
# valid Python expression and would resolve to an undefined-name error.
EXPRESSION_PREFIX = "="

_BINARY: dict[type[ast.operator], Callable[[float, float], float]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: lambda a, b: a / b,
    ast.Mod: operator.mod,
    ast.Pow: lambda a, b: _power(a, b),
}
_UNARY: dict[type[ast.unaryop], Callable[[float], float]] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


def _power(base: float, exponent: float) -> float:
    # Python's ** gives an int, a float, or, for a negative base to a
    # fractional power, a complex number.
    found = cast("float | complex", base**exponent)
    if isinstance(found, complex):
        raise DocumentError(f"{base} ** {exponent} is not a real number")
    return found


class DocumentError(Exception):
    """A document, an edit or an expression the engine will not accept.

    Every raise carries a sentence meant for a human or an agent to read. The
    app puts this straight into its failure status, so a traceback here would
    be shown to the user.
    """


class StaleRevisionError(DocumentError):
    """An edit based on a revision that is no longer current.

    Carries the current revision so the caller can re-read and retry without a
    second round trip.
    """

    def __init__(self, based_on: int, current: int) -> None:
        """Record the revision the edit was based on and the current one."""
        super().__init__(
            f"edit was based on revision {based_on}, but the document is at revision {current}"
        )
        self.based_on: int = based_on
        self.current: int = current


@final
class Snapshot(TypedDict):
    """The editable state of a document, for undo and for 'is it saved'."""

    features: list[JSONObject]
    rollback_index: int
    parameters: JSONObject
    appearances: list[JSONObject]
    bodies: dict[str, JSONObject]


@final
class TimelineEntry(TypedDict):
    """One feature as the timeline sidebar and an agent read it."""

    index: int
    id: JSON
    type: JSON
    built: bool
    author: str | None


def evaluate(expression: str, parameters: dict[str, float]) -> float:
    """Evaluates one arithmetic expression over already-resolved parameters.

    Parsed with `ast` and walked node by node rather than handed to `eval`.
    A document is a file the app opens, and an agent writes parameters into it,
    so an expression is untrusted input on both paths.

    Arithmetic and parameter names only. No function calls: `min`/`max`/`sqrt`
    are the obvious next ask, and they are deliberately absent until something
    needs them.
    """
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise DocumentError(
            f"cannot parse expression {expression!r}: {exc.msg}"
        ) from None

    def walk(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return walk(node.body)
        if isinstance(node, ast.Constant):
            value: object = node.value
            if isinstance(value, bool) or not isinstance(value, int | float):
                raise DocumentError(
                    f"expression {expression!r} holds a non-numeric constant"
                )
            return value
        if isinstance(node, ast.Name):
            if node.id not in parameters:
                known = ", ".join(sorted(parameters)) or "none"
                raise DocumentError(
                    f"expression {expression!r} uses unknown parameter {node.id!r} (known: {known})"
                )
            return parameters[node.id]
        if isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
            return _BINARY[type(node.op)](walk(node.left), walk(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
            return _UNARY[type(node.op)](walk(node.operand))
        raise DocumentError(
            f"expression {expression!r} uses {type(node).__name__}, which is not allowed here"
        )

    try:
        value = walk(tree)
    except ZeroDivisionError:
        raise DocumentError(f"expression {expression!r} divides by zero") from None
    return float(value)


def resolve_parameters(parameters: JSONObject) -> dict[str, float]:
    """Turns a parameter table into plain numbers, resolving references between
    them. Raises on a cycle rather than recursing until the stack dies.
    """
    resolved: dict[str, float] = {}
    resolving: list[str] = []

    def resolve(name: str) -> float:
        if name in resolved:
            return resolved[name]
        if name in resolving:
            cycle = " -> ".join([*resolving[resolving.index(name) :], name])
            raise DocumentError(f"parameters form a cycle: {cycle}")

        raw = parameters[name]
        if isinstance(raw, bool) or not isinstance(raw, int | float | str):
            raise DocumentError(
                f"parameter {name!r} must be a number or an expression string"
            )
        if not isinstance(raw, str):
            resolved[name] = float(raw)
            return resolved[name]
        if not raw.startswith(EXPRESSION_PREFIX):
            raise DocumentError(
                f"parameter {name!r} is a string, so it must start with {EXPRESSION_PREFIX!r}"
            )

        resolving.append(name)
        # Resolve every name this expression mentions before evaluating it, so
        # `evaluate` only ever sees numbers and the cycle check sits here.
        expression = raw[len(EXPRESSION_PREFIX) :]
        for node in ast.walk(_parse(expression)):
            if isinstance(node, ast.Name):
                if node.id not in parameters:
                    raise DocumentError(
                        f"parameter {name!r} references unknown parameter {node.id!r}"
                    )
                _ = resolve(node.id)
        _ = resolving.pop()

        resolved[name] = evaluate(expression, resolved)
        return resolved[name]

    for name in parameters:
        _ = resolve(name)
    return resolved


def _parse(expression: str) -> ast.Expression:
    try:
        return ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise DocumentError(
            f"cannot parse expression {expression!r}: {exc.msg}"
        ) from None


def resolve_value(value: JSON, parameters: dict[str, float]) -> JSON:
    """Replaces expression strings with numbers, anywhere in a feature."""
    if isinstance(value, str) and value.startswith(EXPRESSION_PREFIX):
        return evaluate(value[len(EXPRESSION_PREFIX) :], parameters)
    if isinstance(value, dict):
        return {key: resolve_value(item, parameters) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve_value(item, parameters) for item in value]
    return value


def resolve_object(value: JSONObject, parameters: dict[str, float]) -> JSONObject:
    """`resolve_value` for an object, which stays an object."""
    return {key: resolve_value(item, parameters) for key, item in value.items()}


def keep_expressions(
    old: JSON,
    new: JSON,
    parameters: dict[str, float],
    unlinked: list[JSON],
    path: str = "",
) -> JSON:
    """`new` with the `"=param"` strings of `old` put back wherever the value did not change.

    The viewer and the engine's sketch solver work in numbers, so a sketch
    comes back from either with every expression evaluated. Writing that over
    the stored feature would cut every field loose from its parameter, when the
    person moved one point. So a number that still equals its old expression's
    value keeps the expression. A field whose value did change takes the number,
    and its path goes into `unlinked` so the caller can say the link was replaced.
    Entities and constraints are matched by id, so a reordered list is fine.
    """
    if (
        isinstance(old, str)
        and old.startswith(EXPRESSION_PREFIX)
        and isinstance(new, int | float)
        and not isinstance(new, bool)
    ):
        try:
            value = evaluate(old[len(EXPRESSION_PREFIX) :], parameters)
        except DocumentError:
            return new
        if abs(value - new) <= 1e-6 * max(1.0, abs(value)):
            return old
        unlinked.append({"field": path, "was": old, "now": new})
        return new
    if isinstance(old, dict) and isinstance(new, dict):
        return {
            key: keep_expressions(
                old[key], value, parameters, unlinked, f"{path}.{key}" if path else key
            )
            if key in old
            else value
            for key, value in new.items()
        }
    if isinstance(old, list) and isinstance(new, list):
        olds = [item for item in old if isinstance(item, dict) and "id" in item]
        news = [item for item in new if isinstance(item, dict) and "id" in item]
        if len(olds) == len(old) and len(news) == len(new):
            kept: list[JSON] = []
            for item in news:
                # The last match, as a dict keyed by id would give.
                match = next(
                    (other for other in reversed(olds) if other["id"] == item["id"]),
                    None,
                )
                kept.append(
                    item
                    if match is None
                    else keep_expressions(
                        match, item, parameters, unlinked, f"{path}[{item['id']}]"
                    )
                )
            return kept
        if len(old) == len(new):
            return [
                keep_expressions(a, b, parameters, unlinked, f"{path}[{i}]")
                for i, (a, b) in enumerate(zip(old, new, strict=True))
            ]
    return new


def _keep_object(
    old: JSONObject,
    new: JSONObject,
    parameters: dict[str, float],
    unlinked: list[JSON],
) -> JSONObject:
    """`keep_expressions` for a feature, which stays an object."""
    kept = keep_expressions(old, new, parameters, unlinked)
    if not isinstance(kept, dict):
        raise TypeError("keeping expressions turned an object into something else")
    return kept


def validate_feature(feature: JSON, parameters: dict[str, float]) -> JSONObject:
    """Checks one feature against its op's JSON Schema and returns it resolved.

    Validation runs on the resolved feature, never the raw one: the schemas say
    `distance` is a number, and a parameterised distance is the string
    `"=height"` until parameters are applied.
    """
    if not isinstance(feature, dict):
        raise DocumentError("a feature must be a JSON object")
    feature_id = feature.get("id")
    if not isinstance(feature_id, str) or not feature_id:
        raise DocumentError("a feature needs a non-empty string 'id'")
    op_type = feature.get("type")
    op = REGISTRY.get(op_type) if isinstance(op_type, str) else None
    if op is None:
        known = ", ".join(sorted(REGISTRY))
        raise DocumentError(f"unknown op type {op_type!r} (known: {known})")

    resolved = resolve_object(feature, parameters)
    try:
        jsonschema.validate(resolved, op.schema)
    except jsonschema.ValidationError as exc:
        raise DocumentError(
            f"feature {feature_id!r} failed validation: {exc.message}"
        ) from None
    return resolved


def _text(value: JSON) -> str | None:
    return value if isinstance(value, str) else None


def _index(value: JSON) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise DocumentError("'index' must be an integer")
    return value


class Document:
    """One editable document, plus its revision, log and parameters.

    Not thread-safe on its own. The daemon serialises every call through one
    lock, because OCCT is not thread-safe either and a second replay running
    beside the first is a crash, not a race.
    """

    def __init__(
        self,
        features: list[JSONObject] | None = None,
        rollback_index: int | None = None,
        parameters: JSONObject | None = None,
        revision: int = 0,
        log: list[JSONObject] | None = None,
        path: Path | None = None,
        appearances: list[JSONObject] | None = None,
        bodies: dict[str, JSONObject] | None = None,
    ) -> None:
        """Hold the given state. Nothing is checked here: `load` checks a file."""
        self.features: list[JSONObject] = list(features or [])
        self.rollback_index: int = (
            len(self.features) if rollback_index is None else rollback_index
        )
        self.parameters: JSONObject = dict(parameters or {})
        # Colours by region, applied at mesh export: [{"color": "#rrggbb", "select": <selector>}].
        self.appearances: list[JSONObject] = list(appearances or [])
        # Settings per body, keyed by body id: {"name", "color", "visible", "material"}.
        self.bodies: dict[str, JSONObject] = dict(bodies or {})
        self.revision: int = revision
        self.log: list[JSONObject] = list(log or [])
        self.path: Path | None = Path(path) if path else None
        # The hash of the bytes last read from or written to `path`. The daemon
        # compares the file against it to tell its own save from someone else's.
        self.saved_sha256: str | None = None
        # The editable state as the file holds it, for `has_unsaved_changes`. The
        # file's bytes cannot tell: they carry the revision and the log, so an
        # undo back to the saved state still writes different bytes.
        self.saved_snapshot: Snapshot | None = None

    # ---- loading and saving ------------------------------------------------

    @classmethod
    def load(cls, path: Path) -> "Document":
        """Reads a document file, checking what replay and the daemon rely on.

        A document that is missing, truncated or the wrong shape has to come
        back as a readable sentence. `ContentView.swift` puts a failed build's
        output straight into the user's status line, so an uncaught
        `JSONDecodeError` here is a Python traceback in the app window.

        M0 documents carry only `features` and `rollback_index`. They load as
        revision 0 with no parameters and an empty log.
        """
        path = Path(path)
        try:
            raw_bytes = path.read_bytes()
            raw = raw_bytes.decode()
        except OSError as exc:
            raise DocumentError(
                f"cannot read document {path}: {exc.strerror}"
            ) from None
        except UnicodeDecodeError:
            # A ValueError, not an OSError, so the clause above does not catch
            # it. Pointing at an .stl or .step instead of the .sprocket file lands here.
            raise DocumentError(
                f"{path} is not UTF-8 text, so it is not a document"
            ) from None
        try:
            data = parse_json(raw)
        except ValueError as exc:
            raise DocumentError(f"{path} is not valid JSON: {exc}") from None
        if not isinstance(data, dict):
            raise DocumentError(f"{path}: a document must be a JSON object")

        # Present, not merely well formed. A file with no `features` at all is
        # a typo or the wrong file far more often than it is an empty part,
        # and defaulting it to [] builds nothing and reports success.
        features_value = data.get("features")
        if not isinstance(features_value, list) or not all(
            isinstance(item, dict) for item in features_value
        ):
            raise DocumentError(f"{path}: 'features' must be a list of objects")
        features = [item for item in features_value if isinstance(item, dict)]

        rollback_index = data.get("rollback_index", len(features))
        if isinstance(rollback_index, bool) or not isinstance(rollback_index, int):
            raise DocumentError(f"{path}: 'rollback_index' must be an integer")
        if not 0 <= rollback_index <= len(features):
            raise DocumentError(
                f"{path}: 'rollback_index' is {rollback_index}, outside 0..{len(features)}"
            )

        parameters = data.get("parameters", {})
        if not isinstance(parameters, dict):
            raise DocumentError(f"{path}: 'parameters' must be a JSON object")

        revision = data.get("revision", 0)
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
            raise DocumentError(f"{path}: 'revision' must be a non-negative integer")

        log = data.get("log", [])
        if not isinstance(log, list):
            raise DocumentError(f"{path}: 'log' must be a list")

        appearances = data.get("appearances", [])
        try:
            checked = _check_appearances(appearances)
        except DocumentError as exc:
            raise DocumentError(f"{path}: {exc}") from None

        bodies_value = data.get("bodies", {})
        if not isinstance(bodies_value, dict):
            raise DocumentError(
                f"{path}: 'bodies' must be a JSON object keyed by body id"
            )
        bodies: dict[str, JSONObject] = {}
        for body_id, settings in bodies_value.items():
            try:
                bodies[body_id] = _check_body_settings(settings)
            except DocumentError as exc:
                raise DocumentError(f"{path}: body {body_id!r}: {exc}") from None

        for feature in features:
            assign_constraint_ids(feature)
        document = cls(
            features,
            rollback_index,
            parameters,
            revision,
            [entry for entry in log if isinstance(entry, dict)],
            path,
            checked,
            bodies,
        )
        _ = resolve_parameters(document.parameters)  # fail at open, not at first build
        document.saved_sha256 = hashlib.sha256(raw_bytes).hexdigest()
        document.saved_snapshot = document.snapshot()
        return document

    def to_json(self) -> JSONObject:
        """The document as its file holds it."""
        return {
            "format_version": FORMAT_VERSION,
            "features": [*self.features],
            "rollback_index": self.rollback_index,
            "parameters": self.parameters,
            "appearances": [*self.appearances],
            "bodies": {**self.bodies},
            "revision": self.revision,
            "log": [*self.log],
        }

    def save(self, path: Path | None = None) -> Path:
        """Writes through a temporary file in the same directory, then renames.

        A half-written document is the one failure this file cannot survive:
        the app reopens it on launch, and the edit log is the only record of
        what the user did. `os.replace` under the hood is atomic within a
        filesystem, so a crash mid-write leaves the previous version intact.
        """
        target = Path(path) if path else self.path
        if target is None:
            raise DocumentError("this document has no path, so it cannot be saved")
        temporary = target.with_name(target.name + ".tmp")
        data = (json_text(self.to_json()) + "\n").encode()
        _ = temporary.write_bytes(data)
        _ = temporary.replace(target)
        self.path = target
        self.saved_sha256 = hashlib.sha256(data).hexdigest()
        self.saved_snapshot = self.snapshot()
        return target

    def has_unsaved_changes(self) -> bool:
        """Whether the editable state differs from what was last loaded or saved."""
        return self.snapshot() != self.saved_snapshot

    def file_sha256(self) -> str | None:
        """The hash of what is on disk at `path` now, or None if nothing is."""
        try:
            return (
                hashlib.sha256(self.path.read_bytes()).hexdigest()
                if self.path
                else None
            )
        except OSError:
            return None

    # ---- reading -----------------------------------------------------------

    def resolved_parameters(self) -> dict[str, float]:
        """The parameter table, every value a number."""
        return resolve_parameters(self.parameters)

    def for_replay(self) -> ReplayDocument:
        """The plain document `replay()` takes: numbers only, no log.

        A reference's `file` becomes absolute, against this document's folder,
        and gains the file's hash. The hash is part of the feature, so the
        BREP cache key changes when the referenced file does.
        """
        parameters = self.resolved_parameters()
        features = [resolve_object(feature, parameters) for feature in self.features]
        sources = dict(self.reference_sources())
        for index, feature in enumerate(features):
            feature_id = feature.get("id")
            if isinstance(feature_id, str) and feature_id in sources:
                path = sources[feature_id]
                features[index] = {
                    **feature,
                    "file": str(path),
                    "source_sha256": reference_digest(path),
                }
        return {"features": features, "rollback_index": self.rollback_index}

    def reference_sources(self) -> list[tuple[str | None, Path]]:
        """(feature id, absolute path) for each `reference` feature with a file."""
        folder = self.path.parent if self.path else Path.cwd()
        return [
            (_text(feature.get("id")), (folder / file).resolve())
            for feature in self.features
            if feature.get("type") == "reference"
            and isinstance(file := feature.get("file"), str)
        ]

    def referenced_files(self) -> list[Path]:
        """Every file this document's build reads besides itself: its references,
        and theirs, and so on. A cycle is cut where it closes.
        """
        found: list[Path] = []
        pending = [path for _, path in self.reference_sources()]
        while pending:
            path = pending.pop()
            if path in found or path == self.path:
                continue
            found.append(path)
            pending.extend(_nested_references(path))
        return found

    def index_of(self, feature_id: JSON) -> int:
        """The position of the feature with `feature_id`."""
        for index, feature in enumerate(self.features):
            if feature.get("id") == feature_id:
                return index
        raise DocumentError(f"no feature with id {feature_id!r}")

    def timeline(self) -> list[TimelineEntry]:
        """The feature list as the timeline sidebar and an agent read it.

        `built` says whether the rollback bar includes the feature, which is
        the one thing neither the feature list nor the log carries.
        """
        return [
            {
                "index": index,
                "id": feature.get("id"),
                "type": feature.get("type"),
                "built": index < self.rollback_index,
                "author": self._author_of(feature.get("id")),
            }
            for index, feature in enumerate(self.features)
        ]

    def _author_of(self, feature_id: JSON) -> str | None:
        """Who added this feature, read back off the log. Newest insert wins,
        because a deleted-then-reinserted id belongs to whoever put it back.
        """
        for entry in reversed(self.log):
            if (
                entry.get("action") == "insert"
                and entry.get("feature_id") == feature_id
            ):
                return _text(entry.get("author"))
        return None

    # ---- editing -----------------------------------------------------------

    def apply_edits(self, edits: JSON, base_revision: int | None, author: str) -> int:
        """Applies a list of edits as one unit and returns the new revision.

        All or nothing. The edits run against a copy, and the copy replaces the
        real state only once every edit has been accepted. A three-edit batch
        that fails on the third must not leave the first two applied, because
        the caller's retry would then apply them twice.

        `base_revision=None` means "I am not racing anyone", which is what the
        CLI and the tests use. Every networked client passes a real revision.
        """
        if base_revision is not None and base_revision != self.revision:
            raise StaleRevisionError(base_revision, self.revision)
        if not isinstance(edits, list) or not edits:
            raise DocumentError("apply_edits needs a non-empty list of edits")

        trial = Document(
            self.features,
            self.rollback_index,
            self.parameters,
            self.revision,
            [],
            appearances=copy.deepcopy(self.appearances),
            bodies=copy.deepcopy(self.bodies),
        )
        entries = [trial._apply_one(edit, author) for edit in edits]

        self.features = trial.features
        self.rollback_index = trial.rollback_index
        self.parameters = trial.parameters
        self.appearances = trial.appearances
        self.bodies = trial.bodies
        self.revision += 1
        for entry in entries:
            entry["revision"] = self.revision
            self.log.append(entry)
        return self.revision

    def snapshot(self) -> Snapshot:
        """The editable state, deep-copied, for undo. The log is not part of it:
        undoing an edit is itself an entry in the log, not an erasure.
        """
        return copy.deepcopy(
            {
                "features": self.features,
                "rollback_index": self.rollback_index,
                "parameters": self.parameters,
                "appearances": self.appearances,
                "bodies": self.bodies,
            }
        )

    def restore(
        self,
        snapshot: Snapshot,
        author: str,
        *,
        log: bool = True,
        action: str = "restore",
    ) -> int:
        """Puts back a state taken with `snapshot()` and returns the new revision.
        Undoing a view change (`log=False`) leaves no entry in the log. A reload
        from disk logs `action="reload"`.
        """
        self.features = copy.deepcopy(snapshot["features"])
        self.rollback_index = snapshot["rollback_index"]
        self.parameters = copy.deepcopy(snapshot["parameters"])
        self.appearances = copy.deepcopy(snapshot["appearances"])
        self.bodies = copy.deepcopy(snapshot["bodies"])
        self.revision += 1
        if log:
            self.log.append(
                {
                    "author": author,
                    "action": action,
                    "at": _now(),
                    "revision": self.revision,
                }
            )
        return self.revision

    def set_visibility(self, body_id: str, visible: JSON) -> int:
        """Shows or hides one body: view state, saved with the document but not
        an edit, so it adds nothing to the log. The daemon still puts it on the
        undo stack. A hidden body still exports.
        """
        _ = self.index_of(body_id)
        if not isinstance(visible, bool):
            raise DocumentError("'visible' must be true or false")
        settings = {
            key: value
            for key, value in self.bodies.get(body_id, {}).items()
            if key != "visible"
        }
        if not visible:
            settings["visible"] = False
        if settings:
            self.bodies[body_id] = settings
        else:
            _ = self.bodies.pop(body_id, None)
        self.revision += 1
        return self.revision

    def _apply_one(self, edit: JSON, author: str) -> JSONObject:
        if not isinstance(edit, dict):
            raise DocumentError("an edit must be a JSON object")
        kind = edit.get("op")
        handlers: dict[str, Callable[[JSONObject], JSONObject]] = {
            "insert": self._insert,
            "update": self._update,
            "delete": self._delete,
            "move": self._move,
            "set_rollback": self._set_rollback,
            "set_parameter": self._set_parameter,
            "edit_sketch": self._edit_sketch,
            "set_body": self._set_body,
            "set_appearances": self._set_appearances,
        }
        handler = handlers.get(kind) if isinstance(kind, str) else None
        if handler is None:
            raise DocumentError(
                f"unknown edit op {kind!r} (known: insert, update, delete, move, set_rollback, set_parameter, edit_sketch, set_body, set_appearances)"
            )
        detail = handler(edit)
        return {"author": author, "action": kind, "at": _now(), **detail}

    def _insert(self, edit: JSONObject) -> JSONObject:
        feature = edit.get("feature")
        _ = validate_feature(feature, self.resolved_parameters())
        if not isinstance(feature, dict):
            raise DocumentError("a feature must be a JSON object")
        assign_constraint_ids(feature)
        feature_id = feature["id"]
        if any(existing.get("id") == feature_id for existing in self.features):
            raise DocumentError(f"a feature with id {feature_id!r} already exists")

        index = _index(edit.get("index", len(self.features)))
        if not 0 <= index <= len(self.features):
            raise DocumentError(f"index {index} is outside 0..{len(self.features)}")
        self._check_references(feature, available=self.features[:index])

        self.features.insert(index, feature)
        # An op appended at the bar is meant to be seen, so the bar moves with
        # it. An op inserted behind the bar keeps the same features built.
        if index <= self.rollback_index:
            self.rollback_index += 1
        return {"feature_id": feature_id, "index": index}

    def _update(self, edit: JSONObject) -> JSONObject:
        feature = edit.get("feature")
        parameters = self.resolved_parameters()
        _ = validate_feature(feature, parameters)
        if not isinstance(feature, dict):
            raise DocumentError("a feature must be a JSON object")
        assign_constraint_ids(feature)
        feature_id = edit.get("id", feature.get("id"))
        index = self.index_of(feature_id)
        if feature["id"] != feature_id:
            raise DocumentError("an update cannot change a feature's id")
        self._check_references(feature, available=self.features[:index])
        unlinked: list[JSON] = []
        self.features[index] = _keep_object(
            self.features[index], feature, parameters, unlinked
        )
        detail: JSONObject = {"feature_id": feature_id, "index": index}
        if unlinked:
            detail["unlinked"] = unlinked
        return detail

    def _delete(self, edit: JSONObject) -> JSONObject:
        feature_id = edit.get("id")
        index = self.index_of(feature_id)
        dependants = [
            later.get("id")
            for later in self.features[index + 1 :]
            if feature_id in _referenced_ids(later)
        ]
        if dependants:
            raise DocumentError(
                f"cannot delete {feature_id!r}: {', '.join(repr(d) for d in dependants)} still reference it"
            )
        del self.features[index]
        if index < self.rollback_index:
            self.rollback_index -= 1
        return {"feature_id": feature_id, "index": index}

    def _move(self, edit: JSONObject) -> JSONObject:
        feature_id = edit.get("id")
        index = self.index_of(feature_id)
        target = _index(edit.get("index"))
        if not 0 <= target < len(self.features):
            raise DocumentError(
                f"index {target} is outside 0..{len(self.features) - 1}"
            )

        moved = self.features.pop(index)
        self.features.insert(target, moved)
        # Reordering is the one edit that can put a feature in front of what it
        # references, so check the whole list rather than just the moved one.
        self._check_order()
        return {"feature_id": feature_id, "index": target, "from_index": index}

    def _set_rollback(self, edit: JSONObject) -> JSONObject:
        index = _index(edit.get("index"))
        if not 0 <= index <= len(self.features):
            raise DocumentError(
                f"rollback index {index} is outside 0..{len(self.features)}"
            )
        self.rollback_index = index
        return {"index": index}

    def _set_parameter(self, edit: JSONObject) -> JSONObject:
        name = edit.get("name")
        if not isinstance(name, str) or not name.isidentifier():
            raise DocumentError(f"parameter name {name!r} must be a valid identifier")
        value = edit.get("value")
        if value is None:
            if name not in self.parameters:
                raise DocumentError(f"no parameter named {name!r} to remove")
            candidate = {
                key: item for key, item in self.parameters.items() if key != name
            }
        else:
            candidate = {**self.parameters, name: value}

        # Resolve the whole table, not just this entry: a new value can break
        # another parameter that references it, and every feature expression
        # has to still evaluate afterwards.
        before = self.resolved_parameters()
        resolved = resolve_parameters(candidate)
        self.parameters = candidate
        for feature in self.features:
            _ = validate_feature(feature, resolved)
        detail: JSONObject = {"name": name, "value": value}
        detail.update(self._resolve_sketches(before, resolved))
        return detail

    def _resolve_sketches(
        self, before: dict[str, float], after: dict[str, float]
    ) -> JSONObject:
        """Solves again each sketch whose numbers a parameter change moved.

        Replay trusts the stored entities and does not solve, so a dimension
        written as `"=width"` would otherwise move nothing but its own number.
        A sketch counts when its constraints or entities, with the expressions
        worked out, differ between the old parameters and the new. Its solved
        entities go back into the feature, `"=param"` strings kept where their
        value held. The sketches that project from it are brought up to date
        when the part is built, as for any edit to their source.

        A sketch the new numbers make unsolvable refuses the whole edit, naming
        the conflicting constraints. Without the solver nothing can move: the
        reply lists such sketches as `unsolved`, so nobody trusts stale geometry.
        """
        unsolved: list[JSON] = []
        unlinked: list[JSON] = []
        # Not imported until a sketch needs it: planegcs is slow to load.
        solve: Callable[[SolverSketch], SolveResult] | None = None
        solver_missing = False
        for index, stored in enumerate(self.features):
            if stored.get("type") != "sketch" or "entities" not in stored:
                continue
            old = resolve_object(stored, before)
            new = resolve_object(stored, after)
            if old["entities"] == new["entities"] and old.get("constraints") == new.get(
                "constraints"
            ):
                continue
            if solve is None and not solver_missing:
                solve = _solver()
                solver_missing = solve is None
            if solve is None:
                unsolved.append(stored["id"])
                continue
            sketch = sketch_feature(new)
            if sketch is None:
                continue
            if has_references(new):
                try:
                    sketch = with_references_updated(
                        sketch, self._projector(index, after)
                    )
                except Exception:
                    # A reference that does not resolve is that feature's own failure to report, not this edit's.
                    unsolved.append(stored["id"])
                    continue
            constraints = sketch.get("constraints", [])
            result = solve(
                {"entities": sketch.get("entities", []), "constraints": constraints}
            )
            if not result["ok"]:
                names = ", ".join(
                    f"{constraints[i].get('id', i)} ({constraints[i]['kind']})"
                    for i in result["conflicting"]
                )
                raise DocumentError(
                    f"that value leaves sketch {stored['id']!r} with no solution"
                    + (f". These constraints conflict: {names}" if names else "")
                )
            solved = to_json({**sketch, "entities": result["sketch"]["entities"]})
            self.features[index] = _keep_object(
                stored, {**stored, "entities": solved["entities"]}, after, unlinked
            )
        detail: JSONObject = {}
        if unsolved:
            detail["unsolved"] = unsolved
        if unlinked:
            detail["unlinked"] = unlinked
        return detail

    def _edit_sketch(self, edit: JSONObject) -> JSONObject:
        feature_id = edit.get("id")
        index = self.index_of(feature_id)
        feature = copy.deepcopy(self.features[index])
        if feature.get("type") != "sketch" or "entities" not in feature:
            raise DocumentError(
                f"{feature_id!r} is not a sketch of lines, arcs and circles"
            )
        parameters = self.resolved_parameters()
        try:
            detail = edit_sketch(
                feature,
                edit,
                lambda draft: resolve_object(draft, parameters),
                self._projector(index, parameters),
            )
        except ValueError as exc:
            raise DocumentError(str(exc)) from None
        _ = validate_feature(feature, parameters)
        unlinked: list[JSON] = []
        self.features[index] = _keep_object(
            self.features[index], feature, parameters, unlinked
        )
        if unlinked:
            detail["unlinked"] = unlinked
        return {"feature_id": feature_id, "index": index, **detail}

    def _projector(self, index: int, parameters: dict[str, float]) -> Project:
        """Turns a reference's source into the entity it projects to, for the sketch at `index`.

        The features before the sketch are built on the first call, because a
        sketch on a face finds its plane in them. Only an edit that projects
        or re-projects pays for that.
        """
        built: list[Project] = []

        def project(source: EntityReference) -> Projected:
            if not built:
                earlier = self.for_replay()["features"][:index]
                _, ctx = replay(
                    {"features": earlier, "rollback_index": len(earlier)},
                    return_context=True,
                )
                sketch = sketch_feature(
                    resolve_object(self.features[index], parameters)
                )
                if sketch is None:
                    raise DocumentError("only a sketch projects references")
                built.append(projector(sketch["plane"], ctx))
            return built[0](source)

        return project

    def sync_references(self, built: dict[str, JSONObject]) -> None:
        """Copies the solved entities of sketches with projected references back
        into the stored features. `built` maps a sketch id to its feature as
        replay left it. The numbers are derived from the sources, so this is a
        cache refresh, not an edit: no revision, no log entry, no undo step.
        """
        parameters = self.resolved_parameters()
        for index, stored in enumerate(self.features):
            stored_id = _text(stored.get("id"))
            fresh = built.get(stored_id) if stored_id is not None else None
            if (
                stored.get("type") != "sketch"
                or fresh is None
                or not has_references(fresh)
            ):
                continue
            if not _same_numbers(
                resolve_object(stored, parameters).get("entities"),
                fresh.get("entities"),
            ):
                self.features[index] = _keep_object(
                    stored,
                    {**stored, "entities": fresh.get("entities")},
                    parameters,
                    [],
                )

    def _set_body(self, edit: JSONObject) -> JSONObject:
        """Changes one body's name or colour. A field set to None goes back to
        its default. The body id is the id of the feature that started the
        body, which need not be built yet. Visibility is not an edit: see
        `set_visibility`.
        """
        body_id = edit.get("id")
        _ = self.index_of(body_id)
        if not isinstance(body_id, str):
            raise DocumentError("a body id must be a string")
        if "visible" in edit:
            raise DocumentError(
                "visibility is view state, not an edit: use set_visibility"
            )
        settings = dict(self.bodies.get(body_id, {}))
        for name in ("name", "color", "material"):
            if name not in edit:
                continue
            if edit[name] is None:
                _ = settings.pop(name, None)
            else:
                settings[name] = edit[name]
        settings = _check_body_settings(settings)
        if settings:
            self.bodies[body_id] = settings
        else:
            _ = self.bodies.pop(body_id, None)
        return {
            "body": body_id,
            **{
                name: edit[name]
                for name in ("name", "color", "material")
                if name in edit
            },
        }

    def _set_appearances(self, edit: JSONObject) -> JSONObject:
        """Replaces the face colours: [{"color": "#rrggbb", "select": <selector>}],
        applied in order over the body colours, later ones winning.
        """
        appearances = _check_appearances(edit.get("appearances"))
        self.appearances = copy.deepcopy(appearances)
        return {"count": len(appearances)}

    def _check_references(
        self, feature: JSONObject, available: list[JSONObject]
    ) -> None:
        """A feature may only reference features that come before it.

        Replay walks the list once and looks each reference up in what it has
        already built, so a forward reference is a `KeyError` deep inside an
        op. Caught here it names both features instead.
        """
        earlier = {
            feature_id
            for item in available
            if isinstance(feature_id := item.get("id"), str)
        }
        for referenced in _referenced_ids(feature):
            if referenced not in earlier:
                raise DocumentError(
                    f"feature {feature['id']!r} references {referenced!r}, "
                    "which does not come before it"
                )

    def _check_order(self) -> None:
        seen: set[str] = set()
        for feature in self.features:
            for referenced in _referenced_ids(feature):
                if referenced not in seen:
                    raise DocumentError(
                        f"feature {feature.get('id')!r} references {referenced!r}, "
                        "which does not come before it"
                    )
            feature_id = feature.get("id")
            if isinstance(feature_id, str):
                seen.add(feature_id)


def json_text(value: JSON) -> str:
    """The document's own JSON layout: two-space indent, keys in insertion order."""
    return json.dumps(value, indent=2)


# The fields an op uses to name another feature. One list rather than a
# per-op declaration, because a second place to keep in step with the op
# registry is exactly what broke the BREP cache's op-type map. I wrote this
# tuple with two entries and grep found a third, `tool` on boolean, so the
# coverage check in test_document.py reads the schemas rather than trusting
# this line. Add a field here when an op grows a new reference.
REFERENCE_FIELDS = ("base", "profile", "tool", "sketch")


def _nested_references(path: Path) -> list[Path]:
    """The files a referenced .sprocket references itself. Read as plain JSON,
    because a file that does not load is the reference op's error to report.
    """
    if path.suffix != ".sprocket" or not path.is_file():
        return []
    try:
        data = parse_json(path.read_text())
    except (OSError, ValueError):
        return []
    features = data.get("features") if isinstance(data, dict) else None
    return [
        (path.parent / file).resolve()
        for feature in (features if isinstance(features, list) else [])
        if isinstance(feature, dict)
        and feature.get("type") == "reference"
        and isinstance(file := feature.get("file"), str)
    ]


def reference_digest(path: Path, seen: frozenset[Path] | None = None) -> str | None:
    """SHA-256 of a referenced file and, for a .sprocket, of everything it
    references in turn. It goes in the BREP cache key, so an edit two
    references deep rebuilds this document too.
    """
    if not path.is_file():
        return None
    digest = hashlib.sha256(path.read_bytes())
    seen = seen or frozenset()
    if path not in seen:
        for nested in _nested_references(path):
            digest.update(
                (reference_digest(nested, seen | {path}) or "missing").encode()
            )
    return digest.hexdigest()


def _check_appearances(appearances: JSON) -> list[JSONObject]:
    if not isinstance(appearances, list) or not all(
        isinstance(item, dict)
        and re.fullmatch(r"#[0-9a-fA-F]{6}", str(item.get("color", "")))
        and isinstance(item.get("select"), dict)
        and set(item) <= {"color", "select", "material"}
        for item in appearances
    ):
        raise DocumentError(
            '\'appearances\' must be a list of {"color": "#rrggbb", "select": <selector>, "material": <optional preset>}'
        )
    checked = json_objects(appearances, "'appearances'")
    for item in checked:
        material = item.get("material")
        if "material" in item and (
            not isinstance(material, str) or material not in PRESETS
        ):
            raise DocumentError(
                f"unknown material {material!r} (known: {', '.join(PRESETS)})"
            )
    return checked


def _check_body_settings(settings: JSON) -> JSONObject:
    if not isinstance(settings, dict):
        raise DocumentError("body settings must be a JSON object")
    unknown = set(settings) - {"name", "color", "visible", "material"}
    if unknown:
        raise DocumentError(
            f"unknown body settings {sorted(unknown)} (known: name, color, visible, material)"
        )
    material = settings.get("material")
    if "material" in settings and (
        not isinstance(material, str) or material not in PRESETS
    ):
        raise DocumentError(
            f"unknown material {material!r} (known: {', '.join(PRESETS)})"
        )
    if "name" in settings and not (
        isinstance(settings["name"], str) and settings["name"]
    ):
        raise DocumentError("a body name must be a non-empty string")
    if "color" in settings and not re.fullmatch(
        r"#[0-9a-fA-F]{6}", str(settings["color"])
    ):
        raise DocumentError(f"a body color must be #rrggbb, not {settings['color']!r}")
    if "visible" in settings and not isinstance(settings["visible"], bool):
        raise DocumentError("a body's visible must be true or false")
    return settings


def _named(value: JSON, keys: tuple[str, ...]) -> list[str]:
    """The string values of `keys` in `value`, when it is an object."""
    if not isinstance(value, dict):
        return []
    return [item for key in keys if isinstance(item := value.get(key), str)]


def _referenced_ids(feature: JSONObject) -> list[str]:
    referenced = _named(feature, REFERENCE_FIELDS)
    # A sketch on a face names the feature that holds the face.
    plane = feature.get("plane")
    referenced.extend(_named(plane, ("feature",)))
    # A plane, an axis or a pattern direction may name a construct feature, or the body an axis is found on.
    circular = feature.get("circular")
    named: list[JSON] = [
        plane,
        feature.get("axis"),
        circular.get("axis") if isinstance(circular, dict) else None,
    ]
    for key in ("linear", "linear2"):
        linear = feature.get(key)
        named.append(linear.get("direction") if isinstance(linear, dict) else None)
    for item in named:
        referenced.extend(_named(item, ("construct", "base")))
    # A projected reference names the sketch or body it was projected from.
    entities = feature.get("entities")
    for entity in entities if isinstance(entities, list) else []:
        source = entity.get("reference") if isinstance(entity, dict) else None
        referenced.extend(_named(source, ("sketch", "feature")))
    # A sweep path and picked regions name their sketches one level down.
    referenced.extend(_named(feature.get("path"), ("sketch",)))
    profiles = feature.get("profiles")
    for profile in profiles if isinstance(profiles, list) else []:
        referenced.extend(_named(profile, ("sketch",)))
    # A loft names its sections as a list of ids.
    sections = feature.get("sections")
    referenced.extend(
        section
        for section in (sections if isinstance(sections, list) else [])
        if isinstance(section, str)
    )
    return referenced


def _same_numbers(a: JSON, b: JSON) -> bool:
    """Whether two JSON values are equal, numbers to within what a solver leaves over."""
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_same_numbers(a[key], b[key]) for key in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(
            _same_numbers(x, y) for x, y in zip(a, b, strict=True)
        )
    if (
        isinstance(a, int | float)
        and isinstance(b, int | float)
        and not isinstance(a, bool)
    ):
        return abs(a - b) < 1e-9
    return a == b


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _solver() -> Callable[[SolverSketch], SolveResult] | None:
    """The sketch solver, or None when the `solver` extra is not installed."""
    try:
        from sprocket_engine.solver import solve
    except ImportError:
        return None
    return solve


# Documents being read right now, so a document that includes itself fails
# with a message instead of recursing until the stack runs out.
_loading: set[Path] = set()


def _build_reference(path: Path) -> Compound:
    """Build a referenced .sprocket file into the one shape it makes."""
    if path in _loading:
        raise ValueError(f"reference cycle: {path.name} includes itself")
    _loading.add(path)
    try:
        part = replay(Document.load(path).for_replay())
    finally:
        _loading.discard(path)
    if part is None or not part.solids():
        raise ValueError(f"referenced document {path.name} builds no solid")
    return part


set_document_loader(_build_reference)
