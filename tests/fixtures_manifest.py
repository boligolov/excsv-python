"""Python port of excsv-golang's internal/fixtures/manifest.go: loads
test/fixtures/fixtures.yaml and asserts a ParseResult/error against one
fixture's `expect:` block.

The manifest is a two-document YAML stream: an `error_kinds:` mapping, `---`,
then the flat list of fixture entries (plan/02-fixtures.md). Older snapshots
had no `---` separator; the loader still accepts that layout.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

import excsv


def _fix_null_keys(obj: Any) -> Any:
    """YAML 1.1 resolves a bare `null` scalar to None -- including as a
    mapping key (`header: {null: NA}`). Go's yaml.v3 keeps it as the literal
    string "null" when decoding into map[string]string; PyYAML does not, so
    normalize None keys back to "null" (the only header/meta key this can
    ever collide with)."""
    if isinstance(obj, dict):
        return {("null" if k is None else k): _fix_null_keys(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_fix_null_keys(v) for v in obj]
    return obj


def load_manifest(path: Path) -> dict:
    text = Path(path).read_text(encoding="utf-8-sig")
    docs = [d for d in yaml.safe_load_all(text) if d is not None] if "\n---" in text else []
    if len(docs) == 2:
        # v0.6+ layout: `error_kinds:` mapping, `---`, then the fixture list.
        header, fixtures = docs
    else:
        # Older layout: no `---` separator; split at the first fixture entry.
        idx = text.find("\n- id:")
        if idx < 0:
            raise ValueError("fixtures list not found in manifest")
        header = yaml.safe_load(text[: idx + 1]) or {}
        fixtures = yaml.safe_load(text[idx + 1:]) or []
    return {"error_kinds": header.get("error_kinds", []), "fixtures": _fix_null_keys(fixtures)}


def root_dir(manifest_path: Path) -> Path:
    return Path(manifest_path).parent


def filter_rf(manifest: dict) -> list[dict]:
    out = []
    for fx in manifest["fixtures"]:
        if fx.get("superseded_by"):
            continue
        fid = fx["id"]
        if fid.startswith(("plain/", "zip/", "pack/")):
            out.append(fx)
    return out


# UpstreamFixtureBugs are files whose bytes/yaml disagree with the spec. The
# parser follows the spec; skip until boligolov/excsv fixes the corpus.
UPSTREAM_FIXTURE_BUGS: dict[str, str] = {}


def header_field(doc: excsv.Document, key: str) -> str:
    if key in doc.header.fields:
        return doc.header.fields[key]
    if key == "version":
        return doc.header.version
    if key == "delim":
        return doc.header.delim_name
    if key == "quote":
        return doc.header.quote_name
    if key == "header":
        return "1" if doc.header.header_row else "0"
    if key == "null":
        return doc.header.null
    if key == "checksum":
        if doc.header.checksum is not None:
            return doc.header.checksum.algorithm + ":" + doc.header.checksum.hex
        return ""
    if key == "sql-dialect":
        return doc.header.sql_dialect
    if key == "reference":
        return doc.source.reference or doc.header.fields.get("reference", "")
    if key in ("csvw", "schema", "layout", "single-table", "table-count"):
        return doc.header.fields.get(key, "")
    return ""


def warning_kinds(warnings: list[excsv.Issue], kind: str) -> bool:
    return any(w.kind.value == kind for w in warnings)


def count_sql(doc: excsv.Document) -> tuple[int, int]:
    ddl = sum(1 for s in doc.meta.sql if s.verb == "ddl")
    dql = sum(1 for s in doc.meta.sql if s.verb == "dql")
    return ddl, dql


def pack_dialects(pack) -> list[str]:
    if pack is None:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for t in pack.tables:
        d = t.header.header.sql_dialect or t.header.header.fields.get("sql-dialect", "")
        if not d or d in seen:
            continue
        seen.add(d)
        out.append(d)
    return out


def physical_column_decls(doc: excsv.Document) -> int:
    return sum(1 for col in doc.meta.columns if not _is_virtual(col))


def _is_virtual(col: excsv.ColumnDef) -> bool:
    return bool(col.attrs.get("formula")) and col.attrs.get("materialized") != "1"


def table_doc(res, expect: dict) -> excsv.Document:
    """The document whose schema a fixture's expectations describe: the named
    (or only) pack table for a pack, otherwise the document itself."""
    if res.pack is None:
        return res.doc
    name = (expect.get("table") or {}).get("name", "")
    if name:
        try:
            return res.pack.table(name).document()
        except KeyError:
            pass
    pt = res.pack.default_table()
    if pt is not None:
        return pt.document()
    return res.doc


def assert_computed(doc: excsv.Document, want: dict) -> None:
    virtual, materialized = [], []
    for col in doc.meta.columns:
        if _is_virtual(col):
            virtual.append(col.attrs.get("name", ""))
        elif col.attrs.get("formula"):
            materialized.append(col.attrs.get("name", ""))
    if want.get("virtual") is not None:
        assert virtual == list(want["virtual"]), f"computed.virtual: got {virtual} want {want['virtual']}"
    if want.get("materialized") is not None:
        assert materialized == list(want["materialized"]),             f"computed.materialized: got {materialized} want {want['materialized']}"


def assert_charts(doc: excsv.Document, want: dict) -> None:
    charts = doc.meta.charts
    if want.get("count") is not None:
        assert len(charts) == want["count"], f"charts.count: got {len(charts)} want {want['count']}"
    types = [c.type for c in charts if not c.is_escape]
    engines = [c.engine for c in charts if c.is_escape]
    if want.get("types") is not None:
        assert types == list(want["types"]), f"charts.types: got {types} want {want['types']}"
    if want.get("engines") is not None:
        assert engines == list(want["engines"]), f"charts.engines: got {engines} want {want['engines']}"


def assert_notes(doc: excsv.Document, want: dict) -> None:
    notes = doc.resolve_notes()
    if want.get("count") is not None:
        assert len(notes) == want["count"], f"notes.count: got {len(notes)} want {want['count']}"
    if want.get("targets") is not None:
        got = [n.target.value for n in notes]
        assert got == list(want["targets"]), f"notes.targets: got {got} want {want['targets']}"
    if want.get("texts") is not None:
        got = [n.text for n in notes]
        assert got == list(want["texts"]), f"notes.texts: got {got} want {want['texts']}"
    if want.get("resolved_rows") is not None:
        got = [n.row for n in notes]
        assert got == list(want["resolved_rows"]), f"notes.resolved_rows: got {got} want {want['resolved_rows']}"


def assert_cell_links(doc: excsv.Document, want: dict) -> None:
    links = doc.resolve_links()
    for addr, exp in want.items():
        row, _, col = str(addr).partition(",")
        got = links.cell_link(int(row), col)
        assert got == exp, f"cell_links[{addr!r}]: got {got!r} want {exp!r}"


def assert_expectation(fixture: dict, res, err: Exception | None) -> None:
    expect = fixture.get("expect") or {}
    expect_ok = expect.get("parse") == "ok"

    if expect_ok:
        assert err is None, f"expected ok, got error: {err}"
        assert res is not None and res.doc is not None, "expected document, got nil"
        doc = res.doc

        for k, v in (expect.get("header") or {}).items():
            got = header_field(doc, k)
            want = str(v)
            assert got == want, f"header[{k!r}]: got {got!r} want {want!r}"

        for k, v in (expect.get("meta") or {}).items():
            assert doc.meta_map().get(k, "") == str(v), f"meta[{k!r}]"

        if expect.get("rows") is not None:
            assert doc.row_count() == expect["rows"], f"rows: got {doc.row_count()} want {expect['rows']}"

        if expect.get("columns") is not None:
            got = physical_column_decls(doc)
            assert got == expect["columns"], f"columns: got {got} want {expect['columns']}"

        tdoc = table_doc(res, expect)
        if expect.get("computed") is not None:
            assert_computed(tdoc, expect["computed"])
        if expect.get("charts") is not None:
            assert_charts(doc, expect["charts"])
        if expect.get("notes") is not None:
            assert_notes(tdoc, expect["notes"])
        if (expect.get("links") or {}).get("count") is not None:
            got = len(tdoc.meta.links)
            assert got == expect["links"]["count"], f"links.count: got {got} want {expect['links']['count']}"
        if expect.get("cell_links") is not None:
            assert_cell_links(tdoc, expect["cell_links"])

        if expect.get("sql") is not None:
            sql_exp = expect["sql"]
            ddl, dql = count_sql(doc)
            if sql_exp.get("ddl_count") is not None:
                assert ddl == sql_exp["ddl_count"], f"ddl_count: got {ddl} want {sql_exp['ddl_count']}"
            if sql_exp.get("dql_count") is not None:
                assert dql == sql_exp["dql_count"], f"dql_count: got {dql} want {sql_exp['dql_count']}"
            if sql_exp.get("dialects"):
                got = pack_dialects(res.pack)
                assert got == list(sql_exp["dialects"]), f"sql.dialects: got {got} want {sql_exp['dialects']}"

        if expect.get("tables") is not None:
            n = len(res.pack.tables) if res.pack else 0
            assert n == expect["tables"], f"tables: got {n} want {expect['tables']}"

        if expect.get("fk_count") is not None:
            n = len(res.pack.fks) if res.pack else 0
            assert n == expect["fk_count"], f"fk_count: got {n} want {expect['fk_count']}"

        if expect.get("table") is not None:
            t_exp = expect["table"]
            assert res.pack is not None and res.pack.tables, "expected pack table"
            pt = res.pack.tables[0]
            name = t_exp.get("name", "")
            if name:
                pt = res.pack.table(name)
                assert pt.decl.name == name, f"table.name: got {pt.decl.name!r} want {name!r}"
            if t_exp.get("rows") is not None:
                if pt.header is not None and pt.header.header.rows is not None:
                    got = pt.header.header.rows
                else:
                    got = len(pt.header.data.rows)
                assert got == t_exp["rows"], f"table.rows: got {got} want {t_exp['rows']}"
            if t_exp.get("columns") is not None:
                assert len(pt.col_values) == t_exp["columns"], "table.columns mismatch"
            if t_exp.get("sectioned") is not None:
                assert pt.sectioned == t_exp["sectioned"], f"table.sectioned: got {pt.sectioned}"
            if t_exp.get("section-size") is not None:
                assert pt.section_size == t_exp["section-size"], "table.section-size mismatch"

        if expect.get("comment") is not None:
            c_exp = expect["comment"]
            c = doc.source.comment
            starts_with = c_exp.get("starts_with", "")
            if starts_with:
                assert c.startswith(starts_with), f"comment starts_with: got {c!r}"
            ends_with = c_exp.get("ends_with", "")
            if ends_with:
                assert c.endswith(ends_with), f"comment ends_with: got {c!r}"

        if expect.get("profile"):
            assert doc.source.profile.value == expect["profile"], \
                f"profile: got {doc.source.profile.value!r} want {expect['profile']!r}"

        warnings_expected = expect.get("warnings") or []
        if not warnings_expected:
            assert not res.warnings, f"unexpected warnings: {res.warnings}"
        for w in warnings_expected:
            assert warning_kinds(res.warnings, w), f"missing warning {w!r} (got {res.warnings})"
        return

    assert err is not None, "expected parse failure, got ok"
    assert isinstance(err, excsv.ParseError), f"expected ParseError, got {type(err)}: {err}"
    got_kind = err.issue.kind.value
    want_kind = expect.get("error_kind")
    assert got_kind == want_kind, f"error_kind: got {got_kind!r} want {want_kind!r} ({err.issue.message})"
