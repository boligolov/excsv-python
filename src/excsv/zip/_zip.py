"""ExCSV row-ZIP container (".excsv.zip"/".ecsv.zip") over stdlib `zipfile`.

Store/Deflate/BZIP2 are handled natively by `zipfile`; AES-256 entry
encryption (the optional --zip-password feature) is delegated to
`_zip_crypto.py`, which needs the optional `pyzipper` dependency.
"""

from __future__ import annotations

import io
import os
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone

PRIMARY_NOT_FIRST = "zip_primary_not_first"
ENCRYPTED = "zip_encrypted"
PASSWORD_REQUIRED = "zip_password_required"
WRONG_PASSWORD = "zip_wrong_password"
UNSUPPORTED_COMPRESSION = "zip_unsupported_compression"
COMMENT_NOT_UTF8 = "zip_comment_not_utf8"
COMMENT_NOT_EXCSV_PREFIX = "zip_comment_not_excsv_prefix"
PRIMARY_MISSING = "zip_primary_missing"
PRIMARY_BAD_NAME = "zip_primary_bad_name"

SUPPORTED_METHODS = {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED, zipfile.ZIP_BZIP2}

_FIXED_MTIME = (2026, 1, 1, 0, 0, 0)


class ZipError(Exception):
    def __init__(self, kind: str, message: str):
        super().__init__(f"{kind}: {message}")
        self.kind = kind
        self.message = message


@dataclass
class InspectResult:
    comment: str = ""
    primary_name: str = ""
    uncompressed_size: int = 0
    primary_index: int = 0
    encrypted: bool = False
    comment_not_utf8: bool = False
    comment_not_excsv_prefix: bool = False
    primary: object = field(default=None, repr=False)


@dataclass
class ExtractResult:
    inner: bytes = b""
    comment: str = ""
    primary_name: str = ""
    uncompressed_size: int = 0
    primary_index: int = 0


def _is_primary_entry_name(name: str) -> bool:
    n = name.lower()
    return n.endswith(".excsv") or n.endswith(".ecsv") or n.endswith(".extsv")


def _locate_primary(archive_path: str, infolist: list) -> tuple:
    if not infolist:
        raise ZipError(PRIMARY_NOT_FIRST, "empty archive")
    primary = infolist[0]
    base_name = os.path.basename(primary.filename).lower()
    if not _is_primary_entry_name(base_name):
        raise ZipError(PRIMARY_NOT_FIRST, "first entry is not a valid primary")
    base = os.path.basename(archive_path)
    if base.endswith(".zip"):
        base = base[: -len(".zip")]
    elif base.endswith(".ZIP"):
        base = base[: -len(".ZIP")]
    want = base.lower()
    if base_name == want or base_name in ("data.excsv", "data.ecsv", "data.extsv"):
        return primary, 0
    raise ZipError(PRIMARY_BAD_NAME, primary.filename)


def inspect(archive_path: str, data: bytes) -> InspectResult:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        infolist = zf.infolist()
        primary, idx = _locate_primary(archive_path, infolist)
        if idx != 0:
            raise ZipError(PRIMARY_NOT_FIRST, "primary entry is not first")
        encrypted = bool(primary.flag_bits & 0x1)
        if not encrypted and primary.compress_type not in SUPPORTED_METHODS:
            raise ZipError(UNSUPPORTED_COMPRESSION, f"method {primary.compress_type}")

        comment_bytes = zf.comment or b""
        comment_not_utf8 = False
        try:
            comment_bytes.decode("utf-8")
        except UnicodeDecodeError:
            comment_not_utf8 = True
        comment_text = comment_bytes.decode("utf-8", errors="surrogateescape")
        comment_not_prefix = (
            not comment_not_utf8 and bool(comment_bytes) and not comment_text.startswith("#!excsv")
        )

        return InspectResult(
            comment=comment_text,
            primary_name=primary.filename,
            uncompressed_size=primary.file_size,
            primary_index=idx,
            encrypted=encrypted,
            comment_not_utf8=comment_not_utf8,
            comment_not_excsv_prefix=comment_not_prefix,
            primary=primary,
        )


def extract_primary(zip_data: bytes, ins: InspectResult) -> bytes:
    return extract_primary_with_password(zip_data, ins, "")


