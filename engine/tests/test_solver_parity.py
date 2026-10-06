"""The engine's solver and the viewer's must give the same answers.

The viewer solves in the browser (planegcs as WebAssembly, viewer/sketch-solver.js)
and the engine solves in Python (planegcs on PyPI, sprocket_engine/solver.py).
Both are FreeCAD's PlaneGCS, and nothing says they are the same build, so this
runs the same sketches through both and compares. If a dependency bump moves one
of them, this is where it shows.

Needs the `solver` extra and node. Without either the tests skip.
"""

import json
import math
import shutil
import subprocess
from pathlib import Path

import pytest

pytest.importorskip("planegcs", reason="install the `solver` extra")

from solver_cases import CASES, rough_rectangle  # noqa: E402
from sprocket_engine.solver import POINTS, point_of, solve  # noqa: E402

NODE = shutil.which("node")
RUNNER = Path(__file__).with_name("solver_parity.js")
# A fully constrained sketch has one answer. The solvers stop a hair short of it, by
# different amounts, so allow a millionth of a millimetre.
TOLERANCE = 1e-6

needs_node = pytest.mark.skipif(NODE is None, reason="needs node to run the viewer's solver")

SOLVE_AND_DRAG = [(name, "solve") for name in CASES] + [(name, "drag") for name, case in CASES.items() if case["drag"]]


@pytest.fixture(scope="module")
def viewer_answers(tmp_path_factory):
    cases = tmp_path_factory.mktemp("solver") / "cases.json"
    cases.write_text(json.dumps(CASES))
    done = subprocess.run([NODE, str(RUNNER), str(cases)], capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr
    marked = next(line for line in done.stdout.splitlines() if line.startswith("ANSWERS "))
    return json.loads(marked[len("ANSWERS "):])


def numbers(sketch):
    """Every number that describes the sketch, by name. An arc is compared by its
    ends, not its angles, because two angles a turn apart draw the same arc."""
    described = {}
    for entity in sketch["entities"]:
        for name in POINTS[entity["kind"]]:
            described[f"{entity['id']}.{name}"] = point_of(sketch, f"{entity['id']}.{name}")
        if entity["kind"] != "line":
            described[f"{entity['id']}.r"] = [entity["r"]]
    return described


@needs_node
@pytest.mark.parametrize("name, mode", SOLVE_AND_DRAG)
def test_the_engine_and_the_viewer_solve_a_sketch_the_same_way(viewer_answers, name, mode):
    case = CASES[name]
    ours = solve(case["sketch"], case["drag"] if mode == "drag" else None)
    theirs = viewer_answers[name][mode]

    assert ours["ok"] == theirs["ok"]
    assert ours["conflicting"] == theirs["conflicting"]
    if not ours["ok"]:
        return
    assert ours["dof"] == theirs["dof"]
    assert ours["redundant"] == theirs["redundant"]
    if ours["dof"] != 0:
        return
    mine, yours = numbers(ours["sketch"]), numbers(theirs["sketch"])
    assert mine.keys() == yours.keys()
    worst = max(abs(a - b) for key in mine for a, b in zip(mine[key], yours[key]))
    assert worst <= TOLERANCE, f"the two solvers differ by {worst:.2e}"


def test_a_dimensioned_rectangle_comes_out_the_size_it_was_given():
    """Not a comparison: the numbers the dimensions ask for, so a pair of solvers
    that agree on a wrong answer would still fail."""
    sketch = rough_rectangle([
        {"kind": "distance", "a": "e1.p0", "b": "e1.p1", "value": 40},
        {"kind": "distance", "a": "e2.p0", "b": "e2.p1", "value": 20},
        {"kind": "coincident", "a": "e1.p0", "b": "origin"},
    ])
    result = solve(sketch)
    assert result["ok"] and result["dof"] == 0
    corners = {entity["id"]: entity for entity in result["sketch"]["entities"]}
    assert corners["e1"]["p0"] == pytest.approx([0, 0], abs=1e-9)
    assert corners["e1"]["p1"] == pytest.approx([40, 0], abs=1e-9)
    assert corners["e3"]["p0"] == pytest.approx([40, 20], abs=1e-9)
    assert math.dist(corners["e2"]["p0"], corners["e2"]["p1"]) == pytest.approx(20, abs=1e-9)


def test_two_dimensions_that_disagree_are_named():
    sketch = rough_rectangle([
        {"kind": "distance", "a": "e1.p0", "b": "e1.p1", "value": 40},
        {"kind": "distance", "a": "e3.p0", "b": "e3.p1", "value": 30},
    ])
    result = solve(sketch)
    assert not result["ok"]
    assert {8, 9} <= set(result["conflicting"]), "the two distance constraints are indices 8 and 9"
    assert result["sketch"] is sketch, "a refused sketch comes back untouched"


def test_arcs_add_no_hidden_redundant_constraints():
    """Duplicate arc rules made every arc redundant. Nothing reported it, but each
    diagnosis then ran planegcs's slow redundancy search (24 ms on this plate, 14 without)."""
    from solver_cases import sprocket_plate
    from sprocket_engine.solver import _Build

    diagnosis = _Build(sprocket_plate(), None).system.diagnose()
    assert list(diagnosis.redundant) == []
