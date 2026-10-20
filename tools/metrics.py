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


def complexity(files):
    functions = [fn for f in files for fn in lizard.analyze_file(str(f)).function_list]
    if not functions:
        sys.exit(f"lizard found no functions in {len(files)} files, so the parse failed")
    return {
        "functions": len(functions),
        "nloc": sum(lizard.analyze_file(str(f)).nloc for f in files),
        "ccn_over_10": sum(fn.cyclomatic_complexity > CCN_HIGH for fn in functions),
        "ccn_over_15": sum(fn.cyclomatic_complexity > CCN_VERY_HIGH for fn in functions),
        "max_ccn": max(fn.cyclomatic_complexity for fn in functions),
        "long_functions": sum(fn.nloc > LONG_NLOC for fn in functions),
        "many_params": sum(fn.parameter_count > MANY_PARAMS for fn in functions),
    }


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
    """no-unused-vars over one file, read from stdin.

    The inline script's copy lives outside the checkout, and eslint passes over
    a file outside its base path with exit 0, so every file goes through stdin
    under a name inside the checkout. A skipped file stops the run.
    """
    result = subprocess.run(
        ["npx", "--yes", ESLINT, "--no-config-lookup", "--format", "json",
         "--parser-options", "sourceType:script", "--parser-options", "ecmaVersion:latest",
         "--rule", "no-unused-vars: [error, {args: none, caughtErrors: none, ignoreRestSiblings: true}]",
         "--stdin", "--stdin-filename", f"viewer/{path.name}"],
        input=path.read_text(), capture_output=True, text=True, cwd=root,
    )
    if result.returncode not in (0, 1):
        sys.exit(f"eslint failed on {path.name}:\n{result.stderr}")
    reports = json.loads(result.stdout)
    if not reports or any(m.get("fatal") or not m.get("ruleId") for r in reports for m in r["messages"]):
        sys.exit(f"eslint did not lint {path.name}:\n{result.stdout}")
    return reports


def unused_javascript(root, files):
    """Unused names inside each file (eslint), and `api` keys no other file reads.

    Every viewer module ends `const api = { ... }` and hands it to `window` or
    `module.exports`, so its keys are its whole public surface.
    """
    unused_locals = sum(len(r["messages"]) for f in files for r in eslint(root, f))

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
            numbers = complexity(files)
            numbers |= duplication(files, scratch / name)
            if part["language"] == "python":
                numbers |= unused_python(files)
            if part["language"] == "javascript":
                numbers |= unused_javascript(root, files)
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
    note = "Change against the base branch in brackets. " if base else ""
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
