"""#$ SQL companion statements: key parsing (verb-dialect-version) and dialects."""

from __future__ import annotations

from ._document import SQLStatement
from ._errors import ErrorKind, fail

KNOWN_SQL_VERBS = {"ddl", "dql"}

DIALECT_ALIASES = {
    "postgresql": "postgres",
    "pg": "postgres",
    "sqlserver": "mssql",
}

WELL_KNOWN_DIALECTS = [
    "clickhouse", "postgresql", "snowflake", "sqlserver", "bigquery",
    "mariadb", "postgres", "sqlite", "oracle", "mysql", "mssql",
    "duckdb", "ansi", "db2", "pg",
]


def parse_sql_key(raw: str) -> tuple[str, str, str, bool]:
    dash = raw.find("-")
    if dash < 0:
        return raw, "", "", False
    verb = raw[:dash]
    suffix = raw[dash + 1:].lower()
    dialect, version = split_dialect_version(suffix)
    return verb, dialect, version, True


def split_dialect_version(suffix: str) -> tuple[str, str]:
    best = ""
    for d in WELL_KNOWN_DIALECTS:
        if suffix == d or suffix.startswith(d + "-"):
            if len(d) > len(best):
                best = d
    if best == "":
        return suffix, ""
    if suffix == best:
        return normalize_dialect(best), ""
    return normalize_dialect(best), suffix[len(best) + 1:]


def normalize_dialect(d: str) -> str:
    d = d.lower()
    return DIALECT_ALIASES.get(d, d)


def is_known_dialect(d: str) -> bool:
    if d == "":
        return True
    d = d.lower()
    if d in DIALECT_ALIASES:
        return True
    for k in WELL_KNOWN_DIALECTS:
        if d == k or d == normalize_dialect(k):
            return True
    return False


def effective_dialect(stmt: SQLStatement, header_sql_dialect: str) -> str:
    if stmt.qualified and stmt.dialect:
        if stmt.version:
            return stmt.dialect + "-" + stmt.version
        return stmt.dialect
    if header_sql_dialect:
        return header_sql_dialect
    return "ansi"


def sql_payload_unclosed(payload: str) -> bool:
    n = 0
    for c in payload:
        if c == "(":
            n += 1
        elif c == ")":
            if n > 0:
                n -= 1
    return n > 0


def parse_sql_line(line: str, line_no: int) -> SQLStatement:
    if not line.startswith("#$"):
        raise fail(ErrorKind.SQL_MISSING_COLON, line_no, "expected #$ prefix")
    rest = line[2:]
    colon = rest.find(":")
    if colon < 0:
        raise fail(ErrorKind.SQL_MISSING_COLON, line_no, "missing colon in #$ line")
    raw_key = rest[:colon]
    payload = rest[colon + 1:]
    if payload.startswith(" "):
        payload = payload[1:]
    if "\r" in payload or "\n" in payload:
        raise fail(ErrorKind.SQL_EMBEDDED_NEWLINE, line_no, "embedded newline in SQL payload")
    verb, dialect, version, qualified = parse_sql_key(raw_key)
    return SQLStatement(
        verb=verb, dialect=dialect, version=version, payload=payload,
        raw_key=raw_key, line=line_no, qualified=qualified,
    )
