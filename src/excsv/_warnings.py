"""Non-fatal parse-time findings (warnings), collected rather than raised."""

from __future__ import annotations

from ._agg import (
    cell_at,
    column_type_at,
    data_rows,
    is_measure_column_type,
    is_null_cell,
    is_string_column_type,
)
from ._checksum import verify_checksum
from ._column import column_count_from_schema, effective_column_count, is_virtual_column
from ._dialect import classify_checksum_field
from ._document import ColumnDef, Document, ParseOptions, ParseResult
from ._errors import ErrorKind, ParseError, new_issue
from ._kv import is_reserved_header_key
from ._sidecar_util import sidecar_ext_mismatch

KNOWN_COLUMN_ATTRS = {
    "name", "index", "title", "description",
    "type", "format", "unit", "role", "agg",
    "order", "separator", "enum", "pattern",
    "regexp_dialect", "min", "max", "len_min",
    "len_max", "unique", "required", "default",
    "formula", "materialized",
}

IMPLEMENTED_VERSIONS = {"0.2", "0.3", "0.4", "0.5"}

_KNOWN_8BIT_ENCODINGS = {
    "ISO-8859-1", "ISO8859-1", "LATIN1", "WINDOWS-1252", "US-ASCII", "ASCII", "CP1252",
}


def encoding_issue(data: bytes, enc: str):
    e = enc.strip().upper()
    if e in ("", "UTF-8", "UTF8"):
        return None
    if e.startswith("UTF-16") or e.startswith("UTF16") or e.startswith("UTF-32") or e.startswith("UTF32"):
        return new_issue(ErrorKind.ENCODING_NOT_ASCII_COMPATIBLE, 0, "encoding " + enc + " is not ASCII-compatible")
    if not known_8bit_encoding(e):
        return new_issue(ErrorKind.ENCODING_UNSUPPORTED, 0, "unsupported encoding " + enc)
    if _valid_utf8(data):
        return new_issue(ErrorKind.ENCODING_MISMATCH, 0, "content appears UTF-8 but encoding declares " + enc)
    return None


def _valid_utf8(data: bytes) -> bool:
    try:
        data.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def known_8bit_encoding(e: str) -> bool:
    return e in _KNOWN_8BIT_ENCODINGS


def collect_header_warnings(res: ParseResult, opts: ParseOptions) -> None:
    h = res.doc.header
    if h.has_magic_line and h.version and h.version not in IMPLEMENTED_VERSIONS:
        res.warn(ErrorKind.UNKNOWN_VERSION, 1, "unknown version=" + h.version)
    cs = h.fields.get("checksum", "")
    if cs:
        parsed, kind = classify_checksum_field(cs)
        if kind is not None:
            res.warn(kind, 1, "checksum=" + cs)
            h.checksum = None
        else:
            h.checksum = parsed
    if opts.pack_role == "" and not opts.expect_zip_inner and h.original_size is not None:
        res.warn(ErrorKind.ORIGINAL_SIZE_ON_PLAIN, 1, "original-size= on a plain file")
    if opts.pack_role == "":
        for k in h.fields:
            if is_reserved_header_key(k):
                res.warn(ErrorKind.PACK_KEY_ON_PLAIN, 1, "pack-only key " + k + " on plain/row file")
                break


def collect_sql_warnings(res: ParseResult, stmt) -> None:
    from ._sql import KNOWN_SQL_VERBS, is_known_dialect

    if stmt.verb not in KNOWN_SQL_VERBS:
        res.warn(ErrorKind.SQL_UNKNOWN_VERB, stmt.line, "unknown #$ verb " + stmt.verb)
    if stmt.qualified and stmt.dialect and not is_known_dialect(stmt.dialect):
        res.warn(ErrorKind.SQL_UNKNOWN_DIALECT, stmt.line, "unknown SQL dialect " + stmt.dialect)


