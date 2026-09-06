"""Document.export_json / Pack.export_json: the v0.5 JSON form (implementation/json.md).

The one documented loss is the spec's own: free-text ## comments carry no
structured meaning and have no JSON slot.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from ._column import COLUMN_ATTR_DISPLAY_ORDER, is_virtual_column, parse_attr_int
from ._document import CURRENT_VERSION, Document, Pack, Profile
from ._sidecar_util import header_reference


@dataclass
class JSONExportOptions:
    indent: str = ""


@dataclass
class JSONExportResult:
    data: bytes = b""
    dropped: list[str] = field(default_factory=list)


def export_json(doc: Document, opts: JSONExportOptions | None = None) -> JSONExportResult:
    opts = opts or JSONExportOptions()
    if doc is None:
        raise ValueError("nil document")
    root: dict = {}
    version = doc.header.version or CURRENT_VERSION
    root["excsv"] = version
    root["layout"] = _json_layout(doc).value
    root["csv"] = _json_dialect(doc)
    meta = _json_meta(doc)
    if meta:
        root["meta"] = meta
    _append_table_json(doc, root)

    res = JSONExportResult()
    if doc.meta.human_comments:
        res.dropped.append("## human comments (the spec's one intentional non-round-trip)")
    if doc.meta.unknown:
        res.dropped.append("unrecognized # meta lines (the JSON root schema is closed)")

    res.data = _marshal_json(root, opts.indent)
    return res


def pack_export_json(pack: Pack, opts: JSONExportOptions | None = None) -> JSONExportResult:
    opts = opts or JSONExportOptions()
    if pack is None:
        raise ValueError("nil pack")
    root: dict = {}
    version = CURRENT_VERSION
    if pack.manifest is not None and pack.manifest.header.version:
        version = pack.manifest.header.version
    root["excsv"] = version
    root["layout"] = "pack"
    res = JSONExportResult()
    if pack.manifest is not None:
        meta = _json_meta(pack.manifest)
        if meta:
            root["meta"] = meta
    tables = []
    for t in pack.tables:
        tdoc = t.document()
        entry: dict = {"name": t.decl.name}
        _append_table_json(tdoc, entry)
        tables.append(entry)
        if tdoc.meta.human_comments:
            res.dropped.append("## human comments in table " + t.decl.name)
    root["tables"] = tables
    if pack.fks:
        root["fk"] = [{"from": fk.from_, "to": fk.to} for fk in pack.fks]
    res.data = _marshal_json(root, opts.indent)
    return res


def _append_table_json(doc: Document, into: dict) -> None:
    cols = _json_columns(doc)
    if cols:
        into["columns"] = cols
    sql = _json_sql(doc)
    if sql:
        into["sql"] = sql
    cs = doc.header.fields.get("checksum", "")
    if cs:
        into["checksum"] = cs
    into["rows"] = doc.declared_or_counted_rows()
    agg = _json_aggregates(doc)
    if agg:
        into["aggregates"] = agg
    ref = header_reference(doc.header)
    if ref:
        into["reference"] = ref
        return
    into["data"] = _json_data(doc)


def _json_layout(doc: Document) -> Profile:
    if header_reference(doc.header):
        return Profile.SIDECAR
    return Profile.INLINE


def _json_dialect(doc: Document) -> dict:
    csv: dict = {
        "delim": doc.header.delim_name,
        "quote": doc.header.quote_name,
        "header": doc.header.header_row,
        "encoding": doc.header.encoding or "UTF-8",
    }
    if doc.header.null:
        csv["null"] = doc.header.null
    return csv


def _json_meta(doc: Document) -> dict:
    meta: dict = {}
    for kv in doc.meta.file_meta:
        if kv.key == "tags":
            tags = [p.strip() for p in kv.value.split(",") if p.strip()]
            meta[kv.key] = tags
            continue
        meta[kv.key] = kv.value
    return meta


def _json_columns(doc: Document) -> list:
    from ._warnings import KNOWN_COLUMN_ATTRS

    if not doc.meta.columns:
        return []
    out = []
    phys = 0
    for col in doc.meta.columns:
        entry: dict = {}
        if not is_virtual_column(col):
            idx = phys
            n, ok = parse_attr_int(col.attrs.get("index"))
            if ok:
                idx = n
            entry["index"] = idx
            phys += 1
        col_type = col.attrs.get("type", "")
        for k in COLUMN_ATTR_DISPLAY_ORDER:
            if k == "index" or k not in col.attrs:
                continue
            entry[k] = _json_column_attr(k, col.attrs[k], col_type)
        extra = sorted(k for k in col.attrs if k not in KNOWN_COLUMN_ATTRS)
        for k in extra:
            entry[k] = col.attrs[k]
        out.append(entry)
    return out


def _json_column_attr(key: str, value: str, column_type: str):
    if key in ("unique", "required", "materialized"):
        return value == "1"
    if key == "enum":
        return [_json_scalar(p, column_type) for p in value.split("|")]
    if key in ("len_min", "len_max"):
        n, ok = parse_attr_int(value)
        if ok:
            return n
    return value


def _json_sql(doc: Document) -> dict:
    sql: dict = {}
    by_verb: dict[str, list] = {}
    verbs: list[str] = []
    for stmt in doc.meta.sql:
        entry: dict = {}
        if stmt.qualified and stmt.dialect:
            entry["dialect"] = stmt.dialect
            if stmt.version:
                entry["version"] = stmt.version
        entry["stmt"] = stmt.payload
        if stmt.verb not in by_verb:
            verbs.append(stmt.verb)
            by_verb[stmt.verb] = []
        by_verb[stmt.verb].append(entry)
    for verb in verbs:
        sql[verb] = by_verb[verb]
    return sql


def _json_aggregates(doc: Document) -> dict:
    from ._agg import column_type_at

    agg: dict = {}
    for a in doc.meta.aggregations:
        values = []
        for i, v in enumerate(a.values):
            if v == "":
                values.append(None)
                continue
            values.append(_json_agg_value(a.name, v, column_type_at(doc, i)))
        agg[a.name] = values
    return agg


def _json_agg_value(name: str, value: str, column_type: str):
    if name in ("count_nonnull", "count_null", "count_distinct", "len_min", "len_max"):
        try:
            return int(value.strip())
        except ValueError:
            return value
    return _json_scalar(value, column_type)


def _json_data(doc: Document) -> list:
    from ._agg import cell_at, column_type_at, is_null_cell

    width = doc.column_width()
    rows = []
    for row in doc.data.rows:
        n = width or len(row)
        cells = []
        for i in range(n):
            raw = cell_at(doc, row, i)
            if is_null_cell(doc, raw):
                cells.append(None)
                continue
            cells.append(_json_scalar(raw, column_type_at(doc, i)))
        rows.append(cells)
    return rows


def _json_scalar(raw: str, column_type: str):
    """Encodes one cell. decimal and long stay strings: the column type is
    authoritative and JSON numbers would silently lose precision."""
    ct = (column_type or "").strip().lower()
    if ct == "boolean":
        low = raw.strip().lower()
        if low in ("true", "1"):
            return True
        if low in ("false", "0"):
            return False
        return raw
    if ct == "int":
        try:
            n = int(raw.strip(), 10)
            if str(n) == raw:
                return n
        except ValueError:
            pass
    elif ct in ("float", "double", "number"):
        try:
            f = float(raw.strip())
            if _float_round_trips(f, raw):
                return f
        except ValueError:
            pass
    return raw


def _float_round_trips(f: float, raw: str) -> bool:
    """A JSON number is only safe when it reproduces the cell exactly: 500.00
    must not come back as 500, or the round-trip stops being one."""
    s = repr(f)
    if "e" in s or "E" in s:
        from decimal import Decimal

        s = format(Decimal(s), "f")
    return s == raw


def _marshal_json(v, indent: str) -> bytes:
    if indent:
        text = json.dumps(v, indent=indent, ensure_ascii=False)
    else:
        text = json.dumps(v, separators=(",", ":"), ensure_ascii=False)
    return (text + "\n").encode("utf-8")


Document.export_json = export_json
Pack.export_json = pack_export_json
