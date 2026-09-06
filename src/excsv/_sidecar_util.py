"""Sidecar reference helpers with no dependency on the warnings collector
(kept separate from _sidecar.py to avoid an import cycle with _warnings.py)."""

from __future__ import annotations

import os

from ._document import Header


def header_reference(h: Header) -> str:
    return h.fields.get("reference", "").strip()


def is_likely_sidecar_reference(sidecar_path: str, ref: str) -> bool:
    if not sidecar_path:
        return True
    side_base = os.path.basename(sidecar_path)
    side_stem = os.path.splitext(side_base)[0]
    ref_stem = os.path.splitext(os.path.basename(ref))[0]
    return side_stem == ref_stem


def sidecar_ext_mismatch(path: str, h: Header) -> bool:
    ext = os.path.splitext(path)[1].lower()
    if ext != ".extsv":
        return False
    return h.delim_name != "tab"


def looks_abs_windows(p: str) -> bool:
    if len(p) >= 2 and p[1] == ":":
        return True
    return p.startswith("//") or p.startswith("\\\\")


def has_path_dotdot(slash_path: str) -> bool:
    return any(part == ".." for part in slash_path.split("/"))
