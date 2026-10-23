# /// script
# requires-python = ">=3.12"
# dependencies = ["lizard==1.24.1", "vulture==2.16"]
# ///
"""Code metrics for the engine, the viewer and the app, as absolute counts.

    uv run --script tools/metrics.py [--root DIR] [--out metrics.json]
    uv run --script tools/metrics.py report metrics.json [--base base.json]

The numbers are sensors, not targets: they show what a change did, and nothing
here fails a build. They are counts rather than averages or percentages,
because deleting clean code moves a ratio the wrong way. Duplication needs node
(jscpd and eslint come from npx at pinned versions).
"""

import argparse
import ast
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import lizard
import vulture

JSCPD = "jscpd@5.4.0"
ESLINT = "eslint@9.39.5"

# A function over either limit counts once. lizard's own warning limits (CCN 15,
# 1000 lines, 100 parameters) are too loose to ever move on a codebase this size.
# lizard measures Python and Swift; JS comes from ESLint's parser, because lizard
# reads two `<` in one expression as a generic and runs a function on into the
# next one (54 of 988 viewer functions had a wrong span, 31 were missing).
CCN_HIGH = 10
CCN_VERY_HIGH = 15
LONG_NLOC = 50
MANY_PARAMS = 5

PARTS = {
    "engine": {"language": "python", "dirs": ["engine/src"]},
    "viewer": {"language": "javascript", "dirs": ["viewer"]},
    "app": {"language": "swift", "dirs": ["app/Sources"]},
}
SUFFIX = {"python": ".py", "javascript": ".js", "swift": ".swift"}
SKIP_DIRS = {"vendor", "test", "__pycache__", ".build"}

# What vulture cannot see. `@register` puts an op in REGISTRY; build123d reads
# `label` and `color` when it exports; the rest are http.server and socketserver
# hooks, or arguments an override has to accept.
VULTURE_IGNORE_DECORATORS = ["@register"]
VULTURE_IGNORE_NAMES = [
    "label", "color", "do_POST", "log_message", "daemon_threads",
    "request_queue_size", "format", "exception",
]

ROWS = [
    ("functions", "Functions"),
    ("nloc", "Lines of code (NLOC)"),
    ("ccn_over_10", f"Functions with CCN > {CCN_HIGH}"),
    ("ccn_over_15", f"Functions with CCN > {CCN_VERY_HIGH}"),
    ("max_ccn", "Highest CCN"),
    ("long_functions", f"Functions over {LONG_NLOC} NLOC"),
    ("many_params", f"Functions with > {MANY_PARAMS} parameters"),
    ("duplicated_lines", "Duplicated lines"),
    ("clones", "Clones"),
    ("unused_code", "Unused code (vulture)"),
    ("unused_locals", "Unused names inside a file (eslint)"),
    ("unused_exports", "Exports no other file uses"),
    ("test_only_exports", "Exports only tests use"),
]


def source_files(root, part):
    suffix = SUFFIX[part["language"]]
    files = []
    for top in part["dirs"]:
        for dirpath, dirnames, filenames in os.walk(root / top):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
            files += [Path(dirpath) / f for f in sorted(filenames) if f.endswith(suffix)]
    return files


def inline_script(root, scratch):
    """index.html's inline <script> is most of the viewer, so it is measured as a file."""
    html = (root / "viewer/index.html").read_text()
    blocks = re.findall(r"<script>\n(.*?)</script>", html, re.S)
    if not blocks:
        sys.exit("found no inline <script> in viewer/index.html")
    path = scratch / "index-inline.js"
    path.write_text("\n".join(blocks))
    return path


def counts(ccns, nlocs, params, nloc):
    if not ccns:
        sys.exit("found no functions, so the parse failed")
    return {
        "functions": len(ccns),
        "nloc": nloc,
        "ccn_over_10": sum(c > CCN_HIGH for c in ccns),
        "ccn_over_15": sum(c > CCN_VERY_HIGH for c in ccns),
        "max_ccn": max(ccns),
        "long_functions": sum(n > LONG_NLOC for n in nlocs),
        "many_params": sum(p > MANY_PARAMS for p in params),
    }


def swift_for_lizard(text):
    """Swift with the two things lizard misreads blanked out, line for line.

    lizard takes `private(set)` and a `.set(` or `.init(` call for the start of
    an accessor or an initialiser, and the function then runs on to some later
    brace: `super.init()` stretched three inits by up to 106 lines. Each
    replacement keeps the length, so lines and columns stay where they were.
    """
    text = re.sub(r"\b(private|fileprivate|internal|public|package)\(set\)", lambda m: m.group(1) + " " * 5, text)
    return re.sub(r"\.(set|init)\(", lambda m: "." + m.group(1)[:-1] + "_(", text)


