"""Golden feature tests, in the three layers SPEC.md sets out.

Each tests/golden/<case>/ holds a document.json, a checks.json of independently
hand-computed values, and captured expected.step and expected.stl. Read the
case's README.md for the derivation of every number in checks.json.

The layers are ordered by how much they survive. The fingerprint survives
harmless changes and catches real ones. Geometric equality catches a shape
change the fingerprint could miss. Byte identity catches everything and will
also fail, legitimately, the day OCCT is upgraded, which is why it is the last
layer and not the only one.

Only checks.json proves the engine correct, because only checks.json was
written before the code and by hand. The expected.* files are captured from the
engine, so they prove "same as before" and nothing more. That is what makes
them worth locking: see tests/golden/README.md.
"""

from pathlib import Path

import pytest
from build123d import import_step
from golden_support import (
    CAPTURE_STAMP,
    CASES,
    EXPECTED_STEP,
    EXPECTED_STL,
    build,
    capture_identity,
    case_dir,
    checks,
    document,
    engine_identity,
    face_type_histogram,
    normalised_step,
    write_step,
    write_stl,
)


@pytest.fixture(scope="module")
def parts(tmp_path_factory):
    """One replay per case for all three layers. Replaying six documents three
    times over costs more than the tests do.
    """
    cache_root = tmp_path_factory.mktemp("golden-cache")
    return {case: build(case, cache_root / case) for case in CASES}


@pytest.mark.parametrize("case", CASES)
def test_fingerprint_matches_the_hand_computed_checks(case, parts):
    part = parts[case]
    expected = checks(case)
    bbox = part.bounding_box()

    assert part.volume == pytest.approx(expected["volume"], rel=1e-6)
    assert part.area == pytest.approx(expected["surface_area"], rel=1e-6)
    assert list(bbox.min) == pytest.approx(expected["bounding_box"]["min"], abs=1e-6)
    assert list(bbox.max) == pytest.approx(expected["bounding_box"]["max"], abs=1e-6)
    assert list(part.center()) == pytest.approx(expected["center_of_mass"], abs=1e-6)
    assert len(part.solids()) == expected["solids"]
    assert len(part.faces()) == expected["faces"]
    assert len(part.edges()) == expected["edges"]
    assert len(part.vertices()) == expected["vertices"]
    assert face_type_histogram(part) == expected["face_types"]


@pytest.mark.parametrize("case", CASES)
def test_geometry_equals_the_expected_step(case, parts):
    """Symmetric difference, both ways round. A one-way cut passes when the
    result is a subset of what it should be: cutting a smaller solid out of the
    expected one leaves nothing, and a missing feature would read as correct.
    """
    part = parts[case]
    expected = import_step(case_dir(case) / EXPECTED_STEP)

    for name, left, right in (
        ("replayed minus expected", part, expected),
        ("expected minus replayed", expected, part),
    ):
        residue = left.cut(right)
        volume = 0.0 if residue is None else residue.volume
        # Measured: an identical pair gives exactly 0.0, so this leaves room for
        # a sliver without leaving room for a real difference. The smallest
        # feature in the suite is a 1000 mm^3 notch.
        assert volume == pytest.approx(0.0, abs=1e-6), (
            f"{case}: {name} left {volume} mm^3"
        )


@pytest.mark.parametrize("case", CASES)
def test_exported_bytes_are_unchanged(case, parts, tmp_path):
    """The brittle layer, and deliberately so. An OCCT or build123d upgrade will
    fail this while the two layers above still pass, and that combination is the
    signal to review the upgrade rather than a defect.

    STEP is compared with its FILE_NAME timestamp scrubbed, which is the only
    part of the file that varies between two exports of one solid.

    It only runs on the engine that captured the files, and that is measured, not
    cautious: CI failed five of these six cases on Linux x86_64 against files
    captured on macOS arm64, with the library versions identical, while layers 1
    and 2 passed on all six. See engine_identity() for why a version pin cannot
    cover it. Skipping is the honest outcome, because the layer claims byte
    identity within one compiled engine and nothing wider.
    """
    recorded = capture_identity(case)
    current = engine_identity()
    if recorded != current:
        pytest.skip(f"{case} was captured by {recorded}, this is {current}")

    part = parts[case]
    fresh_step = tmp_path / "fresh.step"
    fresh_stl = tmp_path / "fresh.stl"

    write_step(part, fresh_step)
    write_stl(part, fresh_stl)

    assert normalised_step(fresh_step) == normalised_step(
        case_dir(case) / EXPECTED_STEP
    ), f"{case}: STEP changed beyond its timestamp"
    assert fresh_stl.read_bytes() == (case_dir(case) / EXPECTED_STL).read_bytes(), (
        f"{case}: STL changed"
    )


@pytest.mark.parametrize("case", CASES)
def test_every_case_ships_its_expected_files(case):
    """A case whose expected.* files are missing would otherwise skip two whole
    layers by erroring in a way that looks like a setup problem.

    The stamp is checked here too. Without it the byte layer cannot tell "this is
    a different engine" from "nobody recorded which engine", and both would read
    as a skip, so a case could lose a layer in silence.
    """
    for name in (EXPECTED_STEP, EXPECTED_STL, CAPTURE_STAMP):
        assert (case_dir(case) / name).is_file(), (
            f"{case} has no {name}, run make golden-update CASE={case}"
        )

    assert set(capture_identity(case)) == set(engine_identity()), (
        f"{case}'s {CAPTURE_STAMP} does not describe the same fields as engine_identity()"
    )


def test_replay_is_deterministic(tmp_path: Path):
    """Two replays through one cache directory, so a cache hit has to give the
    same answer as the miss that filled it.
    """
    from sprocket_engine.replay import replay

    doc = document("rect_extrude")
    first = replay(doc, cache_dir=tmp_path / "cache")
    second = replay(doc, cache_dir=tmp_path / "cache")
    assert first.volume == pytest.approx(second.volume, rel=1e-9)


def test_the_timestamp_scrub_is_narrow_enough_and_wide_enough(tmp_path):
    """The byte layer above can pass by luck. Capture a case and run the suite in
    the same second and the raw STEP files match, so the scrub never runs. This
    tests the substitution itself, whatever the clock says.

    The second half matters as much as the first: a scrub broad enough to hide a
    moved vertex would make the whole layer worthless.
    """
    header = b"FILE_NAME('Open CASCADE Shape Model','%s',('Author'),(\n"
    point = b"#42=CARTESIAN_POINT('',(%s,-10.,0.));\n"

    morning = tmp_path / "morning.step"
    evening = tmp_path / "evening.step"
    morning.write_bytes(header % b"2026-09-28T07:43:02" + point % b"-20.")
    evening.write_bytes(header % b"2026-09-28T07:43:25" + point % b"-20.")
    assert morning.read_bytes() != evening.read_bytes(), "the fixture does not differ"
    assert normalised_step(morning) == normalised_step(evening)

    moved = tmp_path / "moved.step"
    moved.write_bytes(header % b"2026-09-28T07:43:02" + point % b"-20.5")
    assert normalised_step(moved) != normalised_step(morning), (
        "the scrub hides a moved vertex"
    )
