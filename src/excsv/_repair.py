"""SetHeaderField (dialect/null/header/checksum conversions), Tidy, InferColumns."""

from __future__ import annotations

import unicodedata

from ._dialect import WELL_KNOWN_DELIMS, WELL_KNOWN_QUOTES, apply_header_defaults, resolve_delim, resolve_quote
from ._document import ColumnDef, Document
from ._errors import ErrorKind, fail

DEFAULT_AGGREGATIONS = ["count_nonnull", "count_null", "sum", "min", "max"]


def set_header_field(doc: Document, key: str, value: str) -> None:
    key = key.strip()
    if not key:
        raise ValueError("header key is required")
    if doc.header.fields is None:
        doc.header.fields = {}

    if key == "rows":
        doc.sync_derived()
        return
    if key == "original-size":
        raise ValueError("original-size= is derived on ZIP/pack write")
    if key == "encoding":
        enc = value.strip()
        if enc and enc.upper() not in ("UTF-8", "UTF8"):
            raise fail(ErrorKind.ENCODING_UNSUPPORTED, 1, "header set encoding= rewrites as UTF-8 only")
        if not enc:
            doc.header.fields.pop("encoding", None)
            doc.header.encoding = "UTF-8"
        else:
            doc.header.fields["encoding"] = "UTF-8"
            doc.header.encoding = "UTF-8"
        doc.sync_derived()
        return
    if key == "delim":
        if not value.strip():
            doc.header.fields.pop("delim", None)
            apply_header_defaults(doc.header)
            doc.sync_derived()
            return
        _convert_delim(doc, value)
        return
    if key == "quote":
        _convert_quote(doc, value)
        return
    if key == "header":
        _convert_header_row(doc, value)
        return
    if key == "null":
        _convert_null(doc, value)
        return
    if key == "checksum":
        _convert_checksum(doc, value)
        return
    if key == "version":
        if not value.strip():
            raise fail(ErrorKind.HEADER_MISSING_VERSION, 1, "version= cannot be empty")
        doc.header.fields["version"] = value
        doc.header.version = value
        apply_header_defaults(doc.header)
        return
    if key == "layout":
        if value not in ("pack", "columnar", ""):
            raise fail(ErrorKind.HEADER_INVALID_VALUE, 1, "layout must be pack or columnar")
        _set_optional_field(doc, "layout", value)
        return
    if key == "section-size":
        if value and value != "0":
            from ._dialect import parse_int_field

            try:
                parse_int_field(value)
            except ValueError:
                raise fail(ErrorKind.HEADER_INVALID_VALUE, 1, "invalid section-size=" + value)
        _set_optional_field(doc, "section-size", value)
        return
    if key in ("single-table", "table-count", "sql-dialect", "reference"):
        if key == "sql-dialect":
            doc.header.sql_dialect = value
        _set_optional_field(doc, key, value)
        return
    if not value:
        doc.header.fields.pop(key, None)
        return
    doc.header.fields[key] = value
    apply_header_defaults(doc.header)


def _set_optional_field(doc: Document, key: str, value: str) -> None:
    if not value:
        doc.header.fields.pop(key, None)
    else:
        doc.header.fields[key] = value
    apply_header_defaults(doc.header)


def _convert_delim(doc: Document, value: str) -> None:
    if not value.strip():
        raise fail(ErrorKind.HEADER_INVALID_VALUE, 1, "empty delimiter")
    r = resolve_delim(value)
    name = value
    for n, wr in WELL_KNOWN_DELIMS.items():
        if wr == r:
            name = n
            break
    if doc.header.quote_name == "none" or not doc.header.quote_enabled:
        if cells_contain_rune(doc, r):
            doc.header.fields["quote"] = "double"
    doc.header.fields["delim"] = name
    doc.header.delim_name = name
    apply_header_defaults(doc.header)
    doc.sync_derived()


def _convert_quote(doc: Document, value: str) -> None:
    if not value:
        value = "none"
    _, enabled = resolve_quote(value)
    name = value if value in WELL_KNOWN_QUOTES else value
    if not enabled and cells_contain_rune(doc, doc.header.delim):
        raise fail(ErrorKind.QUOTE_NONE_DELIMITER_IN_VALUE, 0, "quote=none but a value contains the delimiter")
    doc.header.fields["quote"] = name
    doc.header.quote_name = name
    apply_header_defaults(doc.header)
    doc.sync_derived()


def _convert_header_row(doc: Document, value: str) -> None:
    if value not in ("0", "1"):
        raise fail(ErrorKind.HEADER_INVALID_VALUE, 1, "invalid header=" + value)
    want = value == "1"
    if want and not doc.data.has_header_row:
        from ._pack import column_names_from_header

        names = doc.data.header_row or column_names_from_header(doc)
        if not names:
            w = doc.column_width()
            names = [f"col{i}" for i in range(w)]
        doc.data.header_row = names
        doc.data.has_header_row = True
    if not want:
        doc.data.has_header_row = False
    doc.header.fields["header"] = value
    doc.header.header_row = want
    apply_header_defaults(doc.header)
    doc.sync_derived()


def _convert_null(doc: Document, value: str) -> None:
    old = doc.header.null
    _rewrite_null_cells(doc, old, value)
    if not value:
        doc.header.fields.pop("null", None)
        doc.header.null = ""
    else:
        doc.header.fields["null"] = value
        doc.header.null = value
    apply_header_defaults(doc.header)
    doc.sync_derived()


