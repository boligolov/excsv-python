"""ParseFile/ParsePath: routes to plain, sidecar, row-ZIP, or pack parsing."""

from __future__ import annotations

import os

from . import zip as excsvzip
from ._document import Document, Form, ParseOptions, ParseResult, is_pack_path
from ._errors import ErrorKind, fail
from ._kv import parse_header_line
from ._records import first_line_bytes
from ._sidecar_util import header_reference

_ZIP_KIND_MAP = {
    "zip_primary_not_first": ErrorKind.ZIP_PRIMARY_NOT_FIRST,
    "zip_encrypted": ErrorKind.ZIP_ENCRYPTED,
    "zip_password_required": ErrorKind.ZIP_PASSWORD_REQUIRED,
    "zip_wrong_password": ErrorKind.ZIP_WRONG_PASSWORD,
    "zip_unsupported_compression": ErrorKind.ZIP_UNSUPPORTED_COMPRESSION,
    "zip_comment_not_utf8": ErrorKind.ZIP_COMMENT_NOT_UTF8,
    "zip_comment_not_excsv_prefix": ErrorKind.ZIP_COMMENT_NOT_EXCSV_PREFIX,
    "zip_primary_missing": ErrorKind.ZIP_PRIMARY_MISSING,
    "zip_primary_bad_name": ErrorKind.ZIP_PRIMARY_BAD_NAME,
}


def map_zip_error(err: Exception) -> Exception:
    if isinstance(err, excsvzip.ZipError):
        kind = _ZIP_KIND_MAP.get(err.kind)
        if kind is not None:
            return fail(kind, 0, err.message)
    return err


def parse_file(path: str, opts: ParseOptions) -> ParseResult:
    from ._sidecar import discover_sidecar_for_data

    path = os.path.normpath(path)
    with open(path, "rb") as f:
        data = f.read()
    sidecar_path, side_data, ok = discover_sidecar_for_data(path)
    if ok:
        return _parse_resolved_path(sidecar_path, side_data, opts)
    return parse_path(path, data, opts)


def parse_path(path: str, data: bytes, opts: ParseOptions) -> ParseResult:
    import dataclasses

    path = os.path.normpath(path)
    opts = dataclasses.replace(opts, source_path=path)
    ext = os.path.splitext(path)[1].lower()
    base = os.path.splitext(os.path.basename(path))[0].lower()
    is_row_zip = ext == ".zip" and (base.endswith(".excsv") or base.endswith(".ecsv") or base.endswith(".extsv"))
    is_zip_magic = len(data) >= 4 and data[0:2] == b"PK" and data[2] == 3 and data[3] == 4

    if is_pack_path(path):
        if is_zip_magic:
            from ._pack import parse_pack_path

            return parse_pack_path(path, data, opts)
        raise fail(ErrorKind.ROW_PARSER_GOT_PACK, 0, "pack container routed to row parser")

    if is_row_zip or is_zip_magic:
        return _parse_zip_path(path, data, opts)

    return _parse_resolved_path(path, data, opts)


def _parse_resolved_path(path: str, data: bytes, opts: ParseOptions) -> ParseResult:
    import dataclasses

    from ._parse import parse_bytes

    opts = dataclasses.replace(opts, source_path=path)
    res = parse_bytes(data, opts)
    if res.doc is not None:
        res.doc.source.path = path
        if not res.doc.source.sidecar_path and header_reference(res.doc.header):
            res.doc.source.sidecar_path = path
    return res


def _parse_zip_path(path: str, data: bytes, opts: ParseOptions) -> ParseResult:
    try:
        ins = excsvzip.inspect(path, data)
    except excsvzip.ZipError as e:
        raise map_zip_error(e)
    if opts.zip_load_data:
        return _parse_zip_inner(path, data, ins, opts)
    return _parse_zip_comment(path, ins, opts)


def _parse_zip_comment(path: str, ins, opts: ParseOptions) -> ParseResult:
    import dataclasses

    from ._document import Header
    from ._dialect import apply_header_defaults
    from ._parse import parse_bytes

    if not ins.comment:
        raise fail(ErrorKind.ZIP_COMMENT_NOT_EXCSV_PREFIX, 0, "zip archive has no #!excsv comment for metadata-only read")
    if ins.comment_not_utf8:
        doc = Document(form=Form.ZIP_INNER, header=Header(fields={}, has_magic_line=False))
        apply_header_defaults(doc.header)
        res = ParseResult(doc=doc)
        _apply_zip_source(res.doc, path, ins)
        _apply_zip_comment_warnings(res, ins)
        return res
    opts = dataclasses.replace(opts, expect_zip_inner=True, zip_uncompressed_size=ins.uncompressed_size)
    res = parse_bytes(ins.comment.encode("utf-8", "surrogateescape"), opts)
    _apply_zip_source(res.doc, path, ins)
    _apply_zip_comment_warnings(res, ins)
    return res


def _parse_zip_inner(path: str, data: bytes, ins, opts: ParseOptions) -> ParseResult:
    import dataclasses

    from ._parse import parse_bytes

    try:
        inner = excsvzip.extract_primary_with_password(data, ins, opts.zip_password)
    except excsvzip.ZipError as e:
        raise map_zip_error(e)
    opts = dataclasses.replace(opts, expect_zip_inner=True, zip_uncompressed_size=ins.uncompressed_size)
    res = parse_bytes(inner, opts)
    _apply_zip_source(res.doc, path, ins)
    _apply_zip_comment_warnings(res, ins)
    if _zip_comment_header_disagree(ins.comment, res.doc.header):
        res.warn(ErrorKind.ZIP_COMMENT_HEADER_DISAGREE, 1, "ZIP comment disagrees with inner header")
    return res


def _apply_zip_comment_warnings(res: ParseResult, ins) -> None:
    if ins is None or res is None:
        return
    if ins.comment_not_utf8:
        res.warn(ErrorKind.ZIP_COMMENT_NOT_UTF8, 0, "invalid UTF-8 in ZIP comment")
    if ins.comment_not_excsv_prefix:
        res.warn(ErrorKind.ZIP_COMMENT_NOT_EXCSV_PREFIX, 0, "ZIP comment does not start with #!excsv")


def _zip_comment_header_disagree(comment: str, inner) -> bool:
    if not comment or not comment.startswith("#!excsv"):
        return False
    first = first_line_bytes(comment.encode("utf-8", "surrogateescape"))
    try:
        cf = parse_header_line(first)
    except Exception:
        return True
    for k, v in cf.items():
        if k in inner.fields and inner.fields[k] != v:
            return True
    return False


def _apply_zip_source(doc: Document, path: str, ins) -> None:
    if doc is None or ins is None:
        return
    doc.form = Form.ZIP_INNER
    doc.source.path = path
    doc.source.zip_path = path
    doc.source.comment = ins.comment
    doc.source.primary_name = ins.primary_name


def wrap_zip(inner: bytes, entry_name: str, comment: str) -> bytes:
    return excsvzip.wrap(inner, entry_name, comment)


def wrap_zip_with_password(inner: bytes, entry_name: str, comment: str, password: str) -> bytes:
    return excsvzip.wrap_with_password(inner, entry_name, comment, password)
