"""Reading the JSON form (implementation/json.md § Reading).

A JSON-form document is rendered as the equivalent canonical text document and
parsed as text, so it gets exactly the checks and warning codes of its text
twin; problems that only exist in JSON fail with json_malformed.
"""

from __future__ import annotations

import json

from ._chart import chart_meta_line
from ._column import format_column_attrs
from ._csv import join_csv_fields
from ._dialect import resolve_delim, resolve_quote
from ._document import Aggregation, Chart, Dialect
from ._errors import ErrorKind, ParseError, fail
from ._notes import _format_anchored_line
from ._serialize import CANONICAL_HEADER_ORDER, _format_header_pair


class _Num(str):
    """A JSON number, kept in its exact lexical form."""


def is_json_form(data: bytes) -> bool:
    """Whether data is the JSON form: a top-level JSON object with an "excsv"
    member. Dispatch is on content, not on the file name."""
    trimmed = data.lstrip(b" \t\r\n")
    if not trimmed.startswith(b"{"):
        return False
    # A text document cannot start with "{" and name an "excsv" member near
    # the top, so a broken JSON document still reaches json_to_text and fails
    # with json_malformed instead of being read as CSV.
    return b'"excsv"' in trimmed[:4096]


def json_to_text(data: bytes) -> bytes:
    """Renders a JSON-form document (.excsv.json) as canonical text."""
    try:
        root = json.loads(
            data.decode("utf-8"), parse_int=_Num, parse_float=_Num, parse_constant=_reject_constant,
        )
    except (ValueError, UnicodeDecodeError) as e:
        raise _fail("not valid JSON: " + str(e)) from None
    if not isinstance(root, dict):
        raise _fail("top level is not an object")
    if "excsv" not in root:
        raise _fail('no "excsv" member')
    if "tables" in root:
        raise _fail("pack documents (tables) cannot be read from the JSON form yet")
    return _Writer(root).render().encode("utf-8")


def _reject_constant(name: str):
    raise ValueError(name + " is not a JSON number")


def _fail(msg: str) -> ParseError:
    return fail(ErrorKind.JSON_MALFORMED, 0, msg)


class _Column:
    def __init__(self, attrs: dict, index: int = -1, virtual: bool = False):
        self.attrs = attrs
        self.index = index
        self.virtual = virtual


