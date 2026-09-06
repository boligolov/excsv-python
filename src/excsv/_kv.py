"""Header-line and #column attribute key=value parsing."""

from __future__ import annotations

from ._errors import ErrorKind, fail


def parse_kv_line(line: str, line_no: int) -> dict[str, str]:
    s = line.strip()
    if s == "":
        return {}
    pairs = split_header_pairs(s, line_no)
    out: dict[str, str] = {}
    for p in pairs:
        eq = p.find("=")
        if eq < 0:
            raise fail(ErrorKind.COLUMN_MALFORMED_ATTRIBUTE, line_no, "missing = in attribute")
        key = p[:eq]
        val = p[eq + 1:]
        if key == "":
            raise fail(ErrorKind.COLUMN_MALFORMED_ATTRIBUTE, line_no, "empty attribute key")
        if val == "" and key != "null":
            if key in ("required", "unique"):
                raise fail(ErrorKind.COLUMN_MALFORMED_ATTRIBUTE, line_no, f"attribute {key} requires a value")
        out[key] = val
    return out


def parse_header_line(line: str) -> dict[str, str]:
    if not line.startswith("#!excsv"):
        raise fail(ErrorKind.HEADER_MALFORMED_MAGIC, 1, "expected #!excsv")
    rest = line[len("#!excsv"):].strip()
    if rest == "":
        return {}
    pairs = split_header_pairs(rest, 1)
    out: dict[str, str] = {}
    for p in pairs:
        eq = p.find("=")
        if eq < 0:
            raise fail(ErrorKind.HEADER_MALFORMED_KV, 1, "malformed key=value")
        key = p[:eq]
        val = p[eq + 1:]
        if key == "":
            raise fail(ErrorKind.HEADER_MALFORMED_KV, 1, "empty key")
        out[key] = val
    return out


def split_header_pairs(s: str, line_no: int) -> list[str]:
    pairs: list[str] = []
    i = 0
    n = len(s)
    while i < n:
        while i < n and s[i] == " ":
            i += 1
        if i >= n:
            break
        start = i
        if s[i] == '"':
            i += 1
            buf: list[str] = []
            closed = False
            while i < n:
                if s[i] == '"':
                    if i + 1 < n and s[i + 1] == '"':
                        buf.append('"')
                        i += 2
                        continue
                    i += 1
                    closed = True
                    break
                buf.append(s[i])
                i += 1
            if not closed:
                raise fail(ErrorKind.HEADER_UNCLOSED_QUOTE, line_no, "unclosed quote in header")
            segment = "".join(buf)
            if "=" not in segment:
                raise fail(ErrorKind.HEADER_MALFORMED_KV, line_no, "malformed quoted token")
            pairs.append(segment)
            continue
        while i < n and s[i] != " " and s[i] != "=":
            i += 1
        if i >= n or s[i] != "=":
            pairs.append(s[start:i])
            continue
        key = s[start:i]
        i += 1  # past '='

        if i < n and s[i] == '"':
            i += 1
            buf = []
            closed = False
            while i < n:
                if s[i] == '"':
                    if i + 1 < n and s[i + 1] == '"':
                        buf.append('"')
                        i += 2
                        continue
                    i += 1
                    closed = True
                    break
                buf.append(s[i])
                i += 1
            if not closed:
                raise fail(ErrorKind.HEADER_UNCLOSED_QUOTE, line_no, "unclosed quote in header")
            pairs.append(key + "=" + "".join(buf))
            continue

        val_start = i
        while i < n and s[i] != " ":
            i += 1
        pairs.append(key + "=" + s[val_start:i])
    return pairs


def skip_colon_value(line: str, prefix: str) -> tuple[str, str, bool]:
    if not line.startswith(prefix):
        return "", "", False
    rest = line[len(prefix):]
    colon = rest.find(":")
    if colon < 0:
        return "", "", False
    key = rest[:colon]
    value = rest[colon + 1:]
    if value.startswith(" "):
        value = value[1:]
    return key, value, True


def is_reserved_header_key(key: str) -> bool:
    return key in ("layout", "mode", "section-size", "table-count", "single-table")


def is_reserved_meta_prefix(line: str) -> bool:
    return line.startswith("#table") or line.startswith("#fk")


def valid_utf8(data: bytes) -> bool:
    try:
        data.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False
