"""Sidecar reference resolution: `sales.excsv` -> `sales.csv`, and the reverse."""

from __future__ import annotations

import os

from ._column import validate_columns
from ._data_section import build_data_section
from ._document import Document, ParseOptions, ParseResult, Profile
from ._errors import ErrorKind, fail
from ._path_delim import header_for_data_path
from ._records import split_records
from ._sidecar_util import (
    has_path_dotdot,
    header_reference,
    looks_abs_windows,
)
from ._warnings import (
    apply_checksum_warning,
    apply_columns_mismatch_warning,
    apply_rows_mismatch_warning,
    collect_meta_warnings,
    enforce_declarations,
)

__all__ = [
    "header_reference",
    "is_sidecar_meta_only",
    "resolve_reference_path",
    "finish_sidecar_meta",
    "discover_sidecar_for_data",
    "validate_expect_profile",
]


def is_sidecar_meta_only(doc: Document) -> bool:
    if doc is None or not header_reference(doc.header):
        return False
    return not doc.data.rows and not doc.data.has_header_row


def resolve_reference_path(sidecar_path: str, ref: str) -> str:
    ref = ref.strip()
    if not ref:
        raise fail(ErrorKind.SIDECAR_MISSING_REFERENCE, 1, "missing reference=")
    slash = ref.replace("\\", "/")
    if os.path.isabs(ref) or os.path.isabs(slash) or looks_abs_windows(slash):
        raise fail(ErrorKind.SIDECAR_REFERENCE_ESCAPES_DIR, 1, "reference= must be relative")
    if has_path_dotdot(slash):
        raise fail(ErrorKind.SIDECAR_REFERENCE_ESCAPES_DIR, 1, "reference= escapes sidecar directory")
    dir_ = os.path.dirname(sidecar_path)
    joined = os.path.normpath(os.path.join(dir_, ref.replace("/", os.sep)))
    if dir_:
        rel = os.path.relpath(joined, dir_)
        if has_path_dotdot(rel.replace(os.sep, "/")):
            raise fail(ErrorKind.SIDECAR_REFERENCE_ESCAPES_DIR, 1, "reference= escapes sidecar directory")
    return joined


def parse_delimited_data(data: bytes, h) -> tuple:
    from ._bom import strip_utf8_bom

    data = strip_utf8_bom(data)
    records = split_records(data)
    full = data.decode("utf-8", errors="surrogateescape")
    d = h.dialect()
    ds, section, _col_count, _warnings = build_data_section(records, full, d, h.header_row, h.fields.get("quote", ""), 0, 0, True)
    return ds, section


def attach_referenced_data(res: ParseResult, data_path: str, data: bytes, opts: ParseOptions) -> ParseResult:
    doc = res.doc
    ref_header = header_for_data_path(doc.header, data_path)
    ds, data_section = parse_delimited_data(data, ref_header)
    doc.data = ds
    doc.source.reference_path = data_path
    doc.source.profile = Profile.SIDECAR

    from ._column import column_count_from_schema

    if doc.data.has_header_row:
        col_count = len(doc.data.header_row)
    else:
        col_count = column_count_from_schema(doc.meta.columns)
    validate_columns(res, col_count)
    collect_meta_warnings(res)
    enforce_declarations(res)
    from ._warnings import append_extsv_warning

    append_extsv_warning(res, doc.source.sidecar_path)
    apply_rows_mismatch_warning(res)
    apply_columns_mismatch_warning(res, opts)
    apply_checksum_warning(res, data_section, True)
    return res


def finish_sidecar_meta(res: ParseResult, opts: ParseOptions) -> ParseResult:
    from ._warnings import append_extsv_warning

    doc = res.doc
    ref = header_reference(doc.header)
    doc.source.reference = ref
    doc.source.sidecar_path = opts.source_path
    doc.source.profile = Profile.SIDECAR

    append_extsv_warning(res, opts.source_path)

    if opts.expect_profile in ("sidecar", "sidecar_strict"):
        if not ref:
            raise fail(ErrorKind.SIDECAR_MISSING_REFERENCE, 1, "sidecar missing reference=")

    if not opts.resolve_reference:
        collect_meta_warnings(res)
        enforce_declarations(res)
        return res

    data_path = resolve_reference_path(opts.source_path, ref)
    try:
        with open(data_path, "rb") as f:
            data = f.read()
    except FileNotFoundError:
        res.warn(ErrorKind.SIDECAR_REFERENCE_NOT_FOUND, 1, "referenced file not found: " + ref)
        collect_meta_warnings(res)
        enforce_declarations(res)
        return res
    return attach_referenced_data(res, data_path, data, opts)


def discover_sidecar_for_data(data_path: str) -> tuple[str, bytes, bool]:
    ext = os.path.splitext(data_path)[1].lower()
    if ext not in (".csv", ".tsv"):
        return "", b"", False
    dir_ = os.path.dirname(data_path)
    base = os.path.splitext(os.path.basename(data_path))[0]
    for side_ext in (".excsv", ".ecsv", ".extsv"):
        candidate = os.path.join(dir_, base + side_ext) if dir_ else base + side_ext
        try:
            with open(candidate, "rb") as f:
                return candidate, f.read(), True
        except FileNotFoundError:
            continue
    return "", b"", False


def validate_expect_profile(doc: Document, profile: str) -> None:
    if not profile:
        return
    ref = header_reference(doc.header)
    has_data = doc.row_count() > 0 or doc.data.has_header_row
    if profile == "stub":
        if ref or has_data:
            raise fail(ErrorKind.HEADER_INVALID_VALUE, 1, "expected header-only stub profile")
    elif profile in ("sidecar", "sidecar_strict"):
        if not ref:
            raise fail(ErrorKind.SIDECAR_MISSING_REFERENCE, 1, "sidecar missing reference=")
