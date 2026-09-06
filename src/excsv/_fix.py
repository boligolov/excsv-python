"""FixOptions/FixReport/Document.fix: the repair pipeline (derived metadata only)."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ._agg import compute_aggregation_values
from ._document import Document, Pack

FIX_FORMAT = "format"
FIX_COLUMNS = "columns"
FIX_AGG = "agg"
FIX_CHECKSUM = "checksum"
FIX_ROWS = "rows"
FIX_STAMP = "stamp"

ALL_FIX_TARGETS = [FIX_FORMAT, FIX_COLUMNS, FIX_AGG, FIX_CHECKSUM, FIX_ROWS, FIX_STAMP]


@dataclass
class FixOptions:
    only: list[str] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    dry_run: bool = False
    now: datetime | None = None


@dataclass
class FixReport:
    changed: list[str] = field(default_factory=list)
    dry_run: bool = False

    def ok(self) -> bool:
        return not self.changed

    def _mark(self, target: str) -> None:
        if target not in self.changed:
            self.changed.append(target)

    def _merge(self, other: "FixReport") -> None:
        for t in other.changed:
            self._mark(t)


def parse_fix_targets(list_str: str) -> list[str]:
    list_str = list_str.strip()
    if not list_str:
        return []
    valid = set(ALL_FIX_TARGETS)
    out = []
    for part in list_str.split(","):
        part = part.strip()
        if not part:
            continue
        if part not in valid:
            raise ValueError(f"unknown fix target {part!r} (want {', '.join(ALL_FIX_TARGETS)})")
        out.append(part)
    return out


def fix(doc: Document, opts: FixOptions | None = None) -> FixReport:
    """Repairs derived metadata: formatting, inferred #column stubs, stored #%
    values, checksum=, rows= and the #@exported / #@tool stamp.

    Only repairs what is derived from the data -- a cell that contradicts its
    declared type is reported by validate(), not fixed here.
    """
    opts = opts or FixOptions()
    report = FixReport(dry_run=opts.dry_run)
    if doc is None:
        raise ValueError("nil document")
    targets = opts.only or ALL_FIX_TARGETS
    selected = set(targets)

    scope = _resolve_column_scope(doc, opts.columns)

    work = _clone(doc) if opts.dry_run else doc

    requested = bool(opts.only)
    for target in ALL_FIX_TARGETS:
        if target not in selected:
            continue
        changed = _apply_fix_target(work, target, scope, opts.now, requested)
        if changed:
            report._mark(target)
    work.sync_derived()
    return report


def _resolve_column_scope(doc: Document, refs: list[str]):
    if not refs:
        return None
    return {doc.column_index(ref) for ref in refs}


def _apply_fix_target(doc: Document, target: str, scope, now, requested: bool) -> bool:
    if target == FIX_FORMAT:
        before = doc.serialize_canonical()
        doc.tidy()
        after = doc.serialize_canonical()
        return before != after

    if target == FIX_COLUMNS:
        if doc.meta.columns:
            return False
        doc.infer_columns()
        return bool(doc.meta.columns)

    if target == FIX_AGG:
        changed = False
        for a in doc.meta.aggregations:
            want = compute_aggregation_values(doc, a.name)
            merged = _merge_agg_values(a.values, want, scope)
            if merged != a.values:
                a.values = merged
                changed = True
        return changed

    if target == FIX_CHECKSUM:
        before = doc.header.fields.get("checksum")
        present = "checksum" in doc.header.fields
        if not present and not requested:
            return False
        doc.set_data_checksum("sha256")
        return before != doc.header.fields.get("checksum")

    if target == FIX_ROWS:
        before = doc.header.fields.get("rows")
        n = doc.row_count()
        doc.header.fields["rows"] = str(n)
        doc.header.rows = n
        return before != doc.header.fields.get("rows")

    if target == FIX_STAMP:
        ts = now or datetime.now(timezone.utc)
        meta = doc.meta_map()
        stamp = ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        changed = meta.get("exported") != stamp
        doc.set_file_meta("exported", stamp)
        if "tool" not in meta:
            doc.set_file_meta("tool", "excsv-python")
            changed = True
        return changed

    return False


def _merge_agg_values(current: list[str], recomputed: list[str], scope) -> list[str]:
    if scope is None:
        return recomputed
    out = []
    for i, v in enumerate(recomputed):
        if i in scope:
            out.append(v)
        else:
            out.append(current[i] if i < len(current) else "")
    return out


def _clone(doc: Document) -> Document:
    """Deep-copies everything fix() can mutate, so dry_run cannot leak edits."""
    return copy.deepcopy(doc)


def pack_fix(pack: Pack, opts: FixOptions | None = None) -> FixReport:
    from ._pack_write import sync_manifest

    opts = opts or FixOptions()
    report = FixReport(dry_run=opts.dry_run)
    if pack is None:
        raise ValueError("nil pack")
    for t in pack.tables:
        r = fix(t.header, opts)
        report._merge(r)
        if not opts.dry_run:
            t.sync_from_document(t.header)
    if not opts.dry_run:
        sync_manifest(pack)
    return report


Document.fix = fix
Pack.fix = pack_fix
