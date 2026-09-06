# excsv-python

**Website:** [excsv.org](https://excsv.org)

Python reference implementation of the **excsv** library for [ExCSV](https://github.com/boligolov/excsv) v0.5 (Extended CSV) -- CSV that describes itself. A conforming document is still plain, delimiter-separated data; schema, units, summary statistics, SQL DDL/DQL, and an integrity checksum ride along as `#` comment lines that any existing CSV reader already skips.

> **Alpha.** Pre-1.0 and may change without notice. Pin a version in `pyproject.toml`/`requirements.txt` and check release notes before upgrading.

This is a **library only** -- there is no CLI. It is a companion port of [excsv-golang](https://github.com/boligolov/excsv-golang), the Go reference implementation, mirroring its module layout, error taxonomy, and fixture-driven test strategy.

Supports **plain** (`.excsv`, `.ecsv`, `.extsv` sidecars), **JSON** (`.excsv.json`), **row ZIP** (`.excsv.zip`, `.ecsv.zip`), and **pack** (`.excsv.pack.zip`).

## Install

```bash
pip install -e .            # core library, stdlib only
pip install -e ".[crypto]"  # + AES-256 zip password support (pyzipper)
pip install -e ".[dev]"     # + pytest, PyYAML for running the test suite
```

Requires Python 3.10+.

## Quick start

```python
import excsv

res = excsv.parse_file("data.excsv", excsv.strict_options())
doc = res.doc                      # excsv.Document
print(doc.row_count(), doc.header.delim_name)
```

```python
try:
    res = excsv.parse_file("data.excsv", excsv.strict_options())
except excsv.ParseError as e:
    # e.issue.kind is one of the excsv.ErrorKind members (error registry)
    print(e.issue.kind, e.issue.message)
```

Opening `sales.csv` with a sibling `sales.excsv` auto-discovers and loads the sidecar. Set `ParseOptions.expect_profile` (`"stub"` / `"sidecar"` / `"sidecar_strict"`) when you need sidecar-specific errors (e.g. a missing `reference=`).

### Convert CSV/TSV to ExCSV

```python
with open("data.csv", "rb") as f:
    raw = f.read()
result = excsv.import_delimited(raw, excsv.ImportOptions(source_path="data.csv"))
with open("data.excsv", "wb") as f:
    f.write(result.doc.serialize_canonical())
```

### Validate and repair

```python
report = doc.validate(excsv.ValidateOptions(with_data=True))
for finding in report.findings:
    print(finding.issue.kind, finding.issue.message, "-> fix", finding.repair)

fix_report = doc.fix(excsv.FixOptions())   # repairs derived metadata in place
```

### Computed columns (formula=)

```python
doc.materialize_column("total")     # writes formula output into the data
doc.dematerialize_column("total")   # drops the cached values, keeps formula=
```

### Export

```python
json_result = doc.export_json()          # .excsv.json, a lossless bijection
                                          # except `##` human comments (see .dropped)
csvw_result = doc.export_csvw(excsv.CSVWExportOptions(url="data.csv"))
```

### Row ZIP and pack containers

```python
zip_bytes = excsv.wrap_zip(doc.serialize_canonical(), "data.excsv", comment="")
res = excsv.parse_path("data.excsv.zip", zip_bytes, excsv.strict_options())

pack = excsv.pack_from_document(doc, "people")
pack_bytes = pack.serialize()
res = excsv.parse_path("people.excsv.pack.zip", pack_bytes, excsv.strict_options())
table = res.pack.default_table()
```

AES-256 zip passwords need the optional `pyzipper` dependency (`pip install excsv[crypto]`); everything else is stdlib only.

## Package layout

```
src/excsv/            Core parser, serializer, validator, formula engine
src/excsv/zip/        Row ZIP container (stdlib zipfile + optional AES-256 password)
test/fixtures/        Fixture corpus (gitignored; sync from upstream, see below)
docs/downloaded/      Spec hub + guide/ + implementation/ (gitignored; sync from upstream)
scripts/              sync_upstream.py
```

Internally the package is organized the same way as upstream's Go implementation (one module per concern: parsing, dialect resolution, checksums, aggregations, formula language, pack/zip containers, ...) so the two stay easy to cross-reference.

## Test fixtures

The fixture corpus lives under `test/fixtures/` and is **not committed** to this repo (see `.gitignore`). Tests expect it to be present.

**Authority:** [boligolov/excsv](https://github.com/boligolov/excsv) -- manifest at `fixtures/fixtures.yaml`, files under `fixtures/plain/`, and `fixtures/zip/` + `fixtures/pack/`.

### Download

```bash
python scripts/sync_upstream.py              # specs + manifest + every fixture file
python scripts/sync_upstream.py --specs-only  # docs/downloaded/ + fixtures.yaml only
python scripts/sync_upstream.py --fixtures-only  # fixture bytes only (needs fixtures.yaml already)
```

After sync you should have (among others):

```
test/fixtures/fixtures.yaml
test/fixtures/plain/valid/*.excsv
test/fixtures/plain/invalid/*.excsv
test/fixtures/zip/valid/*.excsv.zip
test/fixtures/zip/invalid/*.excsv.zip
test/fixtures/pack/valid/*.excsv.pack.zip
test/fixtures/pack/invalid/*.excsv.pack.zip
```

Sidecar pairs also pull sibling `.csv` / `.tsv` files listed as `data_sibling` in the manifest.

More detail: [`docs/sources_and_specifications.md`](docs/sources_and_specifications.md).

## Testing

```bash
python scripts/sync_upstream.py   # once, to populate test/fixtures/
pytest
```

`tests/test_manifest_fixtures.py` walks `fixtures.yaml` and drives one test case per fixture id -- same expectations as upstream (parse ok/fail, error kinds, sidecar profiles, pack table shape). A fixture missing from disk is skipped, not failed, so a partial sync still runs the rest of the suite. Hand-written tests cover the pieces the manifest doesn't reach (sort/append, aggregations, the formula engine, computed columns, repair).

## Continuous integration

`.github/workflows/ci.yml`: checkout -> setup Python -> clone upstream fixtures -> `pytest`, on every push/PR to `main`.

## Status

| Area | Scope | Status |
| --- | --- | --- |
| Plain `.excsv` | parse, convert, data, schema, sidecar | Done |
| Row `.excsv.zip` | read/write, password (via `pyzipper`) | Done |
| Pack `.excsv.pack.zip` | multi-table, sectioned columns, FKs | Done |
| JSON / CSVW export | `.excsv.json` bijection, CSVW sidecar | Done |
| Computed columns (v0.5) | `formula=`/`materialized=`, materialize/dematerialize | Done |
| Validate / Fix | full conformance report, derived-metadata repair | Done |
| Streaming, diff, DDL generation | -- | Not yet |

## License

See [LICENSE](LICENSE).