def extract_primary_with_password(zip_data: bytes, ins: InspectResult, password: str) -> bytes:
    if ins is None or ins.primary is None:
        raise ZipError(PRIMARY_MISSING, "no primary entry")
    if ins.encrypted:
        from ._zip_crypto import read_encrypted_entry

        return read_encrypted_entry(zip_data, ins.primary_name, password)
    with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
        return zf.read(ins.primary.filename)


def extract(archive_path: str, data: bytes) -> ExtractResult:
    return extract_with_password(archive_path, data, "")


def extract_with_password(archive_path: str, data: bytes, password: str) -> ExtractResult:
    ins = inspect(archive_path, data)
    inner = extract_primary_with_password(data, ins, password)
    return ExtractResult(
        inner=inner,
        comment=ins.comment,
        primary_name=ins.primary_name,
        uncompressed_size=ins.uncompressed_size,
        primary_index=ins.primary_index,
    )


def read_entries(zip_data: bytes, password: str = ""):
    """Decompresses every non-directory entry. Returns (files, order, first_name, comment)."""
    with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
        infolist = zf.infolist()
        any_encrypted = any(info.flag_bits & 0x1 for info in infolist)
        if any_encrypted:
            from ._zip_crypto import read_entries_encrypted

            return read_entries_encrypted(zip_data, password)
        first_name = infolist[0].filename.replace("\\", "/") if infolist else ""
        comment = (zf.comment or b"").decode("utf-8", errors="surrogateescape")
        files: dict = {}
        order: list = []
        for info in infolist:
            name = info.filename.replace("\\", "/")
            if name.endswith("/"):
                continue
            files[name] = zf.read(info.filename)
            order.append(name)
        return files, order, first_name, comment


def wrap(inner: bytes, entry_name: str, comment: str) -> bytes:
    return wrap_with_password(inner, entry_name, comment, "")


def wrap_with_password(inner: bytes, entry_name: str, comment: str, password: str) -> bytes:
    patched = _patch_original_size(inner)
    if not password:
        return _wrap_plain(patched, entry_name, comment)
    from ._zip_crypto import wrap_encrypted

    return wrap_encrypted(patched, entry_name, comment, password)


def _wrap_plain(patched: bytes, entry_name: str, comment: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        info = zipfile.ZipInfo(entry_name, date_time=_FIXED_MTIME)
        info.compress_type = zipfile.ZIP_DEFLATED
        zf.writestr(info, patched)
        if not comment:
            comment = _build_comment(patched.decode("utf-8", errors="surrogateescape"))
        if len(comment) > 65535:
            comment = _truncate_comment(comment)
        zf.comment = comment.encode("utf-8", errors="surrogateescape")
    return buf.getvalue()


def _patch_original_size(inner: bytes) -> bytes:
    text = inner.decode("utf-8", errors="surrogateescape")
    trailing_nl = text.endswith("\n")
    text = text[:-1] if trailing_nl else text
    text = text[:-1] if text.endswith("\r") else text
    lines = text.split("\n")
    if not lines or not lines[0].startswith("#!excsv"):
        raise ValueError("missing #!excsv header")
    header = lines[0]
    tail = lines[1:]
    n = 0
    for _ in range(3):
        h = _strip_original_size(header)
        combined = h + " original-size=" + str(n)
        body = combined if not tail else combined + "\n" + "\n".join(tail)
        if trailing_nl:
            body += "\n"
        new_n = len(body.encode("utf-8", errors="surrogateescape"))
        if new_n == n:
            return body.encode("utf-8", errors="surrogateescape")
        n = new_n
    raise ValueError("original-size did not converge")


def _strip_original_size(header: str) -> str:
    rest = header[len("#!excsv"):].strip()
    if not rest:
        return "#!excsv"
    parts = [p for p in rest.split() if not p.startswith("original-size=")]
    if not parts:
        return "#!excsv"
    return "#!excsv " + " ".join(parts)


def _build_comment(inner_text: str) -> str:
    lines = []
    for line in inner_text.split("\n"):
        line = line.rstrip("\r")
        if line == "":
            continue
        if line.startswith("#"):
            lines.append(line)
            continue
        break
    return "\n".join(lines)


def _truncate_comment(comment: str) -> str:
    marker = "\n#@comment-truncated: 1"
    budget = 65535 - len(marker)
    cut = comment
    if len(cut) > budget:
        cut = cut[:budget]
        i = cut.rfind("\n")
        if i >= 0:
            cut = cut[:i]
    return cut + marker