def _convert_checksum(doc: Document, value: str) -> None:
    from ._dialect import classify_checksum_field

    value = value.strip()
    if not value:
        doc.header.fields.pop("checksum", None)
        doc.header.checksum = None
        return
    alg = value
    i = value.find(":")
    if i > 0:
        alg = value[:i]
        if value[i + 1:].strip():
            doc.header.fields["checksum"] = value
            cs, kind = classify_checksum_field(value)
            if kind is not None:
                raise fail(kind, 1, "checksum=" + value)
            doc.header.checksum = cs
            return
    doc.set_data_checksum(alg)


def _rewrite_null_cells(doc: Document, old: str, new: str) -> None:
    def map_cell(s: str) -> str:
        is_null = s == "" or (old != "" and s == old)
        if not is_null:
            return s
        return new if new else ""

    for row in doc.data.rows:
        for c in range(len(row)):
            row[c] = map_cell(row[c])


def cells_contain_rune(doc: Document, r: str) -> bool:
    if not r:
        return False
    if doc.data.has_header_row:
        if any(r in c for c in doc.data.header_row):
            return True
    return any(r in c for row in doc.data.rows for c in row)


def tidy(doc: Document) -> None:
    """Repairs ragged rows, canonicalizes meta order, NFC-normalizes cells,
    strips C0 controls, and resyncs derived header fields."""
    width = doc.column_width()
    if width > 0:
        from ._dataops import pad_or_trim

        for i, row in enumerate(doc.data.rows):
            doc.data.rows[i] = pad_or_trim(row, width)
        if doc.data.has_header_row:
            doc.data.header_row = pad_or_trim(doc.data.header_row, width)
    for row in doc.data.rows:
        for c in range(len(row)):
            row[c] = _tidy_cell(row[c])
    if doc.data.has_header_row:
        doc.data.header_row = [_tidy_cell(v) for v in doc.data.header_row]
    _dedupe_file_meta(doc)
    _sort_meta(doc)
    if doc.header.quote_name == "none" or not doc.header.quote_enabled:
        if cells_contain_rune(doc, doc.header.delim):
            doc.header.fields["quote"] = "double"
            apply_header_defaults(doc.header)
    doc.sync_derived()


def _tidy_cell(s: str) -> str:
    s = unicodedata.normalize("NFC", s)
    out = []
    for ch in s:
        if ch == "\t":
            out.append(ch)
            continue
        cp = ord(ch)
        if cp < 0x20 or cp == 0x7F:
            continue
        if unicodedata.category(ch) == "Cc":
            continue
        out.append(ch)
    return "".join(out)


def _dedupe_file_meta(doc: Document) -> None:
    from ._parse import _upsert_kv

    out: list = []
    for kv in doc.meta.file_meta:
        _upsert_kv(out, kv.key, kv.value)
    doc.meta.file_meta = out


def _sort_meta(doc: Document) -> None:
    doc.meta.file_meta.sort(key=lambda kv: kv.key)
    doc.meta.aggregations.sort(key=lambda a: a.name)


def infer_columns(doc: Document) -> None:
    """Adds #column stubs from the header row (or colN) when none exist."""
    if doc.meta.columns:
        return
    titles = list(doc.data.header_row)
    if not titles:
        from ._pack import column_names_from_header

        titles = column_names_from_header(doc)
    infer_columns_from_header(doc, titles, doc.data.has_header_row)


def infer_columns_from_header(doc: Document, titles: list[str], has_header: bool) -> None:
    """Declares one #column per physical column, with an inferred type=.
    name= is a sanitized identifier and title= keeps the raw header text when
    the two differ."""
    if doc.meta.columns:
        return
    width = doc.column_width()
    if len(titles) < width:
        titles = list(titles) + [""] * (width - len(titles))
    used: set[str] = set()
    for i, title in enumerate(titles):
        name = _unique_column_name(_sanitize_column_name(title, i), used)
        attrs = {"name": name, "type": _sniff_column_type(doc, i)}
        if has_header and title and title != name:
            attrs["title"] = title
        if not has_header:
            attrs["index"] = str(i)
        doc.meta.columns.append(ColumnDef(attrs=attrs))


def _sanitize_column_name(raw: str, index: int) -> str:
    out = []
    for ch in raw.strip():
        if ("a" <= ch <= "z") or ("A" <= ch <= "Z") or ("0" <= ch <= "9") or ch in "_-":
            out.append(ch)
        else:
            out.append("_")
    name = "".join(out).strip("_")
    if not name:
        return f"col{index}"
    c = name[0]
    if not (("a" <= c <= "z") or ("A" <= c <= "Z") or c == "_"):
        name = "_" + name
    return name


def _unique_column_name(name: str, used: set[str]) -> str:
    candidate = name
    i = 2
    while candidate in used:
        candidate = f"{name}_{i}"
        i += 1
    used.add(candidate)
    return candidate


def _sniff_column_type(doc: Document, col: int) -> str:
    from ._agg import cell_at, is_null_cell

    saw = False
    all_int = True
    all_num = True
    for row in doc.data.rows:
        v = cell_at(doc, row, col)
        if is_null_cell(doc, v):
            continue
        saw = True
        v = v.strip()
        try:
            int(v, 10)
        except ValueError:
            all_int = False
        try:
            float(v)
        except ValueError:
            all_num = False
            break
    if not saw:
        return "string"
    if all_int:
        return "int"
    if all_num:
        return "double"
    return "string"


Document.set_header_field = set_header_field
Document.tidy = tidy
Document.infer_columns = infer_columns
Document.infer_columns_from_header = infer_columns_from_header
