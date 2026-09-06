"""Parses the delimited data block (optional header row + body rows)."""

from __future__ import annotations

from ._csv import split_csv_fields
from ._document import DataSection, Dialect
from ._errors import Issue, ParseError
from ._records import Record, extract_data_section, trim_trailing_empty_records
from ._rowparse import first_field_hash_error, parse_csv_row, row_arity_error, wrap_line_err


def build_data_section(
    records: list[Record],
    full: str,
    d: Dialect,
    has_header: bool,
    quote_name: str,
    schema_col_count: int,
    section_start: int,
    strict: bool,
) -> tuple[DataSection, str, int, list[Issue]]:
    records = trim_trailing_empty_records(records)
    ds = DataSection()
    warnings: list[Issue] = []
    row_start = 0
    col_count = schema_col_count

    if has_header and records and records[0].text != "":
        try:
            fields = split_csv_fields(records[0].text, d)
        except ParseError as e:
            raise wrap_line_err(e, records[0].num)
        first_field_hash_error(records[0].text, fields, d, records[0].num)
        ds.has_header_row = True
        ds.header_row = fields
        col_count = len(fields)
        row_start = 1

    expected_cols = col_count
    for i in range(row_start, len(records)):
        if records[i].text == "":
            continue
        if strict:
            try:
                fields = split_csv_fields(records[i].text, d)
            except ParseError as e:
                raise wrap_line_err(e, records[i].num)
            first_field_hash_error(records[i].text, fields, d, records[i].num)
            if expected_cols > 0:
                err = row_arity_error(fields, records[i].num, d, expected_cols, quote_name)
                if err is not None:
                    raise err
            elif fields:
                expected_cols = len(fields)
            row = fields
        else:
            row, w = parse_csv_row(records[i].text, records[i].num, d, expected_cols, quote_name, False)
            warnings.extend(w)
            if expected_cols == 0 and row:
                expected_cols = len(row)
        ds.rows.append(row)

    if not has_header and col_count == 0 and expected_cols > 0:
        col_count = expected_cols
    section = extract_data_section(full, records, section_start)
    return ds, section, col_count, warnings
