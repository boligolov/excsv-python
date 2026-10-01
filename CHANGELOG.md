# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html). While the version is `0.x`, a minor release may contain breaking changes; they are listed under **Changed**.

The version tracks the ExCSV spec: `MAJOR.MINOR` is the spec version the release implements, and `PATCH` counts library releases against that spec version (fixes and API additions that need no spec change). Release tags are `vMAJOR.MINOR.PATCH`.

## [Unreleased]

## [0.6.0] - 2026-10-01

Implements ExCSV **v0.6** ([boligolov/excsv@93d4625](https://github.com/boligolov/excsv/commit/93d4625)).

### Added

- **Notes and links (v0.6).** `#note` on a cell, row, column or table; `link=` URL templates on `#column`; `#link` on a single cell. Rows are addressed by position (`row=`) or by the id column's value (`key=`).
  - `Document.resolve_notes()` / `Document.resolve_links()` resolve addresses against the data; `LinkSet.cell_link(row, col)` returns the final URL.
  - Template substitution with UTF-8 percent-encoding (a single-placeholder template is used verbatim), and the `http`/`https`/`mailto` scheme check on the final URL.
  - Editing: `Document.add_note`, `remove_note`, `set_link`, `remove_link`, `row_anchor`, `resolve_column`. Adding a note or link raises `version=` to `0.6`.
  - `is_safe_link_scheme()`, `percent_encode_link_value()`, `Note`, `Link`, `NoteTarget`, `ResolvedNote`, `LinkSet`.
- **Charts.** `#chart` (compact form) and `#chart-<engine>:` (escape hatch) are parsed, validated and preserved. `Document.chart_by_name`, `add_chart`, `remove_chart`; `Chart.column_refs()`.
- **Column rename.** `Document.rename_column(old, new)` updates the header cell, `formula=`, `#chart` channels, `#note`/`#link` `col=` and `link=` placeholders, and reports `#$` SQL and `#chart-vega` payloads it cannot rewrite. `Pack.rename_column_fks` updates `#fk`.
- JSON form: `charts`, `notes`, `links` arrays, `link` on column objects, and `column_count` from `columns=`.
- ZIP comment follows the spec's priority order (`#chart` after `#$dql`, `#note`/`#link` last) instead of file order.
- Error codes: the 9 `chart_*` and 15 `note_*`/`link_*` codes, plus `header_missing_rows`, `columns_mismatch`, `sql_dialect_family`, `sql_version_mismatch`, `sql_no_match`, `ddl_column_mismatch`. `is_fail_kind()` tells FAIL from WARN for declaration checks.
- Version helpers `is_known_version()` and `compare_versions()`; `excsv.__version__`.
- The package version now follows the spec version (`0.6.x` implements ExCSV v0.6).
- Test that `ErrorKind` covers every code in the fixture manifest's `error_kinds`.
- Packaging: complete project metadata, `CHANGELOG.md`, `CONTRIBUTING.md`, `.gitattributes`, `test`/`dev` extras, Ruff configuration; CI runs lint, tests on Python 3.10–3.13, and builds and checks the distributions.

### Changed

- **Version compatibility.** Any earlier `version=` is read without `unknown_version`; only a newer or unparseable version warns. New documents declare `0.6`.
- **Stricter reads (spec conformance).** `rows=` missing from a v0.5+ header is `header_missing_rows` (FAIL under `strict_options()`, WARN otherwise). `formula=` errors (`formula_unknown_reference`, `formula_references_computed`, `formula_parse_error`) and `computed_materialized_mismatch` now fail at parse time instead of only being reported by `validate()`.
- `Document.sort_rows` keeps `#note`/`#link` `row=` anchors on the same rows.
- A pack's manifest-level warnings (e.g. `note_on_manifest`) are returned in `ParseResult.warnings` instead of being dropped.
- `columns=` on a plain file is checked against the physical width (`columns_mismatch`).
- The `dev` extra now holds the development tooling; use `pip install -e ".[test]"` for the test dependencies only.

### Fixed

- `scripts/sync_upstream.py` refreshed a cached upstream clone only on first use, so zip/pack fixtures could go stale while plain fixtures were current. It now fetches and resets the clone on every run, and checks it out with `core.autocrlf=false` so byte-exact fixtures survive on Windows.
- `scripts/sync_upstream.py` now also downloads `charts.md`, `notes.md`, `error-handling.md` and `CHANGELOG.md`.
- The fixture manifest loader reads the two-document YAML layout (`---` separator).
- The formula lexer no longer treats non-ASCII digits as identifier characters.
- Removed the skips for five upstream fixtures that upstream has since fixed.

## [0.5.0] - 2026-09-06

Initial release (published with package metadata `0.1.0`, before the version was aligned with the spec): Python port of [excsv-golang](https://github.com/boligolov/excsv-golang) implementing ExCSV **v0.5** -- plain, sidecar, row ZIP and pack containers, JSON and CSVW export, computed columns, validate and fix.

[Unreleased]: https://github.com/boligolov/excsv-python/compare/v0.6.0...HEAD
[0.6.0]: https://github.com/boligolov/excsv-python/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/boligolov/excsv-python/releases/tag/v0.5.0
