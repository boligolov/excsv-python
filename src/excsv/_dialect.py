"""Header dialect resolution: delim=/quote=/header=/encoding=/etc. defaults."""

from __future__ import annotations

from ._document import Checksum, Header
from ._errors import ErrorKind, fail

WELL_KNOWN_DELIMS = {
    "comma": ",",
    "tab": "\t",
    "pipe": "|",
    "semicolon": ";",
}

WELL_KNOWN_QUOTES = {
    "double": '"',
    "single": "'",
    "none": "",
}


def resolve_delim(name: str) -> str:
    if name == "":
        return ","
    if name in WELL_KNOWN_DELIMS:
        return WELL_KNOWN_DELIMS[name]
    if len(name) != 1:
        raise fail(ErrorKind.HEADER_INVALID_VALUE, 1, "delimiter must be one character")
    return name


def resolve_quote(name: str) -> tuple[str, bool]:
    if name == "":
        return '"', True
    if name == "none":
        return "", False
    if name in WELL_KNOWN_QUOTES:
        r = WELL_KNOWN_QUOTES[name]
        if r == "":
            return "", False
        return r, True
    if len(name) != 1:
        raise fail(ErrorKind.HEADER_INVALID_VALUE, 1, "quote must be one character or well-known name")
    return name, True


def apply_header_defaults(h: Header) -> None:
    if h.fields is None:
        h.fields = {}
    if not h.has_magic_line:
        h.delim_name = "comma"
        h.quote_name = "double"
        h.encoding = "UTF-8"
        h.header_row = True
    else:
        if "delim" in h.fields:
            v = h.fields["delim"]
            if v == "":
                raise fail(ErrorKind.HEADER_INVALID_VALUE, 1, "empty delimiter")
            h.delim_name = v
        else:
            h.delim_name = "comma"
        h.quote_name = h.fields.get("quote", "none")
        h.encoding = h.fields.get("encoding", "UTF-8")
        if "header" in h.fields:
            v = h.fields["header"]
            if v != "0" and v != "1":
                raise fail(ErrorKind.HEADER_INVALID_VALUE, 1, "invalid header=" + v)
            h.header_row = v != "0"
        else:
            h.header_row = True
        if "null" in h.fields:
            h.null = h.fields["null"]
        if "sql-dialect" in h.fields:
            h.sql_dialect = h.fields["sql-dialect"]
        if "version" in h.fields:
            h.version = h.fields["version"]

    h.delim = resolve_delim(h.delim_name)
    quote, enabled = resolve_quote(h.quote_name)
    h.quote = quote
    h.quote_enabled = enabled

    if h.delim_name == "":
        raise fail(ErrorKind.HEADER_INVALID_VALUE, 1, "empty delimiter")

    if h.has_magic_line:
        if h.version == "":
            raise fail(ErrorKind.HEADER_MISSING_VERSION, 1, "missing version=")
        if "rows" in h.fields:
            try:
                h.rows = parse_int_field(h.fields["rows"])
            except ValueError:
                raise fail(ErrorKind.HEADER_INVALID_VALUE, 1, "invalid rows=" + h.fields["rows"])
        if "original-size" in h.fields and h.fields["original-size"].strip() != "":
            try:
                h.original_size = parse_int64_field(h.fields["original-size"])
            except ValueError:
                raise fail(ErrorKind.HEADER_INVALID_VALUE, 1, "invalid original-size=" + h.fields["original-size"])


def classify_checksum_field(s: str) -> tuple[Checksum | None, ErrorKind | None]:
    try:
        alg, hexval = parse_checksum_field(s)
    except ValueError:
        return None, ErrorKind.CHECKSUM_MALFORMED
    if alg == "sha256":
        if len(hexval) != 64 or not _is_lower_hex(hexval):
            return None, ErrorKind.CHECKSUM_MALFORMED
        return Checksum(algorithm=alg, hex=hexval), None
    return None, ErrorKind.CHECKSUM_UNKNOWN_ALGORITHM


def _is_lower_hex(s: str) -> bool:
    if s == "":
        return False
    return all(("0" <= c <= "9") or ("a" <= c <= "f") for c in s)


def parse_int_field(s: str) -> int:
    n = 0
    for c in s:
        if not ("0" <= c <= "9"):
            raise ValueError("invalid")
        n = n * 10 + (ord(c) - ord("0"))
    return n


def parse_int64_field(s: str) -> int:
    return parse_int_field(s)


def parse_checksum_field(s: str) -> tuple[str, str]:
    i = s.find(":")
    if i <= 0:
        raise ValueError("invalid")
    return s[:i], s[i + 1:]
