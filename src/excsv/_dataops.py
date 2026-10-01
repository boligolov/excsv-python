"""Row-level mutation: SyncDerived, AppendRows, SortRows, cell comparison."""

from __future__ import annotations

import functools
from dataclasses import dataclass
from fractions import Fraction

from ._agg import STANDARD_AGGREGATIONS, cell_at, column_type_at, is_null_cell
from ._document import Document
from ._errors import ErrorKind, fail


@dataclass
class SortKey:
    index: int
    desc: bool = False


def sync_derived(doc: Document) -> None:
    n = doc.row_count()
    doc.header.fields["rows"] = str(n)
    doc.header.rows = n
    if doc.header.checksum is not None:
        alg = doc.header.checksum.algorithm or "sha256"
        doc.set_data_checksum(alg)
    from ._agg import compute_aggregation_values

    for a in doc.meta.aggregations:
        if a.name not in STANDARD_AGGREGATIONS:
            continue
        a.values = compute_aggregation_values(doc, a.name)


def pad_or_trim(row: list[str], width: int) -> list[str]:
    if len(row) == width:
        return row
    if len(row) > width:
        return row[:width]
    return row + [""] * (width - len(row))


def append_rows(doc: Document, rows: list[list[str]], strict: bool) -> None:
    width = doc.column_width()
    out = []
    for i, row in enumerate(rows):
        if width == 0 and row:
            width = len(row)
        if width > 0 and len(row) != width:
            if strict:
                raise fail(ErrorKind.DATA_ROW_ARITY_MISMATCH, i + 1, f"row has {len(row)} fields, expected {width}")
            row = pad_or_trim(row, width)
        out.append(list(row))
    doc.data.rows.extend(out)
    sync_derived(doc)


def sort_rows(doc: Document, keys: list[SortKey]) -> None:
    if not keys:
        raise ValueError("at least one sort key is required")
    width = doc.column_width()
    for k in keys:
        if k.index < 0 or (width > 0 and k.index >= width):
            raise ValueError(f"column index out of range: {k.index}")

    def cmp(a: list[str], b: list[str]) -> int:
        for key in keys:
            c = compare_cells(doc, key.index, cell_at(doc, a, key.index), cell_at(doc, b, key.index))
            if c == 0:
                continue
            return -c if key.desc else c
        return 0

    # Sort a permutation rather than the rows themselves, so #note / #link
    # row= anchors can follow their rows (notes.md § Writer obligations).
    from ._notes import anchor_resolver, remap_row_anchors

    anchors = anchor_resolver(doc)
    rows = doc.data.rows
    order = sorted(range(len(rows)), key=functools.cmp_to_key(lambda i, j: cmp(rows[i], rows[j])))
    new_index = [0] * len(order)
    for to, src in enumerate(order):
        new_index[src] = to
    doc.data.rows = [rows[i] for i in order]
    remap_row_anchors(doc, anchors, new_index)
    sync_derived(doc)


def compare_cells(doc: Document, col: int, a: str, b: str) -> int:
    a_null = is_null_cell(doc, a)
    b_null = is_null_cell(doc, b)
    if a_null and b_null:
        return 0
    if a_null:
        return 1
    if b_null:
        return -1
    ct = column_type_at(doc, col)
    if ct in ("int", "long", "float", "double", "decimal", "number"):
        fa = _to_fraction(a)
        fb = _to_fraction(b)
        if fa is not None and fb is not None:
            if fa < fb:
                return -1
            if fa > fb:
                return 1
            return 0
    elif ct in ("date", "time", "datetime"):
        if a != b:
            return -1 if a < b else 1
        return 0
    elif ct == "boolean":
        return bool_order(a) - bool_order(b)
    return -1 if a < b else (1 if a > b else 0)


def _to_fraction(s: str):
    try:
        return Fraction(s.strip())
    except (ValueError, ZeroDivisionError):
        return None


def bool_order(s: str) -> int:
    return 0 if s.strip().lower() in ("0", "false") else 1


Document.sync_derived = sync_derived
Document.append_rows = append_rows
Document.sort_rows = sort_rows
