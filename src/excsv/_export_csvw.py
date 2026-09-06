"""Document.export_csvw / Pack.export_csvw: a CSVW metadata sidecar (write-only).

Nothing reads CSVW in this library and nothing stores it: this is a one-way
boundary format. There is no importer, by design.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ._column import parse_attr_int
from ._document import Document, Pack
from ._export_json import _marshal_json
from ._sidecar_util import header_reference

CSVW_TYPE_MAP = {
    "string": "string", "int": "int", "long": "long", "float": "float",
    "double": "double", "decimal": "decimal", "boolean": "boolean",
    "date": "date", "time": "time", "datetime": "dateTime",
    "uuid": "string", "binary": "base64Binary",
}

CSVW_UNCARRIED = ["unit", "role", "agg", "order", "regexp_dialect"]

_REGEXP_META = r"\.+*?()|[]{}^$"


@dataclass
class CSVWExportOptions:
    url: str = ""
    enum_as_pattern: bool = False
    table: str = ""
    indent: str = ""


@dataclass
class CSVWExportResult:
    data: bytes = b""
    dropped: list[str] = field(default_factory=list)

    def drop(self, msg: str) -> None:
        if msg not in self.dropped:
            self.dropped.append(msg)

    def sorted_dropped(self) -> list[str]:
        return sorted(self.dropped)


def export_csvw(doc: Document, opts: CSVWExportOptions | None = None) -> CSVWExportResult:
    opts = opts or CSVWExportOptions()
    if doc is None:
        raise ValueError("nil document")
    url = opts.url.strip() or header_reference(doc.header)
    if not url:
        raise ValueError(
            "url is required: an inline ExCSV document is not a CSV file, so CSVW has no url to point at"
        )
    res = CSVWExportResult()
    root: dict = {"@context": "http://www.w3.org/ns/csvw"}
    _apply_csvw_meta(doc, root, res)
    root["url"] = url
    dialect = _csvw_dialect(doc)
    if dialect:
        root["dialect"] = dialect
    root["tableSchema"] = _csvw_table_schema(doc, opts, res)
    res.data = _marshal_json(root, opts.indent)
    return res


def pack_export_csvw(pack: Pack, opts: CSVWExportOptions | None = None) -> CSVWExportResult:
    opts = opts or CSVWExportOptions()
    if pack is None:
        raise ValueError("nil pack")
    res = CSVWExportResult()
    root: dict = {"@context": "http://www.w3.org/ns/csvw"}
    tables = []
    for t in pack.tables:
        name = t.decl.name
        if opts.table and opts.table != name:
            continue
        tdoc = t.document()
        url = opts.url.strip() or (name + ".csv")
        entry = {
            "url": url,
            "dc:title": name,
            "tableSchema": _csvw_table_schema(tdoc, opts, res),
        }
        tables.append(entry)
    if not tables:
        raise ValueError("no tables to export")
    root["tables"] = tables
    res.data = _marshal_json(root, opts.indent)
    return res


def _apply_csvw_meta(doc: Document, root: dict, res: CSVWExportResult) -> None:
    dublin_core = {
        "title": "dc:title", "description": "dc:description", "comment": "dc:description",
        "author": "dc:creator", "license": "dc:license", "created": "dc:created",
    }
    dropped = []
    for kv in doc.meta.file_meta:
        target = dublin_core.get(kv.key)
        if target is None:
            dropped.append("#@" + kv.key)
            continue
        root[target] = kv.value
    if dropped:
        res.drop("no Dublin Core counterpart: " + ", ".join(dropped))
    if doc.meta.aggregations:
        res.drop("#% aggregations have no CSVW analogue")
    if doc.meta.sql:
        res.drop("#$ SQL companions have no CSVW analogue")


def _csvw_dialect(doc: Document) -> dict:
    d: dict = {}
    if doc.header.delim:
        d["delimiter"] = doc.header.delim
    d["header"] = doc.header.header_row
    if doc.header.quote_enabled:
        d["quoteChar"] = doc.header.quote
    else:
        d["quoteChar"] = None
    if doc.header.encoding:
        d["encoding"] = doc.header.encoding
    return d


def _csvw_table_schema(doc: Document, opts: CSVWExportOptions, res: CSVWExportResult) -> dict:
    return {"columns": [_csvw_column(doc, col, opts, res) for col in doc.meta.columns]}


def _csvw_column(doc: Document, col, opts: CSVWExportOptions, res: CSVWExportResult) -> dict:
    out: dict = {}
    label = col.attrs.get("name") or col.attrs.get("index", "")
    if col.attrs.get("name"):
        out["name"] = col.attrs["name"]
    if col.attrs.get("title"):
        out["titles"] = col.attrs["title"]
    if col.attrs.get("description"):
        out["dc:description"] = col.attrs["description"]

    datatype: dict = {}
    raw_type = col.attrs.get("type", "").strip().lower()
    if raw_type:
        base = CSVW_TYPE_MAP.get(raw_type)
        if base is None:
            base = "string"
            res.drop(f"column {label}: unknown type={raw_type} widened to xsd:string")
        datatype["base"] = base
        if raw_type == "uuid":
            datatype["format"] = "[0-9a-fA-F-]{36}"
            res.drop(f"column {label}: type=uuid approximated as xsd:string with a format regex (XSD has no UUID type)")
        elif raw_type == "binary":
            res.drop(f"column {label}: type=binary approximated as xsd:base64Binary")
    if col.attrs.get("min"):
        datatype["minimum"] = col.attrs["min"]
    if col.attrs.get("max"):
        datatype["maximum"] = col.attrs["max"]
    n, ok = parse_attr_int(col.attrs.get("len_min"))
    if ok:
        datatype["minLength"] = n
    n, ok = parse_attr_int(col.attrs.get("len_max"))
    if ok:
        datatype["maxLength"] = n
    if col.attrs.get("pattern"):
        datatype["format"] = col.attrs["pattern"]
    enum = col.attrs.get("enum", "")
    if enum:
        if opts.enum_as_pattern:
            quoted = [_regexp_quote_meta(p) for p in enum.split("|")]
            datatype["format"] = "^(?:" + "|".join(quoted) + ")$"
        else:
            res.drop(f"column {label}: enum= dropped (CSVW has no enumeration facet; "
                      "--enum-as-pattern encodes it as a regex)")
    if datatype:
        out["datatype"] = datatype

    if col.attrs.get("required") == "1":
        out["required"] = True
    if col.attrs.get("default"):
        out["default"] = col.attrs["default"]
    if col.attrs.get("null"):
        out["null"] = col.attrs["null"]
    if col.attrs.get("separator"):
        out["separator"] = col.attrs["separator"]

    if col.attrs.get("unique") == "1":
        res.drop(f"column {label}: unique= dropped (CSVW primaryKey asserts a weaker, combination-wide constraint)")
    for attr in CSVW_UNCARRIED:
        if col.attrs.get(attr):
            res.drop(f"column {label}: {attr}= has no CSVW counterpart")
    return out


def _regexp_quote_meta(s: str) -> str:
    out = []
    for ch in s:
        if ch in _REGEXP_META:
            out.append("\\")
        out.append(ch)
    return "".join(out)


Document.export_csvw = export_csvw
Pack.export_csvw = pack_export_csvw