def python_ends(text):
    """Each function's last line, keyed by its `def` line and its first decorator's."""
    ends = {}
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            ends[node.lineno] = node.end_lineno
            if node.decorator_list:
                ends[node.decorator_list[0].lineno] = node.end_lineno
    return ends


def swift_end(lines, start):
    """The line where the braces opened at or after `start` close again."""
    depth, opened = 0, False
    for number, line in enumerate(lines[start - 1:], start):
        code = re.sub(r'"(?:\\.|[^"\\])*"', '""', line.split("//")[0])
        for char in code:
            depth += {"{": 1, "}": -1}.get(char, 0)
            opened = opened or char == "{"
            if opened and depth == 0:
                return number
    return None


def complexity_lizard(files, scratch, language):
    """lizard's counts, after checking every span against a second reading.

    A misread span is how lizard goes wrong, and the counts read normal when it
    does, so a span that disagrees stops the run instead of being reported.
    """
    infos, wrong = [], []
    for f in files:
        text = f.read_text()
        path = f
        if language == "swift":
            path = scratch / "swift" / f.name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(swift_for_lizard(text))
        info = lizard.analyze_file(str(path))
        infos.append(info)
        ends = python_ends(text) if language == "python" else None
        lines = text.splitlines()
        for fn in info.function_list:
            end = ends.get(fn.start_line) if ends is not None else swift_end(lines, fn.start_line)
            if end != fn.end_line:
                wrong.append(f"{f.name}:{fn.start_line} {fn.name} ends at {fn.end_line}, not {end}")
    if wrong:
        sys.exit("lizard misread these spans:\n" + "\n".join(wrong))
    functions = [fn for info in infos for fn in info.function_list]
    return counts(
        [fn.cyclomatic_complexity for fn in functions],
        [fn.nloc for fn in functions],
        [fn.parameter_count for fn in functions],
        sum(info.nloc for info in infos),
    )


def complexity_javascript(messages):
    """ESLint's own counts, one message per function and rule at a limit of 0.

    `complexity` counts optional chaining and default values too, so it reads a
    little higher than lizard's CCN. `max-lines-per-function` counts the lines
    of nested functions as well, and skips an IIFE, so a module's wrapper does
    not count as one long function.
    """
    def numbers(rule, pattern):
        return [int(re.search(pattern, m["message"]).group(1)) for m in messages if m["ruleId"] == rule]

    return counts(
        numbers("complexity", r"complexity of (\d+)"),
        numbers("max-lines-per-function", r"too many lines \((\d+)\)"),
        numbers("max-params", r"too many parameters \((\d+)\)"),
        sum(numbers("max-lines", r"too many lines \((\d+)\)")),
    )


def duplication(files, scratch):
    """jscpd over one part's files, so a clone never spans two languages."""
    out = scratch / "jscpd-report"
    subprocess.run(
        ["npx", "--yes", JSCPD, "--silent", "--reporters", "json", "--output", str(out),
         "--min-tokens", "50", *map(str, files)],
        check=True, stdout=subprocess.DEVNULL,
    )
    total = json.loads((out / "jscpd-report.json").read_text())["statistics"]["total"]
    if not total["sources"]:
        sys.exit(f"jscpd read none of {len(files)} files")
    return {"duplicated_lines": total["duplicatedLines"], "clones": total["clones"]}


def unused_python(files):
    scan = vulture.Vulture(ignore_names=VULTURE_IGNORE_NAMES, ignore_decorators=VULTURE_IGNORE_DECORATORS)
    scan.scavenge([str(f) for f in files])
    return {"unused_code": len(scan.get_unused_code(min_confidence=60))}


