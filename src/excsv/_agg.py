"""Standard #% aggregations (sum/avg/min/max/count_*/len_*) and cell helpers."""

from __future__ import annotations

from ._document import ColumnDef, Document

STANDARD_AGGREGATIONS = {
    "count_nonnull", "count_null", "count_distinct",
    "sum", "avg", "min", "max", "len_min", "len_max",
}

ALL_AGGREGATIONS = [
    "count_nonnull", "count_null", "count_distinct",
    "sum", "avg", "min", "max", "len_min", "len_max",
]


def cell_at(doc: Document, row: list[str], col: int) -> str:
    if col >= len(row):
        return ""
    return row[col]


def is_null_cell(doc: Document, s: str) -> bool:
    if s == "":
        return True
    if doc.header.null and s == doc.header.null:
        return True
    return False


def data_rows(doc: Document) -> list[list[str]]:
    return doc.data.rows


def column_type_at(doc: Document, col: int) -> str:
    d, ok = doc.column_def_at(col)
    if not ok:
        return ""
    return d.attrs.get("type", "").strip().lower()


def is_measure_column_type(t: str) -> bool:
    return t in ("int", "long", "float", "double", "decimal", "number")


def is_string_column_type(t: str) -> bool:
    if t in ("string", "text"):
        return True
    return t == ""


def column_role_at(doc: Document, col: int) -> str:
    d, ok = doc.column_def_at(col)
    if not ok:
        return ""
    return d.attrs.get("role", "").strip().lower()


def column_agg_hint_at(doc: Document, col: int) -> str:
    d, ok = doc.column_def_at(col)
    if not ok:
        return ""
    return d.attrs.get("agg", "").strip().lower()


def numeric_agg_applies(doc: Document, col: int) -> bool:
    if column_role_at(doc, col) == "id":
        return False
    if column_agg_hint_at(doc, col) == "none":
        return False
    ct = column_type_at(doc, col)
    if ct in ("float", "double", "decimal", "number"):
        return True
    if ct in ("int", "long"):
        return column_role_at(doc, col) == "measure"
    return False


def column_count_for_agg(doc: Document) -> int:
    from ._column import effective_column_count

    width = len(doc.data.header_row) if doc.data.has_header_row else 0
    return effective_column_count(doc, width)


def compute_aggregation_values(doc: Document, name: str) -> list[str]:
    if name not in STANDARD_AGGREGATIONS:
        raise ValueError(f"unknown aggregation: {name}")
    n = column_count_for_agg(doc)
    if n == 0:
        raise ValueError("no columns to aggregate")
    return [compute_agg_column(doc, name, col) for col in range(n)]


def compute_agg_column(doc: Document, name: str, col: int) -> str:
    ct = column_type_at(doc, col)
    if name == "count_nonnull":
        return str(_count_nonnull(doc, col))
    if name == "count_null":
        return str(_count_null(doc, col))
    if name == "count_distinct":
        return str(_count_distinct(doc, col))
    if name in ("sum", "avg", "min", "max"):
        if not numeric_agg_applies(doc, col):
            return ""
        return _compute_numeric_agg(doc, name, col)
    if name in ("len_min", "len_max"):
        if not is_string_column_type(ct):
            return ""
        return _compute_len_agg(doc, name, col)
    raise ValueError(f"unknown aggregation: {name}")


def _count_nonnull(doc: Document, col: int) -> int:
    return sum(1 for row in data_rows(doc) if not is_null_cell(doc, cell_at(doc, row, col)))


def _count_null(doc: Document, col: int) -> int:
    return sum(1 for row in data_rows(doc) if is_null_cell(doc, cell_at(doc, row, col)))


def _count_distinct(doc: Document, col: int) -> int:
    seen = set()
    for row in data_rows(doc):
        v = cell_at(doc, row, col)
        if is_null_cell(doc, v):
            continue
        seen.add(v)
    return len(seen)


def _compute_numeric_agg(doc: Document, name: str, col: int) -> str:
    total = 0.0
    count = 0
    min_v = float("inf")
    max_v = float("-inf")
    for row in data_rows(doc):
        v = cell_at(doc, row, col)
        if is_null_cell(doc, v):
            continue
        try:
            f = float(v.strip())
        except ValueError:
            raise ValueError(f"non-numeric value {v!r} in column {col}")
        total += f
        count += 1
        if f < min_v:
            min_v = f
        if f > max_v:
            max_v = f
    if count == 0:
        return ""
    if name == "sum":
        return format_decimal(total)
    if name == "avg":
        return format_decimal(total / count)
    if name == "min":
        return format_decimal(min_v)
    if name == "max":
        return format_decimal(max_v)
    return ""


def _compute_len_agg(doc: Document, name: str, col: int) -> str:
    min_len = -1
    max_len = -1
    for row in data_rows(doc):
        v = cell_at(doc, row, col)
        if is_null_cell(doc, v):
            continue
        length = len(v.encode("utf-8"))
        if min_len < 0 or length < min_len:
            min_len = length
        if length > max_len:
            max_len = length
    if min_len < 0:
        return ""
    return str(min_len if name == "len_min" else max_len)


def format_decimal(f: float) -> str:
    from decimal import Decimal

    s = repr(f)
    if "e" in s or "E" in s:
        s = format(Decimal(s), "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    if "." not in s:
        return s + ".00"
    parts = s.split(".", 1)
    if len(parts[1]) == 1:
        return s + "0"
    return s
