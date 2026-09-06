"""AES-256 ZIP entry password support.

Standard-library `zipfile` cannot read or write WinZip AES-encrypted entries,
so this feature needs the optional `pyzipper` dependency
(`pip install excsv[crypto]`). It is only imported when a caller actually
supplies a zip password -- the rest of the library has no dependency on it.
"""

from __future__ import annotations

import io

from ._zip import ENCRYPTED, PRIMARY_MISSING, WRONG_PASSWORD, ZipError, _build_comment, _truncate_comment


def _require_pyzipper():
    try:
        import pyzipper
    except ImportError as e:  # pragma: no cover - exercised only without the extra
        raise ImportError(
            "AES-256 zip password support requires the optional 'pyzipper' "
            "dependency -- install with `pip install excsv[crypto]`"
        ) from e
    return pyzipper


def wrap_encrypted(patched: bytes, entry_name: str, comment: str, password: str) -> bytes:
    pyzipper = _require_pyzipper()
    buf = io.BytesIO()
    with pyzipper.AESZipFile(
        buf, "w", compression=pyzipper.ZIP_DEFLATED, encryption=pyzipper.WZ_AES
    ) as zf:
        zf.setpassword(password.encode("utf-8"))
        zf.setencryption(pyzipper.WZ_AES, nbits=256)
        zf.writestr(entry_name, patched)
        if not comment:
            comment = _build_comment(patched.decode("utf-8", errors="surrogateescape"))
        if len(comment) > 65535:
            comment = _truncate_comment(comment)
        zf.comment = comment.encode("utf-8", errors="surrogateescape")
    return buf.getvalue()


def read_encrypted_entry(zip_data: bytes, entry_name: str, password: str) -> bytes:
    if not password:
        raise ZipError(ENCRYPTED, "password required for encrypted entry")
    pyzipper = _require_pyzipper()
    try:
        with pyzipper.AESZipFile(io.BytesIO(zip_data)) as zf:
            zf.setpassword(password.encode("utf-8"))
            try:
                return zf.read(entry_name)
            except RuntimeError as e:
                raise ZipError(WRONG_PASSWORD, "wrong password or corrupt entry") from e
    except KeyError as e:
        raise ZipError(PRIMARY_MISSING, "encrypted entry not found") from e


def read_entries_encrypted(zip_data: bytes, password: str):
    pyzipper = _require_pyzipper()
    with pyzipper.AESZipFile(io.BytesIO(zip_data)) as zf:
        infolist = zf.infolist()
        first_name = infolist[0].filename.replace("\\", "/") if infolist else ""
        comment = (zf.comment or b"").decode("utf-8", errors="surrogateescape")
        files: dict[str, bytes] = {}
        order: list[str] = []
        any_encrypted = any(getattr(info, "flag_bits", 0) & 0x1 for info in infolist)
        if any_encrypted:
            if not password:
                raise ZipError(ENCRYPTED, "encrypted pack requires a password")
            zf.setpassword(password.encode("utf-8"))
        for info in infolist:
            name = info.filename.replace("\\", "/")
            if name.endswith("/"):
                continue
            try:
                files[name] = zf.read(info.filename)
            except RuntimeError as e:
                raise ZipError(WRONG_PASSWORD, "wrong password or corrupt entry") from e
            order.append(name)
        return files, order, first_name, comment
