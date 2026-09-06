"""sha256 data-section checksum: compute, verify, and header sync."""

from __future__ import annotations

import hashlib

from ._csv import join_csv_fields
from ._dialect import parse_checksum_field
from ._document import Checksum, Document
from ._errors import ErrorKind, fail


def compute_data_checksum(data_section: str, alg: str) -> str:
    normalized = data_section.replace("\r\n", "\n").replace("\r", "\n")
    if alg == "sha256":
        return hashlib.sha256(normalized.encode("utf-8", "surrogateescape")).hexdigest()
    raise ValueError(f"unsupported checksum algorithm: {alg}")


def verify_checksum(data_section: str, cs: Checksum | None) -> None:
    if cs is None:
        return
    try:
        got = compute_data_checksum(data_section, cs.algorithm)
    except ValueError as e:
        raise fail(ErrorKind.CHECKSUM_UNKNOWN_ALGORITHM, 0, str(e))
    if got.lower() != cs.hex.lower():
        raise fail(ErrorKind.CHECKSUM_MISMATCH, 0, "checksum mismatch")


def serialize_data_section(doc: Document) -> str:
    d = doc.header.dialect()
    parts = []
    if doc.data.has_header_row:
        parts.append(join_csv_fields(doc.data.header_row, d))
        parts.append("\n")
    for row in doc.data.rows:
        parts.append(join_csv_fields(row, d))
        parts.append("\n")
    return "".join(parts)


def _set_checksum_from_section(doc: Document, data_section: str, algorithm: str) -> None:
    hexdigest = compute_data_checksum(data_section, algorithm)
    value = algorithm + ":" + hexdigest
    doc.header.fields["checksum"] = value
    alg, digest = parse_checksum_field(value)
    doc.header.checksum = Checksum(algorithm=alg, hex=digest)


def set_data_checksum(doc: Document, algorithm: str) -> None:
    _set_checksum_from_section(doc, serialize_data_section(doc), algorithm)


def set_data_checksum_from_section(doc: Document, data_section: str, algorithm: str) -> None:
    _set_checksum_from_section(doc, data_section, algorithm)


Document.serialize_data_section = serialize_data_section
Document.set_data_checksum = set_data_checksum
Document.set_data_checksum_from_section = set_data_checksum_from_section
