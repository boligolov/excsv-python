"""Column resolution, #column CRUD, and attribute formatting."""

from __future__ import annotations

from ._document import ColumnDef, Document
from ._errors import ErrorKind, fail

COLUMN_ATTR_DISPLAY_ORDER = [
    "name", "index", "title", "description", "type", "format", "unit",
    "role", "agg", "order", "separator", "enum", "pattern", "regexp_dialect",
    "min", "max", "len_min", "len_max", "unique", "required", "default",
    "formula", "materialized",
]


def is_virtual_column(col: ColumnDef) -> bool:
    if not col.attrs.get("formula"):
        return False
    return col.attrs.get("materialized") != "1"


def column_count_from_schema(cols: list[ColumnDef]) -> int:
    max_idx = -1
    physical = 0
    for c in cols:
        if is_virtual_column(c):
            continue
        physical += 1
        idx = c.attrs.get("index")
        if idx is not None and idx.isdigit():
            n = int(idx)
            if n > max_idx:
                max_idx = n
    if max_idx >= 0:
        return max_idx + 1
    if physical > 0:
        return physical
    return 0


def effective_column_count(doc: Document, header_width: int) -> int:
    if doc.header.header_row and header_width > 0:
        return header_width
    n = column_count_from_schema(doc.meta.columns)
    if n > 0:
        return n
    return header_width


def column_width(doc: Document) -> int:
    if doc.data.has_header_row and len(doc.data.header_row) > 0:
        return len(doc.data.header_row)
    n = column_count_from_schema(doc.meta.columns)
    if n > 0:
        return n
    if doc.data.rows:
        return len(doc.data.rows[0])
    return 0


def column_def_at(doc: Document, col: int) -> tuple[ColumnDef | None, bool]:
    phys = 0
    for c in doc.meta.columns:
        if is_virtual_column(c):
            continue
        idx = phys
        v = c.attrs.get("index")
        if v is not None:
            try:
                idx = int(v)
            except ValueError:
                pass
        if idx == col:
            return c, True
        phys += 1
    return None, False


def column_by_name(doc: Document, name: str) -> tuple[int, ColumnDef | None, bool]:
    for i, col in enumerate(doc.meta.columns):
        if col.attrs.get("name") == name:
            return i, col, True
    return -1, None, False


def column_index(doc: Document, ref: str) -> int:
    ref = ref.strip()
    if ref == "":
        raise ValueError("empty column reference")
    try:
        n = int(ref)
        is_int = True
    except ValueError:
        is_int = False
        n = 0
    if is_int:
        width = column_width(doc)
        if n < 0 or (width > 0 and n >= width):
            raise ValueError(f"column index out of range: {n}")
        return n
    for i, cell in enumerate(doc.data.header_row):
        if cell == ref:
            return i
    phys = 0
    for col in doc.meta.columns:
        virtual = is_virtual_column(col)
        if col.attrs.get("name") == ref:
            if virtual:
                raise ValueError(f"column {ref} is virtual (formula=), has no physical index")
            v = col.attrs.get("index")
            if v is not None:
                try:
                    return int(v)
                except ValueError:
                    pass
            return phys
        if not virtual:
            phys += 1
    raise ValueError(f"unknown column: {ref}")


def upsert_column(doc: Document, name: str, attrs: dict[str, str]) -> None:
    name = name.strip()
    if name == "":
        raise ValueError("column name is required")
    if " " in name:
        raise fail(ErrorKind.COLUMN_MALFORMED_ATTRIBUTE, 0, "column name must not contain spaces")
    idx, col, ok = column_by_name(doc, name)
    if not ok:
        merged = {"name": name}
        for k, v in attrs.items():
            if k == "":
                continue
            merged[k] = v
        doc.meta.columns.append(ColumnDef(attrs=merged))
        return
    if col.attrs is None:
        col.attrs = {}
    col.attrs["name"] = name
    for k, v in attrs.items():
        if k == "":
            continue
        if v == "":
            col.attrs.pop(k, None)
            continue
        col.attrs[k] = v
    doc.meta.columns[idx] = col


def remove_column(doc: Document, name: str) -> bool:
    found = False
    out = []
    for col in doc.meta.columns:
        if col.attrs.get("name") == name:
            found = True
            continue
        out.append(col)
    doc.meta.columns = out
    return found


def format_column_attr(k: str, v: str) -> str:
    if " " in v or "\t" in v:
        return k + '="' + v.replace('"', '""') + '"'
    return k + "=" + v


def format_column_info_attr(k: str, v: str) -> str:
    if any(c in v for c in " \t,") or '"' in v:
        return k + ': "' + v.replace('"', '""') + '"'
    return k + ": " + v


def _format_column_attr_parts(attrs: dict[str, str], fmt, sep: str) -> str:
    seen = set()
    parts = []
    for k in COLUMN_ATTR_DISPLAY_ORDER:
        if k not in attrs:
            continue
        seen.add(k)
        parts.append(fmt(k, attrs[k]))
    extra = sorted(k for k in attrs if k not in seen)
    for k in extra:
        parts.append(fmt(k, attrs[k]))
    return sep.join(parts)


def format_column_attrs(attrs: dict[str, str]) -> str:
    return _format_column_attr_parts(attrs, format_column_attr, " ")


def format_column_info_line(attrs: dict[str, str]) -> str:
    return _format_column_attr_parts(attrs, format_column_info_attr, ", ")


def parse_attr_int(s: str | None) -> tuple[int | None, bool]:
    if not s:
        return None, False
    try:
        return int(s.strip()), True
    except ValueError:
        return None, False


def validate_columns(res, header_width: int) -> None:
    """Checks #column declarations against the parsed header row/width.

    Raises ParseError (hard failure) or appends a warning to res, matching
    Go's parse.go validateColumns.
    """
    doc = res.doc
    phys = 0
    for col in doc.meta.columns:
        if col.attrs.get("formula"):
            if not doc.header.header_row:
                raise fail(ErrorKind.FORMULA_REQUIRES_HEADER, col.line, "formula= requires header=1")
            if "index" in col.attrs:
                raise fail(ErrorKind.FORMULA_INDEX_FORBIDDEN, col.line, "formula= column must not carry index=")
        if is_virtual_column(col):
            continue
        i = phys
        phys += 1
        if doc.header.header_row:
            name = col.attrs.get("name", "")
            if not name:
                raise fail(ErrorKind.COLUMN_MISSING_NAME, col.line, "missing name= when header=1")
            title = col.attrs.get("title")
            if header_width > 0 and len(doc.data.header_row) > i:
                cell = doc.data.header_row[i]
                if title is not None and cell != title:
                    res.warn(ErrorKind.COLUMN_TITLE_HEADER_MISMATCH, col.line, "title does not match header row")
                if title is None and cell != name:
                    res.warn(ErrorKind.COLUMN_NAME_HEADER_MISMATCH, col.line, "name does not match header row")
        elif "index" not in col.attrs:
            raise fail(ErrorKind.COLUMN_MISSING_INDEX, col.line, "missing index= when header=0")


# ---- bind as Document methods ----
Document.column_width = column_width
Document.column_def_at = column_def_at
Document.column_by_name = column_by_name
Document.column_index = column_index
Document.upsert_column = upsert_column
Document.remove_column = remove_column
