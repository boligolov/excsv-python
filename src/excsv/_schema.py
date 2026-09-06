"""#column type=/constraints checking against cell values."""

from __future__ import annotations

import base64
import re
from datetime import datetime
from fractions import Fraction

from ._document import ColumnDef, Document
from ._errors import ErrorKind, Issue, new_issue

DECIMAL_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$")
UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TIME_RE = re.compile(r"^\d{2}:\d{2}:\d{2}(?:\.\d+)?$")


def check_schema(doc: Document) -> list[Issue]:
    if not doc.meta.columns:
        return []
    issues: list[Issue] = []
    width = doc.column_width()
    for col in range(width):
        d, ok = doc.column_def_at(col)
        if not ok:
            continue
        issues.extend(check_column_values(doc, col, d))
    return issues


def check_column_values(doc: Document, col: int, d: ColumnDef) -> list[Issue]:
    from ._agg import cell_at, data_rows, is_null_cell

    issues: list[Issue] = []
    required = d.attrs.get("required") == "1"
    col_name = d.attrs.get("name") or str(col)
    sep = d.attrs.get("separator", "")
    pattern, pat_err = _compile_column_pattern(d)
    if pat_err is not None:
        issues.append(new_issue(ErrorKind.COLUMN_MALFORMED_ATTRIBUTE, d.line, str(pat_err)))
    enum_set = _split_enum(d.attrs.get("enum", ""))

    for r, row in enumerate(data_rows(doc)):
        raw = cell_at(doc, row, col)
        line = r + 1
        if doc.data.has_header_row:
            line += 1
        if is_null_cell(doc, raw):
            if required:
                issues.append(new_issue(ErrorKind.COLUMN_VALUE_INVALID, line,
                                         f"column {col_name}: required value is null"))
            continue
        parts = raw.split(sep) if sep else [raw]
        for part in parts:
            msg = check_typed_value(d, part)
            if msg:
                issues.append(new_issue(ErrorKind.COLUMN_VALUE_INVALID, line, f"column {col_name}: {msg}"))
                continue
            if enum_set and not _enum_allows(d, enum_set, part):
                issues.append(new_issue(ErrorKind.COLUMN_VALUE_INVALID, line,
                                         f"column {col_name}: value not in enum"))
            if pattern is not None and not pattern.search(part):
                issues.append(new_issue(ErrorKind.COLUMN_VALUE_INVALID, line,
                                         f"column {col_name}: value does not match pattern"))
            msg = check_bounds(d, part)
            if msg:
                issues.append(new_issue(ErrorKind.COLUMN_VALUE_INVALID, line, f"column {col_name}: {msg}"))
            msg = check_length(d, part)
            if msg:
                issues.append(new_issue(ErrorKind.COLUMN_VALUE_INVALID, line, f"column {col_name}: {msg}"))
    return issues


def _compile_column_pattern(d: ColumnDef):
    pat = d.attrs.get("pattern", "")
    if not pat:
        return None, None
    try:
        return re.compile(pat), None
    except re.error as e:
        return None, e


def _split_enum(s: str) -> set[str] | None:
    if not s:
        return None
    return set(s.split("|"))


def _enum_allows(d: ColumnDef, enum_set: set[str], v: str) -> bool:
    if v in enum_set:
        return True
    ct = d.attrs.get("type", "").strip().lower()
    if _is_measure_column_type(ct):
        for allowed in enum_set:
            if _numeric_equal(allowed, v):
                return True
    return False


def _numeric_equal(a: str, b: str) -> bool:
    try:
        return float(a.strip()) == float(b.strip())
    except ValueError:
        return False


def _is_measure_column_type(t: str) -> bool:
    return t in ("int", "long", "float", "double", "decimal", "number")


def check_typed_value(d: ColumnDef, v: str) -> str:
    ct = d.attrs.get("type", "").strip().lower()
    if ct in ("", "string", "text"):
        return ""
    if ct == "int":
        if not _parse_int(v.strip(), 32):
            return "not an int"
    elif ct == "long":
        if not _parse_int(v.strip(), 64):
            return "not a long"
    elif ct == "float":
        if not _parse_float(v.strip()):
            return "not a float"
    elif ct in ("double", "number"):
        if not _parse_float(v.strip()):
            return "not a double"
    elif ct == "decimal":
        if not DECIMAL_RE.match(v.strip()):
            return "not a decimal"
    elif ct == "boolean":
        if v.strip().lower() not in ("true", "false", "1", "0"):
            return "not a boolean"
    elif ct == "date":
        if not DATE_RE.match(v):
            return "not a date (YYYY-MM-DD)"
        try:
            datetime.strptime(v, "%Y-%m-%d")
        except ValueError:
            return "not a date (YYYY-MM-DD)"
    elif ct == "time":
        if not TIME_RE.match(v):
            return "not a time (HH:MM:SS)"
    elif ct == "datetime":
        if not _parse_datetime(v):
            return "not a datetime (ISO 8601)"
    elif ct == "uuid":
        if not UUID_RE.match(v):
            return "not a uuid"
    elif ct == "binary":
        try:
            base64.b64decode(v, validate=True)
        except Exception:
            return "not base64"
    return ""


def _parse_int(s: str, bits: int) -> bool:
    try:
        n = int(s, 10)
    except ValueError:
        return False
    limit = 1 << (bits - 1)
    return -limit <= n < limit


def _parse_float(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False


def _parse_datetime(v: str) -> bool:
    s = v
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        datetime.fromisoformat(s)
        return True
    except ValueError:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            datetime.strptime(v, fmt)
            return True
        except ValueError:
            continue
    return False


def check_bounds(d: ColumnDef, v: str) -> str:
    has_min = "min" in d.attrs
    has_max = "max" in d.attrs
    if not has_min and not has_max:
        return ""
    min_v = d.attrs.get("min", "")
    max_v = d.attrs.get("max", "")
    ct = d.attrs.get("type", "").strip().lower()
    if ct in ("date", "time", "datetime"):
        if has_min and v < min_v:
            return "below min"
        if has_max and v > max_v:
            return "above max"
        return ""
    if ct in ("int", "long", "float", "double", "decimal", "number"):
        if has_min and compare_numeric(v, min_v) < 0:
            return "below min"
        if has_max and compare_numeric(v, max_v) > 0:
            return "above max"
        return ""
    if has_min and v < min_v:
        return "below min"
    if has_max and v > max_v:
        return "above max"
    return ""


def compare_numeric(a: str, b: str) -> int:
    fa = _to_fraction(a)
    fb = _to_fraction(b)
    if fa is not None and fb is not None:
        if fa < fb:
            return -1
        if fa > fb:
            return 1
        return 0
    return -1 if a < b else (1 if a > b else 0)


def _to_fraction(s: str) -> Fraction | None:
    s = s.strip()
    try:
        return Fraction(s)
    except (ValueError, ZeroDivisionError):
        return None


def check_length(d: ColumnDef, v: str) -> str:
    n = len(v)
    s = d.attrs.get("len_min", "")
    if s:
        try:
            if n < int(s):
                return "shorter than len_min"
        except ValueError:
            pass
    s = d.attrs.get("len_max", "")
    if s:
        try:
            if n > int(s):
                return "longer than len_max"
        except ValueError:
            pass
    return ""


Document.check_schema = check_schema
