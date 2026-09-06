"""Document -> canonical text form, plus the small RowCount/MetaMap helpers."""

from __future__ import annotations

from ._csv import join_csv_fields
from ._document import Document
from ._sidecar_util import header_reference

CANONICAL_HEADER_ORDER = [
    "version", "layout", "delim", "quote", "header", "encoding", "null", "rows",
    "checksum", "sql-dialect", "reference", "original-size", "table-count",
    "single-table", "section-size",
]


def row_count(doc: Document) -> int:
    return len(doc.data.rows)


def declared_or_counted_rows(doc: Document) -> int:
    if doc.data.has_header_row or doc.data.rows:
        return row_count(doc)
    if doc.header.rows is not None:
        return doc.header.rows
    return 0


def meta_map(doc: Document) -> dict[str, str]:
    return {kv.key: kv.value for kv in doc.meta.file_meta}


def _format_header_pair(k: str, v: str) -> str:
    if " " in v:
        return k + '="' + v.replace('"', '""') + '"'
    return k + "=" + v


def _build_canonical_header_line(doc: Document) -> str:
    defaults = {"header": "1", "encoding": "UTF-8"}
    parts: list[str] = []
    emitted: set[str] = set()
    for k in CANONICAL_HEADER_ORDER:
        if k in doc.header.fields:
            v = doc.header.fields[k]
        elif k == "version" and doc.header.version:
            v = doc.header.version
        elif k == "delim" and doc.header.has_magic_line:
            v = doc.header.delim_name
        elif k == "quote" and doc.header.has_magic_line:
            v = doc.header.quote_name
        else:
            continue
        emitted.add(k)
        if v == "":
            continue
        if defaults.get(k) == v:
            continue
        if k == "header" and doc.header.header_row and v == "1":
            continue
        parts.append(_format_header_pair(k, v))
    extra = sorted(k for k in doc.header.fields if k not in emitted)
    for k in extra:
        parts.append(_format_header_pair(k, doc.header.fields[k]))
    if not parts:
        return "#!excsv"
    return "#!excsv " + " ".join(parts)


def serialize_canonical(doc: Document) -> bytes:
    from ._column import format_column_attrs
    from ._pack import format_fk_line, format_pack_table_line

    lines: list[str] = [_build_canonical_header_line(doc)]
    for kv in doc.meta.file_meta:
        lines.append("#@" + kv.key + ": " + kv.value)
    for t in doc.meta.tables:
        lines.append(format_pack_table_line(t))
    for fk in doc.meta.fks:
        lines.append(format_fk_line(fk))
    for col in doc.meta.columns:
        attrs = format_column_attrs(col.attrs)
        lines.append("#column" + (" " + attrs if attrs else ""))
    for s in doc.meta.sql:
        key = s.raw_key or (s.verb + (("-" + s.dialect) if s.dialect else ""))
        lines.append("#$" + key + ": " + s.payload)
    for u in doc.meta.unknown:
        lines.append(u.text)
    d = doc.header.dialect()
    for a in doc.meta.aggregations:
        lines.append("#%" + a.name + ": " + join_csv_fields(a.values, d))
    for line in doc.meta.human_comments:
        lines.append(line)
    layout = doc.header.fields.get("layout", "")
    from ._document import Profile

    if layout not in ("pack", "columnar") and doc.source.profile != Profile.SIDECAR and header_reference(doc.header) == "":
        if doc.data.has_header_row:
            lines.append(join_csv_fields(doc.data.header_row, d))
        for row in doc.data.rows:
            lines.append(join_csv_fields(row, d))
    out = "\n".join(lines)
    if lines:
        out += "\n"
    return out.encode("utf-8", "surrogateescape")


Document.row_count = row_count
Document.declared_or_counted_rows = declared_or_counted_rows
Document.meta_map = meta_map
Document.serialize_canonical = serialize_canonical
