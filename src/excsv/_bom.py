UTF8_BOM = b"\xef\xbb\xbf"


def strip_utf8_bom(data: bytes) -> bytes:
    """Remove a leading UTF-8 BOM (U+FEFF) when present."""
    if data.startswith(UTF8_BOM):
        return data[len(UTF8_BOM):]
    return data