class _Writer:
    def __init__(self, root: dict):
        self.root = root
        self.dialect = Dialect()
        self.header = True

    def render(self) -> str:
        version = _string(self.root["excsv"], "excsv")
        csv_opts = _object(self.root.get("csv"), "csv")
        fields = {"version": version}
        delim_name, quote_name = "comma", ""
        if csv_opts is not None:
            if "delim" in csv_opts:
                delim_name = _string(csv_opts["delim"], "csv.delim")
            if "quote" in csv_opts:
                quote_name = _string(csv_opts["quote"], "csv.quote")
            if "header" in csv_opts:
                if not isinstance(csv_opts["header"], bool):
                    raise _fail("csv.header is not a boolean")
                self.header = csv_opts["header"]
            if "null" in csv_opts:
                marker = _null_marker(csv_opts["null"])
                if marker:
                    fields["null"] = marker
            if "encoding" in csv_opts:
                enc = _string(csv_opts["encoding"], "csv.encoding")
                if enc.upper() != "UTF-8":
                    fields["encoding"] = enc
        if not self.header:
            fields["header"] = "0"

        columns = self.columns()
        width = max((c.index + 1 for c in columns if not c.virtual), default=0)
        has_ref = "reference" in self.root
        rows: list[list[str]] = []
        if has_ref:
            fields["reference"] = _string(self.root["reference"], "reference")
        else:
            rows = self.data()
        if width == 0 and rows:
            width = len(rows[0])
        header_row = self.header_row(columns, width)
        aggs = self.aggregates()

        # Pick the dialect: csv.delim, and csv.quote unless a value needs
        # quoting that quote=none cannot give it.
        try:
            delim = resolve_delim(delim_name)
        except ParseError as e:
            raise _fail("csv.delim: " + e.issue.message) from None
        if quote_name in ("", "none"):
            quote_name = "none"
            if _needs_quoting(delim, fields.get("null", ""), header_row, rows, aggs):
                quote_name = "double"
        try:
            quote, quote_on = resolve_quote(quote_name)
        except ParseError as e:
            raise _fail("csv.quote: " + e.issue.message) from None
        self.dialect = Dialect(delim=delim, quote=quote, quote_enabled=quote_on)
        fields["delim"], fields["quote"] = delim_name, quote_name

        if "rows" in self.root:
            fields["rows"] = str(_int(self.root["rows"], "rows"))
        elif not has_ref:
            fields["rows"] = str(len(rows))
        if "column_count" in self.root:
            fields["columns"] = str(_int(self.root["column_count"], "column_count"))
        if "checksum" in self.root:
            fields["checksum"] = _string(self.root["checksum"], "checksum")

        order = [k for k in CANONICAL_HEADER_ORDER if k in fields]
        order += sorted(k for k in fields if k not in CANONICAL_HEADER_ORDER)
        out = ["#!excsv" + "".join(" " + _format_header_pair(k, fields[k]) for k in order)]
        out += self.meta_lines()
        for c in columns:
            line = self.column_line(c)
            if line:
                out.append(line)
        out += self.chart_lines()
        out += self.anchored_lines("notes")
        out += self.anchored_lines("links")
        out += self.sql_lines()
        for a in aggs:
            out.append("#%" + a.name + ": " + join_csv_fields(a.values, self.dialect))
        if not has_ref:
            if self.header:
                out.append(join_row(header_row, self.dialect))
            for row in rows:
                line = join_row(row, self.dialect)
                if line == "" and len(row) == 1:
                    # A lone null would be a blank line, which is no row at all.
                    line = fields.get("null") or '""'
                out.append(line)
        return "\n".join(out) + "\n"

    def columns(self) -> list[_Column]:
        """columns[]: physical columns sorted by index, then virtual ones in
        array order."""
        phys: list[_Column] = []
        virt: list[_Column] = []
        seen: set[int] = set()
        for i, obj in enumerate(_array(self.root.get("columns"), "columns")):
            if not isinstance(obj, dict):
                raise _fail(f"columns[{i}] is not an object")
            virtual = obj.get("formula") is not None and obj.get("materialized") is not True
            if virtual:
                if "index" in obj:
                    raise _fail(f"columns[{i}]: a virtual computed column has no index")
                virt.append(_Column(obj, virtual=True))
                continue
            if "index" not in obj:
                raise _fail(f"columns[{i}]: a physical column needs index")
            idx = _int(obj["index"], f"columns[{i}].index")
            if idx < 0 or idx in seen:
                raise _fail(f"columns[{i}]: index {idx} is negative or repeated")
            seen.add(idx)
            phys.append(_Column(obj, index=idx))
        phys.sort(key=lambda c: c.index)
        return phys + virt

    def header_row(self, columns: list[_Column], width: int) -> list[str]:
        """Names each physical position: title, else name."""
        if not self.header:
            return []
        row = [""] * width
        for c in columns:
            if c.virtual or c.index >= width:
                continue
            for k in ("title", "name"):
                if k in c.attrs:
                    row[c.index] = _string(c.attrs[k], "columns." + k)
                    break
        for i, cell in enumerate(row):
            if cell == "":
                raise _fail(f"column {i} has no name, but csv.header is true")
        return row

    def column_line(self, c: _Column) -> str:
        """One column object as a #column line, or "" for a bare object (index
        and name only), which carries no schema."""
        attrs = {k: _column_attr_text(k, v) for k, v in c.attrs.items() if k != "index"}
        if not attrs or (self.header and len(attrs) == 1 and attrs.get("name")):
            return ""
        if not self.header and not c.virtual:
            attrs["index"] = str(c.index)
        return "#column " + format_column_attrs(attrs)

    def meta_lines(self) -> list[str]:
        meta = _object(self.root.get("meta"), "meta")
        out = []
        for k, v in (meta or {}).items():
            if k == "tags" and isinstance(v, list):
                s = ",".join(_scalar_text(p, "meta.tags") for p in v)
            else:
                s = _scalar_text(v, "meta." + k)
            if "\r" in s or "\n" in s:
                raise _fail("meta." + k + " contains a line break")
            out.append("#@" + k + ": " + s)
        return out

    def chart_lines(self) -> list[str]:
        out = []
        for i, obj in enumerate(_array(self.root.get("charts"), "charts")):
            if not isinstance(obj, dict):
                raise _fail(f"charts[{i}] is not an object")
            keys = list(obj)
            # {"<engine>": payload} is a #chart-<engine>: escape line.
            if len(keys) == 1 and keys[0] != "type":
                payload = obj[keys[0]]
                text = payload if isinstance(payload, str) and not isinstance(payload, _Num) else _dump(payload)
                out.append(chart_meta_line(Chart(engine=keys[0], payload=text)))
                continue
            attrs = {k: _chart_attr_text(k, v) for k, v in obj.items()}
            out.append(chart_meta_line(Chart(attrs=attrs)))
        return out

    def anchored_lines(self, kind: str) -> list[str]:
        prefix, quoted = ("#link", "href") if kind == "links" else ("#note", "text")
        out = []
        for i, obj in enumerate(_array(self.root.get(kind), kind)):
            if not isinstance(obj, dict):
                raise _fail(f"{kind}[{i}] is not an object")
            attrs = {}
            for k, v in obj.items():
                s = _scalar_text(v, kind + "." + k)
                if "\r" in s or "\n" in s:
                    raise _fail(f"{kind}[{i}].{k} contains a line break")
                attrs[k] = s
            out.append(_format_anchored_line(prefix, attrs, quoted))
        return out

    def sql_lines(self) -> list[str]:
        sql = _object(self.root.get("sql"), "sql")
        out = []
        for verb, items in (sql or {}).items():
            for i, obj in enumerate(_array(items, "sql." + verb)):
                if not isinstance(obj, dict):
                    raise _fail(f"sql.{verb}[{i}] is not an object")
                key = verb
                for part in ("dialect", "version"):
                    if part in obj:
                        key += "-" + _string(obj[part], f"sql.{verb}.{part}")
                stmt = _string(obj.get("stmt"), f"sql.{verb}.stmt")
                if "\r" in stmt or "\n" in stmt:
                    raise _fail("sql." + verb + " statement contains a line break")
                out.append("#$" + key + ": " + stmt)
        return out

    def aggregates(self) -> list[Aggregation]:
        agg = _object(self.root.get("aggregates"), "aggregates")
        out = []
        for name, values in (agg or {}).items():
            vals = [_scalar_text(v, "aggregates." + name) for v in _array(values, "aggregates." + name)]
            out.append(Aggregation(name=name, values=vals))
        return out

    def data(self) -> list[list[str]]:
        rows = []
        for r, cells in enumerate(_array(self.root.get("data"), "data")):
            if not isinstance(cells, list):
                raise _fail(f"data[{r}] is not an array")
            row = []
            for i, v in enumerate(cells):
                s = _scalar_text(v, f"data[{r}][{i}]")
                if "\r" in s or "\n" in s:
                    raise _fail(f"data[{r}][{i}] contains a line break; text rows are single-line")
                row.append(s)
            rows.append(row)
        return rows


