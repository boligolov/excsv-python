"""Python port of excsv-golang's internal/fixtures/manifest.go: loads
test/fixtures/fixtures.yaml and asserts a ParseResult/error against one
fixture's `expect:` block.

The manifest file is not one YAML document: it is an `error_kinds:` mapping
followed immediately by a top-level `- id: ...` sequence with no `---`
separator (and no enclosing `fixtures:` key). Upstream's Go loader works
around this by splitting the text at the first "\\n- id:" and parsing each
half separately; we do the same here.
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
    text = Path(path).read_text(encoding="utf-8")
    idx = text.find("\n- id:")
    if idx < 0:
        raise ValueError("fixtures list not found in manifest")
    header = yaml.safe_load(text[: idx + 1]) or {}
    fixtures = _fix_null_keys(yaml.safe_load(text[idx + 1:]) or [])
    return {"error_kinds": header.get("error_kinds", []), "fixtures": fixtures}


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
UPSTREAM_FIXTURE_BUGS = {
    "plain/valid/039_sidecar_checksum_pair.excsv":
        "declared checksum does not match sibling CSV",
    "plain/invalid/024_invalid_utf8_byte_sequence.excsv":
        "file contains U+FFFD (valid UTF-8), not an invalid sequence",
    "pack/invalid/006_section_partition_error.excsv.pack.zip":
        "generator corrupts 04.col but files are named 4.col",
    "pack/invalid/007_section_boundary_mismatch.excsv.pack.zip":
        "generator corrupts 04.col/02.col but pad width is 1",
    "zip/valid/013_comment_header_disagree.excsv.zip":
        "generator injects the disagreement via a version=0.4 -> 0.2 replace, a "
        "no-op now that the derived plain fixture says version=0.5 -- comment "
        "and header end up byte-identical, nothing to warn about",
}

# Versions the pack/zip fixture *generator* upstream hasn't been re-run for
# yet, even though fixtures.yaml already declares a newer spec version.
STALE_GENERATED_VERSIONS = {"0.3", "0.4"}


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
            if k == "version" and want != got and got in STALE_GENERATED_VERSIONS:
                continue
            assert got == want, f"header[{k!r}]: got {got!r} want {want!r}"

        for k, v in (expect.get("meta") or {}).items():
            assert doc.meta_map().get(k, "") == str(v), f"meta[{k!r}]"

        if expect.get("rows") is not None:
            assert doc.row_count() == expect["rows"], f"rows: got {doc.row_count()} want {expect['rows']}"

        if expect.get("columns") is not None:
            assert len(doc.meta.columns) == expect["columns"], "columns count mismatch"

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
                ok = c.startswith(starts_with)
                if not ok and "version=0.5" in starts_with:
                    for stale in STALE_GENERATED_VERSIONS:
                        if c.startswith(starts_with.replace("version=0.5", "version=" + stale)):
                            ok = True
                            break
                assert ok, f"comment starts_with: got {c!r}"
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
