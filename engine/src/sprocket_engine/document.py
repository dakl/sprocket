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
import json
import operator
import re
import time
from pathlib import Path

import jsonschema

from .ops import REGISTRY
from .sketch_edit import assign_constraint_ids, edit_sketch

FORMAT_VERSION = 2

# A feature field or a parameter holding a string that starts with this is an
# expression over the parameters, not a literal. A prefix rather than "try to
# parse every string as an expression", because a plane name like "XY" is a
# valid Python expression and would resolve to an undefined-name error.
EXPRESSION_PREFIX = "="

_BINARY = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}


class DocumentError(Exception):
    """A document, an edit or an expression the engine will not accept.

    Every raise carries a sentence meant for a human or an agent to read. The
    app puts this straight into its failure status, so a traceback here would
    be shown to the user.
    """


class StaleRevision(DocumentError):
    """An edit based on a revision that is no longer current.

    Carries the current revision so the caller can re-read and retry without a
    second round trip.
    """

    def __init__(self, based_on: int, current: int):
        super().__init__(
            f"edit was based on revision {based_on}, but the document is at revision {current}"
        )
        self.based_on = based_on
        self.current = current


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
        raise DocumentError(f"cannot parse expression {expression!r}: {exc.msg}") from None

    def walk(node):
        if isinstance(node, ast.Expression):
            return walk(node.body)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
                raise DocumentError(f"expression {expression!r} holds a non-numeric constant")
            return node.value
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


def resolve_parameters(parameters: dict) -> dict[str, float]:
    """Turns a parameter table into plain numbers, resolving references between
    them. Raises on a cycle rather than recursing until the stack dies."""
    resolved: dict[str, float] = {}
    resolving: list[str] = []

    def resolve(name: str) -> float:
        if name in resolved:
            return resolved[name]
        if name in resolving:
            cycle = " -> ".join(resolving[resolving.index(name):] + [name])
            raise DocumentError(f"parameters form a cycle: {cycle}")

        raw = parameters[name]
        if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
            raise DocumentError(f"parameter {name!r} must be a number or an expression string")
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
        expression = raw[len(EXPRESSION_PREFIX):]
        for node in ast.walk(_parse(expression)):
            if isinstance(node, ast.Name):
                if node.id not in parameters:
                    raise DocumentError(
                        f"parameter {name!r} references unknown parameter {node.id!r}"
                    )
                resolve(node.id)
        resolving.pop()

        resolved[name] = evaluate(expression, resolved)
        return resolved[name]

    for name in parameters:
        resolve(name)
    return resolved


def _parse(expression: str) -> ast.Expression:
    try:
        return ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise DocumentError(f"cannot parse expression {expression!r}: {exc.msg}") from None


def resolve_value(value, parameters: dict[str, float]):
    """Replaces expression strings with numbers, anywhere in a feature."""
    if isinstance(value, str) and value.startswith(EXPRESSION_PREFIX):
        return evaluate(value[len(EXPRESSION_PREFIX):], parameters)
    if isinstance(value, dict):
        return {key: resolve_value(item, parameters) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve_value(item, parameters) for item in value]
    return value


def validate_feature(feature, parameters: dict[str, float]) -> dict:
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
    op = REGISTRY.get(feature.get("type"))
    if op is None:
        known = ", ".join(sorted(REGISTRY))
        raise DocumentError(f"unknown op type {feature.get('type')!r} (known: {known})")

    resolved = resolve_value(feature, parameters)
    try:
        jsonschema.validate(resolved, op.schema)
    except jsonschema.ValidationError as exc:
        raise DocumentError(f"feature {feature_id!r} failed validation: {exc.message}") from None
    return resolved


