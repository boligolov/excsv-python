"""ParseBytes / ParseRecords -- the header+meta+data state machine."""

from __future__ import annotations

from ._column import validate_columns
from ._csv import parse_agg_payload
from ._data_section import build_data_section
from ._dialect import apply_header_defaults
from ._document import (
    Aggregation,
    ColumnDef,
    Document,
    ForeignKey,
    ParseOptions,
    ParseResult,
    TableDecl,
    UnknownMetaLine,
)
from ._errors import ErrorKind, ParseError, fail
from ._kv import is_reserved_meta_prefix, parse_header_line, parse_kv_line, skip_colon_value
from ._records import extract_data_section, first_line_bytes, split_records, trim_trailing_empty_records
from ._sidecar_util import header_reference, is_likely_sidecar_reference
from ._sql import parse_sql_line, sql_payload_unclosed
from ._warnings import (
    apply_checksum_warning,
    apply_rows_mismatch_warning,
    collect_header_warnings,
    collect_meta_warnings,
    collect_sql_warnings,
    encoding_issue,
)


def parse_bytes(data: bytes, opts: ParseOptions) -> ParseResult:
    from ._bom import strip_utf8_bom
    from ._kv import valid_utf8

    data = strip_utf8_bom(data)

    enc = "UTF-8"
    first_line = first_line_bytes(data)
    if first_line.startswith("#!excsv"):
        try:
            fields = parse_header_line(first_line)
            if "encoding" in fields:
                enc = fields["encoding"]
        except ParseError:
            pass

    if enc.upper() == "UTF-8" or enc == "":
        if not valid_utf8(data):
            raise fail(ErrorKind.INVALID_UTF8, 0, "invalid UTF-8")

    if len(data) == 0:
        doc = Document()
        apply_header_defaults(doc.header)
        return ParseResult(doc=doc)

    records = split_records(data)
    full = data.decode("utf-8", errors="surrogateescape")
    res = _parse_records(records, full, opts)
    from ._warnings import append_extsv_warning

    iss = encoding_issue(data, enc)
    if iss is not None:
        res.warn(iss.kind, iss.line, iss.message)
    append_extsv_warning(res, opts.source_path)
    return res


