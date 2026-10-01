# Contributing

## Setup

```bash
python -m venv .venv
.venv/bin/pip install -e ".[dev]"      # Windows: .venv\Scripts\pip
python scripts/sync_upstream.py         # spec snapshots + fixture corpus (both gitignored)
pytest
```

## Ground rules

- **Spec first.** Behaviour comes from the normative spec in [boligolov/excsv](https://github.com/boligolov/excsv) (`docs/implementation/`); see [docs/sources_and_specifications.md](docs/sources_and_specifications.md). If the spec does not define something, do not invent it.
- **Mirror the Go port.** This package tracks [excsv-golang](https://github.com/boligolov/excsv-golang): same module split, error taxonomy and fixture expectations. A change in one port usually has a counterpart in the other.
- **Fixtures drive the tests.** `tests/test_manifest_fixtures.py` runs every entry in the upstream `fixtures.yaml`. Add hand-written tests for API-level behaviour the corpus does not reach.
- **Stdlib only** in the core package; optional features go behind an extra (like `crypto`).
- Keep `ruff check src tests scripts` and `pytest` green.

## Updating to a new spec version

1. `python scripts/sync_upstream.py` and read upstream `CHANGELOG.md` and the diff of `docs/downloaded/implementation/`.
2. Add new error codes to `ErrorKind` (`src/excsv/_errors.py`); `test_error_registry_matches_manifest` fails until the registry matches the manifest.
3. Implement the change, bump `CURRENT_VERSION` and `IMPLEMENTED_VERSIONS` (and `__version__` to `<spec>.0`), extend the fixture assertions in `tests/fixtures_manifest.py` for any new `expect:` keys.
4. Update `README.md` and `CHANGELOG.md`.

## Releasing

1. Move the `[Unreleased]` entries in `CHANGELOG.md` under a new version heading with the date.
2. Bump `__version__` in `src/excsv/__init__.py` (the single source of the package version). `MAJOR.MINOR` is the ExCSV spec version the release implements; bump `PATCH` for a release that needs no spec change, and reset it to `0` when moving to a new spec version.
3. `python -m build && twine check --strict dist/*`
4. Commit, then tag `vX.Y.Z` (e.g. `v0.6.0` for the first release implementing spec v0.6) and push the tag.