def eslint(root, path):
    """The rules metrics reads, over one file read from stdin.

    Every count rule runs at a limit of 0, so it reports every function (or the
    file) with its number.

    The inline script's copy lives outside the checkout, and eslint passes over
    a file outside its base path with exit 0, so every file goes through stdin
    under a name inside the checkout. A skipped file stops the run.
    """
    result = subprocess.run(
        ["npx", "--yes", ESLINT, "--no-config-lookup", "--format", "json",
         "--parser-options", "sourceType:script", "--parser-options", "ecmaVersion:latest",
         "--rule", "no-unused-vars: [error, {args: none, caughtErrors: none, ignoreRestSiblings: true}]",
         "--rule", "complexity: [error, 0]",
         "--rule", "max-params: [error, 0]",
         "--rule", "max-lines-per-function: [error, {max: 0, skipBlankLines: true, skipComments: true}]",
         "--rule", "max-lines: [error, {max: 0, skipBlankLines: true, skipComments: true}]",
         "--stdin", "--stdin-filename", f"viewer/{path.name}"],
        input=path.read_text(), capture_output=True, text=True, cwd=root,
    )
    if result.returncode not in (0, 1):
        sys.exit(f"eslint failed on {path.name}:\n{result.stderr}")
    reports = json.loads(result.stdout)
    if not reports or any(m.get("fatal") or not m.get("ruleId") for r in reports for m in r["messages"]):
        sys.exit(f"eslint did not lint {path.name}:\n{result.stdout}")
    return [m for r in reports for m in r["messages"]]


def unused_javascript(root, files, messages):
    """Unused names inside each file (eslint), and `api` keys no other file reads.

    Every viewer module ends `const api = { ... }` and hands it to `window` or
    `module.exports`, so its keys are its whole public surface.
    """
    unused_locals = sum(m["ruleId"] == "no-unused-vars" for m in messages)

    texts = {f: f.read_text() for f in files}
    tests = [f.read_text() for f in sorted((root / "viewer/test").glob("*.js"))]
    unused_exports = test_only = 0
    for f, text in texts.items():
        match = re.search(r"const api = \{(.*?)\};", text, re.S)
        if not match:
            continue
        keys = [entry.split(":")[0].strip() for entry in match.group(1).split(",")]
        for key in filter(None, keys):
            word = re.compile(rf"\b{re.escape(key)}\b")
            if any(word.search(other) for g, other in texts.items() if g != f):
                continue
            if any(word.search(t) for t in tests):
                test_only += 1
            else:
                unused_exports += 1
    return {"unused_locals": unused_locals, "unused_exports": unused_exports, "test_only_exports": test_only}


def measure(root):
    metrics = {}
    with tempfile.TemporaryDirectory() as tmp:
        scratch = Path(tmp)
        for name, part in PARTS.items():
            files = source_files(root, part)
            if name == "viewer":
                files.append(inline_script(root, scratch))
            language = part["language"]
            if language == "javascript":
                messages = [m for f in files for m in eslint(root, f)]
                numbers = complexity_javascript(messages) | unused_javascript(root, files, messages)
            else:
                numbers = complexity_lizard(files, scratch, language)
            numbers |= duplication(files, scratch / name)
            if language == "python":
                numbers |= unused_python(files)
            metrics[name] = {"language": part["language"], "files": len(files), **numbers}
    return metrics


def report(head, base=None):
    names = list(head)
    lines = [
        "| Metric | " + " | ".join(f"{n} ({head[n]['language']})" for n in names) + " |",
        "|---" * (len(names) + 1) + "|",
    ]
    for key, label in ROWS:
        cells = []
        for n in names:
            value = head[n].get(key)
            if value is None:
                cells.append("–")
                continue
            before = (base or {}).get(n, {}).get(key)
            if before is None or before == value:
                cells.append(str(value))
            else:
                cells.append(f"{value} ({value - before:+d})")
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    if base:
        moved = sum(
            head[n].get(key) != base.get(n, {}).get(key)
            for n in names for key, _ in ROWS if head[n].get(key) is not None
        )
        note = f"{moved} numbers moved against the base, shown in brackets. " if moved else "No number moved against the base. "
    else:
        note = ""
    lines.append("")
    lines.append(
        f"{note}Counts, not targets: a number that moves the wrong way gets a sentence "
        "in the PR, not a refactor. The viewer includes index.html's inline script."
    )
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    show = sub.add_parser("report", help="print a markdown table from a metrics.json")
    show.add_argument("head")
    show.add_argument("--base")
    parser.add_argument("--root", default=".", help="the checkout to measure")
    parser.add_argument("--out", default="metrics.json")
    args = parser.parse_args()

    if args.command == "report":
        head = json.loads(Path(args.head).read_text())
        base = json.loads(Path(args.base).read_text()) if args.base else None
        print(report(head, base))
        return

    metrics = measure(Path(args.root).resolve())
    Path(args.out).write_text(json.dumps(metrics, indent=2) + "\n")
    print(report(metrics))


if __name__ == "__main__":
    main()
