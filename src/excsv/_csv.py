"""Field-level CSV splitting/joining for a document's declared dialect."""

from __future__ import annotations

from ._document import Dialect
from ._errors import ErrorKind, fail


def _err_quoted_newline():
    return fail(ErrorKind.QUOTED_VALUE_RAW_NEWLINE, 0, "raw newline in quoted value")


def _err_delimiter_in_value():
    return fail(ErrorKind.QUOTE_NONE_DELIMITER_IN_VALUE, 0, "delimiter in unquoted value")


def split_csv_fields(line: str, d: Dialect) -> list[str]:
    if not d.quote_enabled:
        return _split_unquoted(line, d.delim)
    return _split_quoted(line, d.delim, d.quote)


def _split_unquoted(line: str, delim: str) -> list[str]:
    if line == "":
        return [""]
    parts = line.split(delim)
    for p in parts:
        if "\r" in p or "\n" in p:
            raise _err_quoted_newline()
        if p.count(delim) > 0:
            raise _err_delimiter_in_value()
    return parts


def _split_quoted(line: str, delim: str, quote: str) -> list[str]:
    fields: list[str] = []
    cur: list[str] = []
    in_quote = False
    runes = line
    n = len(runes)
    i = 0
    while i < n:
        r = runes[i]
        if in_quote:
            if r == quote:
                if i + 1 < n and runes[i + 1] == quote:
                    cur.append(r)
                    i += 2
                    continue
                in_quote = False
                i += 1
                continue
            if r == "\n" or r == "\r":
                raise _err_quoted_newline()
            cur.append(r)
            i += 1
            continue
        if r == quote:
            in_quote = True
            i += 1
            continue
        if r == delim:
            fields.append("".join(cur))
            cur = []
            i += 1
            continue
        if r == "\n" or r == "\r":
            raise _err_quoted_newline()
        cur.append(r)
        i += 1
    if in_quote:
        if quote == "#":
            fields.append("".join(cur))
            return fields
        raise _err_quoted_newline()
    fields.append("".join(cur))
    return fields


def join_csv_fields(fields: list[str], d: Dialect) -> str:
    if not d.quote_enabled:
        return d.delim.join(fields)
    return d.delim.join(_quote_field(f, d.delim, d.quote) for f in fields)


def _quote_field(s: str, delim: str, quote: str) -> str:
    needs = any(c in s for c in (quote, delim, "\r", "\n")) or " " in s
    if not needs:
        return s
    return quote + s.replace(quote, quote + quote) + quote


def parse_agg_payload(payload: str, d: Dialect) -> list[str]:
    return split_csv_fields(payload, d)