def _needs_quoting(delim: str, null_marker: str, header: list[str], rows: list[list[str]], aggs: list[Aggregation]) -> bool:
    """Whether some value cannot be written with quote=none: it contains the
    delimiter, starts a row with "#", or is the lone null of a one-column row
    with no null= marker to stand for it (written as "")."""

    def check(fields: list[str]) -> bool:
        if len(fields) == 1 and fields[0] == "" and not null_marker:
            return True
        return any(delim in f or (i == 0 and f.startswith("#")) for i, f in enumerate(fields))

    if check(header) or any(check(r) for r in rows):
        return True
    return any(delim in v for a in aggs for v in a.values)


def join_row(fields: list[str], d: Dialect) -> str:
    """Renders one data or header record. A first field starting with "#" is
    quoted so the line is not read as a meta line."""
    line = join_csv_fields(fields, d)
    if d.quote_enabled and fields and fields[0].startswith("#") and not line.startswith(d.quote):
        q = d.quote
        line = q + fields[0].replace(q, q + q) + q + line[len(fields[0]):]
    return line


def _column_attr_text(key: str, v) -> str:
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, list):
        if key != "enum":
            raise _fail("columns." + key + " is an array")
        return "|".join(_scalar_text(p, "columns.enum") for p in v)
    return _scalar_text(v, "columns." + key)


def _chart_attr_text(key: str, v) -> str:
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, list):
        return ",".join(_scalar_text(p, "charts." + key) for p in v)
    return _scalar_text(v, "charts." + key)


def _null_marker(v) -> str:
    if isinstance(v, str) and not isinstance(v, _Num):
        return v
    if isinstance(v, list):
        return _string(v[0], "csv.null") if v else ""
    raise _fail("csv.null is neither a string nor an array of strings")


def _scalar_text(v, what: str) -> str:
    """A JSON scalar as its text-form value; numbers keep their exact lexical
    form."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if v is None:
        return ""
    if isinstance(v, str):
        return str(v)
    raise _fail(what + " is not a scalar")


def _string(v, what: str) -> str:
    if not isinstance(v, str) or isinstance(v, _Num):
        raise _fail(what + " is not a string")
    return v


def _int(v, what: str) -> int:
    if not isinstance(v, _Num):
        raise _fail(what + " is not a number")
    try:
        return int(v)
    except ValueError:
        raise _fail(what + " is not an integer") from None


def _object(v, what: str) -> dict | None:
    if v is None:
        return None
    if not isinstance(v, dict):
        raise _fail(what + " is not an object")
    return v


def _array(v, what: str) -> list:
    if v is None:
        return []
    if not isinstance(v, list):
        raise _fail(what + " is not an array")
    return v


def _dump(v) -> str:
    """Compact JSON for a chart payload, numbers in their lexical form."""
    if isinstance(v, _Num):
        return str(v)
    if isinstance(v, dict):
        return "{" + ",".join(json.dumps(k, ensure_ascii=False) + ":" + _dump(x) for k, x in v.items()) + "}"
    if isinstance(v, list):
        return "[" + ",".join(_dump(x) for x in v) + "]"
    return json.dumps(v, ensure_ascii=False)