class Document:
    """One editable document, plus its revision, log and parameters.

    Not thread-safe on its own. The daemon serialises every call through one
    lock, because OCCT is not thread-safe either and a second replay running
    beside the first is a crash, not a race.
    """

    def __init__(
        self,
        features: list[dict] | None = None,
        rollback_index: int | None = None,
        parameters: dict | None = None,
        revision: int = 0,
        log: list[dict] | None = None,
        path: Path | None = None,
        appearances: list[dict] | None = None,
    ):
        self.features = list(features or [])
        self.rollback_index = len(self.features) if rollback_index is None else rollback_index
        self.parameters = dict(parameters or {})
        # Colours by region, applied at mesh export: [{"color": "#rrggbb", "select": <selector>}].
        self.appearances = list(appearances or [])
        self.revision = revision
        self.log = list(log or [])
        self.path = Path(path) if path else None

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
            raw = path.read_text()
        except OSError as exc:
            raise DocumentError(f"cannot read document {path}: {exc.strerror}") from None
        except UnicodeDecodeError:
            # A ValueError, not an OSError, so the clause above does not catch
            # it. Pointing at an .stl or .step instead of the .sprocket file lands here.
            raise DocumentError(f"{path} is not UTF-8 text, so it is not a document") from None
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise DocumentError(f"{path} is not valid JSON: {exc}") from None
        if not isinstance(data, dict):
            raise DocumentError(f"{path}: a document must be a JSON object")

        # Present, not merely well formed. A file with no `features` at all is
        # a typo or the wrong file far more often than it is an empty part,
        # and defaulting it to [] builds nothing and reports success.
        features = data.get("features")
        if not isinstance(features, list) or not all(isinstance(item, dict) for item in features):
            raise DocumentError(f"{path}: 'features' must be a list of objects")

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
        if not isinstance(appearances, list) or not all(
            isinstance(item, dict)
            and re.fullmatch(r"#[0-9a-fA-F]{6}", str(item.get("color", "")))
            and isinstance(item.get("select"), dict)
            for item in appearances
        ):
            raise DocumentError(f"{path}: 'appearances' must be a list of {{\"color\": \"#rrggbb\", \"select\": <selector>}}")

        for feature in features:
            assign_constraint_ids(feature)
        document = cls(features, rollback_index, parameters, revision, log, path, appearances)
        resolve_parameters(document.parameters)  # fail at open, not at first build
        return document

    def to_json(self) -> dict:
        return {
            "format_version": FORMAT_VERSION,
            "features": self.features,
            "rollback_index": self.rollback_index,
            "parameters": self.parameters,
            "appearances": self.appearances,
            "revision": self.revision,
            "log": self.log,
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
        temporary.write_text(json.dumps(self.to_json(), indent=2) + "\n")
        temporary.replace(target)
        self.path = target
        return target

    # ---- reading -----------------------------------------------------------

    def resolved_parameters(self) -> dict[str, float]:
        return resolve_parameters(self.parameters)

    def for_replay(self) -> dict:
        """The plain document `replay()` takes: numbers only, no log."""
        parameters = self.resolved_parameters()
        return {
            "features": [resolve_value(feature, parameters) for feature in self.features],
            "rollback_index": self.rollback_index,
        }

    def index_of(self, feature_id: str) -> int:
        for index, feature in enumerate(self.features):
            if feature.get("id") == feature_id:
                return index
        raise DocumentError(f"no feature with id {feature_id!r}")

    def timeline(self) -> list[dict]:
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

    def _author_of(self, feature_id) -> str | None:
        """Who added this feature, read back off the log. Newest insert wins,
        because a deleted-then-reinserted id belongs to whoever put it back."""
        for entry in reversed(self.log):
            if entry.get("action") == "insert" and entry.get("feature_id") == feature_id:
                return entry.get("author")
        return None

    # ---- editing -----------------------------------------------------------

    def apply_edits(self, edits: list[dict], base_revision: int | None, author: str) -> int:
        """Applies a list of edits as one unit and returns the new revision.

        All or nothing. The edits run against a copy, and the copy replaces the
        real state only once every edit has been accepted. A three-edit batch
        that fails on the third must not leave the first two applied, because
        the caller's retry would then apply them twice.

        `base_revision=None` means "I am not racing anyone", which is what the
        CLI and the tests use. Every networked client passes a real revision.
        """
        if base_revision is not None and base_revision != self.revision:
            raise StaleRevision(base_revision, self.revision)
        if not isinstance(edits, list) or not edits:
            raise DocumentError("apply_edits needs a non-empty list of edits")

        trial = Document(self.features, self.rollback_index, self.parameters, self.revision, [])
        entries = [trial._apply_one(edit, author) for edit in edits]

        self.features = trial.features
        self.rollback_index = trial.rollback_index
        self.parameters = trial.parameters
        self.revision += 1
        for entry in entries:
            entry["revision"] = self.revision
            self.log.append(entry)
        return self.revision

    def snapshot(self) -> dict:
        """The editable state, deep-copied, for undo. The log is not part of it:
        undoing an edit is itself an entry in the log, not an erasure."""
        return copy.deepcopy(
            {
                "features": self.features,
                "rollback_index": self.rollback_index,
                "parameters": self.parameters,
                "appearances": self.appearances,
            }
        )

    def restore(self, snapshot: dict, author: str) -> int:
        """Puts back a state taken with `snapshot()` and returns the new revision."""
        self.features = copy.deepcopy(snapshot["features"])
        self.rollback_index = snapshot["rollback_index"]
        self.parameters = copy.deepcopy(snapshot["parameters"])
        self.appearances = copy.deepcopy(snapshot.get("appearances", []))
        self.revision += 1
        self.log.append({"author": author, "action": "restore", "at": _now(), "revision": self.revision})
        return self.revision

    def _apply_one(self, edit: dict, author: str) -> dict:
        if not isinstance(edit, dict):
            raise DocumentError("an edit must be a JSON object")
        kind = edit.get("op")
        handler = {
            "insert": self._insert,
            "update": self._update,
            "delete": self._delete,
            "move": self._move,
            "set_rollback": self._set_rollback,
            "set_parameter": self._set_parameter,
            "edit_sketch": self._edit_sketch,
        }.get(kind)
        if handler is None:
            raise DocumentError(f"unknown edit op {kind!r} (known: insert, update, delete, move, set_rollback, set_parameter, edit_sketch)")
        detail = handler(edit)
        return {"author": author, "action": kind, "at": _now(), **detail}

    def _insert(self, edit: dict) -> dict:
        feature = edit.get("feature")
        validate_feature(feature, self.resolved_parameters())
        assign_constraint_ids(feature)
        feature_id = feature["id"]
        if any(existing.get("id") == feature_id for existing in self.features):
            raise DocumentError(f"a feature with id {feature_id!r} already exists")

        index = edit.get("index", len(self.features))
        if isinstance(index, bool) or not isinstance(index, int):
            raise DocumentError("'index' must be an integer")
        if not 0 <= index <= len(self.features):
            raise DocumentError(f"index {index} is outside 0..{len(self.features)}")
        self._check_references(feature, available=self.features[:index])

        self.features.insert(index, feature)
        # An op appended at the bar is meant to be seen, so the bar moves with
        # it. An op inserted behind the bar keeps the same features built.
        if index <= self.rollback_index:
            self.rollback_index += 1
        return {"feature_id": feature_id, "index": index}

    def _update(self, edit: dict) -> dict:
        feature = edit.get("feature")
        validate_feature(feature, self.resolved_parameters())
        assign_constraint_ids(feature)
        feature_id = edit.get("id", feature.get("id"))
        index = self.index_of(feature_id)
        if feature["id"] != feature_id:
            raise DocumentError("an update cannot change a feature's id")
        self._check_references(feature, available=self.features[:index])
        self.features[index] = feature
        return {"feature_id": feature_id, "index": index}

    def _delete(self, edit: dict) -> dict:
        feature_id = edit.get("id")
        index = self.index_of(feature_id)
        dependants = [
            later.get("id")
            for later in self.features[index + 1:]
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

    def _move(self, edit: dict) -> dict:
        feature_id = edit.get("id")
        index = self.index_of(feature_id)
        target = edit.get("index")
        if isinstance(target, bool) or not isinstance(target, int):
            raise DocumentError("'index' must be an integer")
        if not 0 <= target < len(self.features):
            raise DocumentError(f"index {target} is outside 0..{len(self.features) - 1}")

        moved = self.features.pop(index)
        self.features.insert(target, moved)
        # Reordering is the one edit that can put a feature in front of what it
        # references, so check the whole list rather than just the moved one.
        self._check_order()
        return {"feature_id": feature_id, "index": target, "from_index": index}

    def _set_rollback(self, edit: dict) -> dict:
        index = edit.get("index")
        if isinstance(index, bool) or not isinstance(index, int):
            raise DocumentError("'index' must be an integer")
        if not 0 <= index <= len(self.features):
            raise DocumentError(f"rollback index {index} is outside 0..{len(self.features)}")
        self.rollback_index = index
        return {"index": index}

    def _set_parameter(self, edit: dict) -> dict:
        name = edit.get("name")
        if not isinstance(name, str) or not name.isidentifier():
            raise DocumentError(f"parameter name {name!r} must be a valid identifier")
        value = edit.get("value")
        if value is None:
            if name not in self.parameters:
                raise DocumentError(f"no parameter named {name!r} to remove")
            candidate = {key: item for key, item in self.parameters.items() if key != name}
        else:
            candidate = {**self.parameters, name: value}

        # Resolve the whole table, not just this entry: a new value can break
        # another parameter that references it, and every feature expression
        # has to still evaluate afterwards.
        resolved = resolve_parameters(candidate)
        self.parameters = candidate
        for feature in self.features:
            validate_feature(feature, resolved)
        return {"name": name, "value": value}

    def _edit_sketch(self, edit: dict) -> dict:
        feature_id = edit.get("id")
        index = self.index_of(feature_id)
        feature = copy.deepcopy(self.features[index])
        if feature.get("type") != "sketch" or "entities" not in feature:
            raise DocumentError(f"{feature_id!r} is not a sketch of lines, arcs and circles")
        parameters = self.resolved_parameters()
        try:
            detail = edit_sketch(feature, edit, lambda draft: resolve_value(draft, parameters))
        except ValueError as exc:
            raise DocumentError(str(exc)) from None
        validate_feature(feature, parameters)
        self.features[index] = feature
        return {"feature_id": feature_id, "index": index, **detail}

    def _check_references(self, feature: dict, available: list[dict]) -> None:
        """A feature may only reference features that come before it.

        Replay walks the list once and looks each reference up in what it has
        already built, so a forward reference is a `KeyError` deep inside an
        op. Caught here it names both features instead.
        """
        earlier = {item.get("id") for item in available}
        for referenced in _referenced_ids(feature):
            if referenced not in earlier:
                raise DocumentError(
                    f"feature {feature['id']!r} references {referenced!r}, "
                    "which does not come before it"
                )

    def _check_order(self) -> None:
        seen: set = set()
        for feature in self.features:
            for referenced in _referenced_ids(feature):
                if referenced not in seen:
                    raise DocumentError(
                        f"feature {feature.get('id')!r} references {referenced!r}, "
                        "which does not come before it"
                    )
            seen.add(feature.get("id"))


# The fields an op uses to name another feature. One list rather than a
# per-op declaration, because a second place to keep in step with the op
# registry is exactly what broke the BREP cache's op-type map. I wrote this
# tuple with two entries and grep found a third, `tool` on boolean, so the
# coverage check in test_document.py reads the schemas rather than trusting
# this line. Add a field here when an op grows a new reference.
REFERENCE_FIELDS = ("base", "profile", "tool")


def _referenced_ids(feature: dict) -> list[str]:
    return [
        feature[field]
        for field in REFERENCE_FIELDS
        if isinstance(feature.get(field), str)
    ]


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