def collect_meta_warnings(res: ParseResult) -> None:
    doc = res.doc
    apply_duplicate_column_last_wins(res)

    for col in doc.meta.columns:
        for k in col.attrs:
            if k.startswith("x-"):
                continue
            if k not in KNOWN_COLUMN_ATTRS:
                res.warn(ErrorKind.COLUMN_UNKNOWN_ATTRIBUTE, col.line, "unknown #column attribute " + k)
                break

    width = physical_width(doc)
    n = column_count_from_schema(doc.meta.columns)
    if width > 0 and n > width:
        res.warn(ErrorKind.COLUMN_COUNT_MISMATCH, 0, "schema column count exceeds physical width")
    for col in doc.meta.columns:
        idx = col.attrs.get("index")
        if idx is not None and width > 0 and idx.lstrip("-").isdigit():
            if int(idx) >= width:
                res.warn(ErrorKind.COLUMN_COUNT_MISMATCH, col.line, "index= exceeds physical width")
                break

    for col in doc.meta.columns:
        if "default" not in col.attrs:
            continue
        idx = column_index_for_def(doc, col)
        if idx < 0:
            continue
        for row in data_rows(doc):
            if is_null_cell(doc, cell_at(doc, row, idx)):
                res.warn(ErrorKind.DEFAULT_WITH_NULLS, col.line, "default= set but column contains nulls")
                break

    expected_cols = effective_column_count(doc, width)
    for agg in doc.meta.aggregations:
        if expected_cols > 0 and len(agg.values) > expected_cols:
            res.warn(ErrorKind.AGG_ARITY_MISMATCH, agg.line,
                      f"aggregation has {len(agg.values)} values, expected {expected_cols}")
        for col, v in enumerate(agg.values):
            if v == "":
                continue
            ct = column_type_at(doc, col)
            if ct == "":
                continue
            if agg.name in ("sum", "avg", "min", "max"):
                if not is_measure_column_type(ct):
                    res.warn(ErrorKind.AGG_TYPE_INCOMPATIBLE, agg.line,
                              agg.name + " incompatible with column type " + ct)
            elif agg.name in ("len_min", "len_max"):
                if not is_string_column_type(ct):
                    res.warn(ErrorKind.AGG_TYPE_INCOMPATIBLE, agg.line,
                              agg.name + " incompatible with column type " + ct)


def apply_duplicate_column_last_wins(res: ParseResult) -> None:
    cols = res.doc.meta.columns
    if len(cols) < 2:
        return
    seen_name: set[str] = set()
    seen_index: set[str] = set()
    kept: list[ColumnDef] = []
    dups: list[ColumnDef] = []
    for col in reversed(cols):
        name = col.attrs.get("name", "")
        idx = col.attrs.get("index", "")
        dup = False
        if name and name in seen_name:
            dup = True
        if idx and idx in seen_index:
            dup = True
        if dup:
            dups.append(col)
            continue
        if name:
            seen_name.add(name)
        if idx:
            seen_index.add(idx)
        kept.append(col)
    kept.reverse()
    res.doc.meta.columns = kept
    for col in dups:
        res.warn(ErrorKind.DUPLICATE_COLUMN, col.line, "duplicate #column; last-wins")


def physical_width(doc: Document) -> int:
    if doc.data.has_header_row:
        return len(doc.data.header_row)
    if doc.data.rows:
        return len(doc.data.rows[0])
    return 0


def column_index_for_def(doc: Document, col: ColumnDef) -> int:
    v = col.attrs.get("index")
    if v is not None:
        try:
            return int(v)
        except ValueError:
            return -1
    name = col.attrs.get("name", "")
    if not name:
        return -1
    for i, cell in enumerate(doc.data.header_row):
        if cell == name or cell == col.attrs.get("title"):
            return i
    for i, c in enumerate(doc.meta.columns):
        if c.attrs.get("name") == name:
            v2 = c.attrs.get("index")
            if v2 is not None:
                try:
                    return int(v2)
                except ValueError:
                    pass
            return i
    return -1


def append_extsv_warning(res: ParseResult, path: str) -> None:
    if res is None or res.doc is None:
        return
    if sidecar_ext_mismatch(path, res.doc.header):
        for w in res.warnings:
            if w.kind == ErrorKind.EXTSV_DELIM_MISMATCH:
                return
        res.warn(ErrorKind.EXTSV_DELIM_MISMATCH, 1, ".extsv should declare delim=tab")


def apply_checksum_warning(res: ParseResult, data_section: str, sidecar: bool) -> None:
    if res.doc.header.checksum is None:
        return
    try:
        verify_checksum(data_section, res.doc.header.checksum)
    except ParseError as e:
        kind = ErrorKind.SIDECAR_CHECKSUM_MISMATCH if sidecar else ErrorKind.CHECKSUM_MISMATCH
        if e.issue.kind == ErrorKind.CHECKSUM_UNKNOWN_ALGORITHM:
            kind = ErrorKind.CHECKSUM_UNKNOWN_ALGORITHM
        res.warn(kind, 1, str(e.issue))


def apply_rows_mismatch_warning(res: ParseResult) -> None:
    if res.doc.header.rows is None:
        return
    if not res.doc.data.has_header_row and not res.doc.data.rows:
        return
    if res.doc.row_count() != res.doc.header.rows:
        res.warn(ErrorKind.ROWS_MISMATCH, 1, "rows= does not match data row count")
