"""Single-row CSV parsing with strict/lenient arity handling."""

from __future__ import annotations

from ._csv import split_csv_fields
from ._document import Dialect
from ._errors import ErrorKind, Issue, ParseError, fail, new_issue


def is_first_field_quoted(line: str, d: Dialect) -> bool:
    if not d.quote_enabled:
        return False
    return line.strip().startswith(d.quote)


def first_field_hash_error(line: str, fields: list[str], d: Dialect, line_no: int) -> None:
    if not fields:
        return
    if not d.quote_enabled and fields[0].startswith("#") and not is_first_field_quoted(line, d):
        raise fail(ErrorKind.FIRST_FIELD_HASH_UNQUOTED, line_no, "first field starts with # unquoted")


def fmt_row_arity(got: int, want: int) -> str:
    return f"row has {got} fields, expected {want}"


def row_arity_error(fields: list[str], line_no: int, d: Dialect, expected_cols: int, quote_name: str):
    if expected_cols <= 0 or len(fields) == expected_cols:
        return None
    if not d.quote_enabled and len(fields) > expected_cols and quote_name == "none":
        return fail(ErrorKind.QUOTE_NONE_DELIMITER_IN_VALUE, line_no, "delimiter in unquoted value")
    return fail(ErrorKind.DATA_ROW_ARITY_MISMATCH, line_no, fmt_row_arity(len(fields), expected_cols))


def parse_csv_row(
    line: str, line_no: int, d: Dialect, expected_cols: int, quote_name: str, strict: bool
) -> tuple[list[str], list[Issue]]:
    """Parses one data line; when strict is False, pads/truncates to expected_cols with warnings."""
    try:
        fields = split_csv_fields(line, d)
    except ParseError as e:
        e.issue.line = line_no
        raise
    first_field_hash_error(line, fields, d, line_no)
    if expected_cols == 0:
        return fields, []
    if len(fields) == expected_cols:
        return fields, []
    if strict:
        err = row_arity_error(fields, line_no, d, expected_cols, quote_name)
        if err is not None:
            raise err
        return fields, []
    warnings: list[Issue] = []
    if len(fields) < expected_cols:
        warnings.append(new_issue(
            ErrorKind.DATA_ROW_ARITY_MISMATCH, line_no,
            f"padded row from {len(fields)} to {expected_cols} fields",
        ))
        fields = fields + [""] * (expected_cols - len(fields))
    else:
        warnings.append(new_issue(
            ErrorKind.DATA_ROW_ARITY_MISMATCH, line_no,
            f"truncated row from {len(fields)} to {expected_cols} fields",
        ))
        fields = fields[:expected_cols]
    return fields, warnings


def wrap_line_err(e: ParseError, line: int) -> ParseError:
    e.issue.line = line
    return e
