from pathlib import Path

import pytest

import excsv

from .fixtures_manifest import UPSTREAM_FIXTURE_BUGS, assert_expectation, filter_rf, load_manifest, root_dir

MANIFEST_PATH = Path(__file__).parent.parent / "test" / "fixtures" / "fixtures.yaml"


def _fixture_ids() -> list[str]:
    if not MANIFEST_PATH.exists():
        return []
    manifest = load_manifest(MANIFEST_PATH)
    return [fx["id"] for fx in filter_rf(manifest)]


def _fixtures_by_id() -> dict[str, dict]:
    manifest = load_manifest(MANIFEST_PATH)
    return {fx["id"]: fx for fx in filter_rf(manifest)}


_IDS = _fixture_ids()


@pytest.mark.skipif(not MANIFEST_PATH.exists(), reason="fixtures.yaml not synced -- run scripts/sync_upstream.py")
def test_error_registry_matches_manifest() -> None:
    """Every code in the manifest's error_kinds (which MUST match the spec's
    error-handling.md) is an ErrorKind member."""
    manifest = load_manifest(MANIFEST_PATH)
    known = {k.value for k in excsv.ErrorKind}
    missing = sorted(set(manifest["error_kinds"]) - known)
    assert not missing, f"ErrorKind lacks spec codes: {missing}"


@pytest.mark.skipif(not MANIFEST_PATH.exists(), reason="fixtures.yaml not synced -- run scripts/sync_upstream.py")
@pytest.mark.parametrize("fixture_id", _IDS)
def test_manifest_fixture(fixture_id: str) -> None:
    fx = _fixtures_by_id()[fixture_id]
    if fixture_id in UPSTREAM_FIXTURE_BUGS:
        pytest.skip(UPSTREAM_FIXTURE_BUGS[fixture_id])

    path = root_dir(MANIFEST_PATH) / fixture_id
    if not path.exists():
        pytest.skip(f"fixture not on disk (run sync_upstream.py): {path}")

    opts = excsv.strict_options()
    opts.expect_profile = (fx.get("expect") or {}).get("profile", "")

    err = None
    res = None
    try:
        res = excsv.parse_file(str(path), opts)
    except Exception as e:  # noqa: BLE001 - the manifest asserts on the exact exception
        err = e

    assert_expectation(fx, res, err)