def _parse_records(records: list, full: str, opts: ParseOptions) -> ParseResult:
    from ._sidecar import finish_sidecar_meta, validate_expect_profile

    doc = Document()
    idx = 0

    records = trim_trailing_empty_records(records)

    if not records:
        apply_header_defaults(doc.header)
        from ._document import Profile

        doc.source.profile = Profile.STUB
        validate_expect_profile(doc, opts.expect_profile)
        return ParseResult(doc=doc)

    first = records[0].text
    if first.startswith("#!excsv"):
        fields = parse_header_line(first)
        doc.header.fields = fields
        doc.header.has_magic_line = True
        if "version" in fields:
            doc.header.version = fields["version"]
        idx = 1

    apply_header_defaults(doc.header)

    res = ParseResult(doc=doc)
    collect_header_warnings(res, opts)

    if opts.expect_zip_inner:
        if doc.header.original_size is None:
            if opts.zip_load_data:
                raise fail(ErrorKind.ZIP_MISSING_ORIGINAL_SIZE, 1, "missing original-size in zipped inner file")
        elif opts.zip_uncompressed_size > 0:
            got = doc.header.original_size
            want = opts.zip_uncompressed_size
            if got != want and got != want - 3 and got + 3 != want:
                raise fail(ErrorKind.ZIP_ORIGINAL_SIZE_MISMATCH, 1, "original-size does not match ZIP entry size")

    d = doc.header.dialect()
    last_was_sql = False
    last_sql_payload = ""
    n = len(records)
    while idx < n:
        line = records[idx].text
        ln = records[idx].num
        if line == "":
            idx += 1
            continue
        if not line.startswith("#"):
            if last_was_sql and sql_payload_unclosed(last_sql_payload):
                raise fail(ErrorKind.SQL_EMBEDDED_NEWLINE, ln, "SQL statement spans multiple lines")
            break
        if line.startswith("#!excsv"):
            raise fail(ErrorKind.HEADER_MALFORMED_MAGIC, ln, "duplicate header line")
        if line.startswith("##"):
            if not opts.clear_human_comments:
                doc.meta.human_comments.append(line)
            last_was_sql = False
            idx += 1
            continue
        if line.startswith("#excsv"):
            raise fail(ErrorKind.HEADER_MALFORMED_MAGIC, ln, "malformed header magic in meta section")
        if is_reserved_meta_prefix(line):
            if opts.pack_role == "manifest":
                if line.startswith("#table"):
                    doc.meta.tables.append(_parse_table_line(line, ln))
                elif line.startswith("#fk"):
                    doc.meta.fks.append(_parse_fk_line(line, ln))
            else:
                res.warn(ErrorKind.PACK_KEY_ON_PLAIN, ln, "pack-only meta line on plain/row file")
            last_was_sql = False
            idx += 1
            continue
        if line.startswith("#@"):
            key, val, ok = skip_colon_value(line, "#@")
            if ok:
                _upsert_kv(doc.meta.file_meta, key, val)
            last_was_sql = False
        elif line.startswith("#column"):
            rest = line[len("#column "):] if line.startswith("#column ") else line
            attrs = parse_kv_line(rest, ln)
            doc.meta.columns.append(ColumnDef(attrs=attrs, line=ln))
            last_was_sql = False
        elif line.startswith("#%"):
            rest = line[2:]
            colon = rest.find(":")
            if colon < 0:
                doc.meta.unknown.append(UnknownMetaLine(text=line, line=ln))
                idx += 1
                continue
            name = rest[:colon]
            payload = rest[colon + 1:]
            if payload.startswith(" "):
                payload = payload[1:]
            try:
                vals = parse_agg_payload(payload, d)
            except ParseError as e:
                e.issue.line = ln
                raise
            doc.meta.aggregations.append(Aggregation(name=name, values=vals, line=ln))
            last_was_sql = False
        elif line.startswith("#$"):
            stmt = parse_sql_line(line, ln)
            doc.meta.sql.append(stmt)
            collect_sql_warnings(res, stmt)
            last_was_sql = True
            last_sql_payload = stmt.payload
        else:
            doc.meta.unknown.append(UnknownMetaLine(text=line, line=ln))
            last_was_sql = False
        idx += 1

    ref = header_reference(doc.header)
    has_inline_data = any(records[j].text != "" for j in range(idx, n))
    if ref:
        if has_inline_data:
            if is_likely_sidecar_reference(opts.source_path, ref):
                raise fail(ErrorKind.SIDECAR_HAS_DATA_SECTION, records[idx].num, "sidecar must not contain a data section")
            raise fail(ErrorKind.REFERENCE_ON_INLINE, 1, "inline document must not set reference=")
        return finish_sidecar_meta(res, opts)
    if has_inline_data and not ref:
        from ._document import Profile

        doc.source.profile = Profile.INLINE

    data_records = records[idx:]
    parse_records = data_records
    if parse_records and parse_records[-1].text == "":
        parse_records = parse_records[:-1]

    ds, _section, col_count, _warn = build_data_section(
        parse_records, full, d, doc.header.header_row, doc.header.fields.get("quote", ""), 0, idx, True
    )
    doc.data = ds

    validate_columns(res, col_count)
    collect_meta_warnings(res)
    apply_rows_mismatch_warning(res)

    if doc.header.checksum is not None and data_records:
        data_section = extract_data_section(full, records, idx)
        apply_checksum_warning(res, data_section, False)

    if not has_inline_data and not ref:
        from ._document import Profile

        doc.source.profile = Profile.STUB
        validate_expect_profile(doc, opts.expect_profile)

    return res


def _parse_table_line(line: str, line_no: int) -> TableDecl:
    attrs = parse_kv_line(line[len("#table"):].strip(), line_no)
    decl = TableDecl(name=attrs.get("name", ""), dir=attrs.get("dir", ""), attrs=attrs, line=line_no)
    v = attrs.get("columns", "")
    if v:
        try:
            decl.columns = int(v)
        except ValueError:
            raise fail(ErrorKind.HEADER_INVALID_VALUE, line_no, "invalid columns=" + v)
    v = attrs.get("original-size", "")
    if v:
        try:
            decl.original_size = int(v)
        except ValueError:
            raise fail(ErrorKind.HEADER_INVALID_VALUE, line_no, "invalid original-size=" + v)
    return decl


def _parse_fk_line(line: str, line_no: int) -> ForeignKey:
    attrs = parse_kv_line(line[len("#fk"):].strip(), line_no)
    return ForeignKey(from_=attrs.get("from", ""), to=attrs.get("to", ""), line=line_no)


def _upsert_kv(lst, key, val):
    for kv in lst:
        if kv.key == key:
            kv.value = val
            return lst
    from ._document import KV

    lst.append(KV(key=key, value=val))
    return lst
