"""Document.rename_column: rename a column and every reference to it by name
(notes.md § Writer obligations)."""

from __future__ import annotations

import re

from ._chart import chart_channel_values, CHART_CHANNELS
from ._column import column_by_name, is_virtual_column
from ._document import Document, Pack, Profile
from ._errors import ErrorKind, fail
from ._formula import _is_ident_part, _is_ident_start
from ._notes import _AnchorResolver, _TemplatePart, parse_link_template

# Identifiers the formula grammar reserves; a column named like one could not
# be referenced from formula=.
FORMULA_KEYWORDS = frozenset({
    "and", "or", "not", "case", "when", "then", "else", "end", "true", "false", "null",
})

_CHANNELS = frozenset(CHART_CHANNELS)


def rename_column(doc: Document, old: str, new: str) -> list[str]:
    """Renames column ``old`` to ``new`` and updates every reference to it by
    name: the #column declaration, the header row cell, formula= of computed
    columns, #chart channels, #note / #link col=, and ``{old}`` placeholders in
    link= templates.

    Returns notices about references it cannot rewrite safely (#$ SQL,
    #chart-<engine>: payloads). A sidecar's referenced data file is never
    rewritten: when its header cell carries the old name, that text is kept as
    title= so the declaration still maps onto it.

    Raises ValueError (or ParseError for a malformed name) and leaves the
    document unchanged when the rename is not possible.
    """
    new = new.strip()
    if new == "":
        raise ValueError("new column name is required")
    if " " in new or "\t" in new:
        raise fail(ErrorKind.COLUMN_MALFORMED_ATTRIBUTE, 0, "column name must not contain spaces")
    if old == new:
        raise ValueError(f"column is already named {new}")

    _, decl, declared = column_by_name(doc, old)
    header_idx = -1
    if doc.data.has_header_row and not (declared and is_virtual_column(decl)):
        if declared:
            header_idx = _AnchorResolver(doc).phys_index_of_decl(decl)
        elif old in doc.data.header_row:
            header_idx = doc.data.header_row.index(old)
        if header_idx >= len(doc.data.header_row):
            header_idx = -1
    if not declared and header_idx < 0:
        raise ValueError(f"unknown column: {old}")
    if not declared and doc.source.profile == Profile.SIDECAR:
        raise ValueError(f"column {old} has no #column declaration; a sidecar cannot rename its "
                         f"referenced file's header -- declare it first")
    if column_by_name(doc, new)[2]:
        raise ValueError(f"column {new} already exists")
    for i, cell in enumerate(doc.data.header_row):
        if cell == new and i != header_idx:
            raise ValueError(f"column {new} already exists")

    # Formulas are checked before anything changes: a name the formula grammar
    # cannot reference would leave them dangling.
    formulas: dict[int, str] = {}
    for i, col in enumerate(doc.meta.columns):
        expr = col.attrs.get("formula", "")
        if not expr:
            continue
        renamed, changed = rename_formula_ref(expr, old, new)
        if changed:
            if not is_formula_ident(new):
                label = col.attrs.get("name") or col.attrs.get("index", "")
                raise ValueError(f"column {old} is used in formula= of {label}; "
                                 f"{new!r} is not a valid formula identifier")
            formulas[i] = renamed

    if declared:
        decl.attrs["name"] = new
    for i, expr in formulas.items():
        doc.meta.columns[i].attrs["formula"] = expr
    notices: list[str] = []
    if header_idx >= 0 and doc.data.header_row[header_idx] == old:
        if doc.source.profile == Profile.SIDECAR:
            if declared and not decl.attrs.get("title"):
                decl.attrs["title"] = old
                notices.append("sidecar: the referenced data file is not rewritten; its header cell "
                               f"{old} is kept as title={old}")
        else:
            doc.data.header_row[header_idx] = new
            # The header row is part of the data section, which checksum= covers.
            doc.sync_derived()

    _rename_in_charts(doc, old, new, notices)
    _rename_in_notes_links(doc, old, new)
    _rename_in_sql(doc, old, notices)
    return notices


def _rename_in_charts(doc: Document, old: str, new: str, notices: list[str]) -> None:
    for c in doc.meta.charts:
        if c.is_escape:
            if '"' + old + '"' in c.payload:
                notices.append(f"#chart-{c.engine}: payload at line {c.line} mentions {old!r}; update it by hand")
            continue
        for k, v in c.attrs.items():
            if k not in _CHANNELS:
                continue
            refs = chart_channel_values(k, v)
            if old in refs:
                c.attrs[k] = ",".join(new if r == old else r for r in refs)


def _rename_in_notes_links(doc: Document, old: str, new: str) -> None:
    # With header=0, col= and {N} may be bare indexes; only names are renamed.
    for line in [*doc.meta.notes, *doc.meta.links]:
        if line.attrs.get("col") == old:
            line.attrs["col"] = new
    for col in doc.meta.columns:
        template = col.attrs.get("link")
        if template is None:
            continue
        try:
            parts = parse_link_template(template)
        except ValueError:
            continue  # link_template_malformed: leave it as written
        if any(p.ref == old for p in parts):
            for p in parts:
                if p.ref == old:
                    p.ref = new
            col.attrs["link"] = format_link_template(parts)


def _rename_in_sql(doc: Document, old: str, notices: list[str]) -> None:
    word = re.compile(r"\b" + re.escape(old) + r"\b")
    for s in doc.meta.sql:
        if word.search(s.payload):
            notices.append(f"#${s.verb} at line {s.line} mentions {old}; SQL is not rewritten, update it by hand")


def format_link_template(parts: list[_TemplatePart]) -> str:
    """The inverse of parse_link_template: a literal { is written back as {{."""
    return "".join("{" + p.ref + "}" if p.ref is not None else p.lit.replace("{", "{{") for p in parts)


def is_formula_ident(s: str) -> bool:
    if not s or s.lower() in FORMULA_KEYWORDS:
        return False
    return _is_ident_start(s[0]) and all(_is_ident_part(c) for c in s)


def rename_formula_ref(expr: str, old: str, new: str) -> tuple[str, bool]:
    """Replaces column references to ``old`` in a formula= payload, with the
    formula lexer's rules: string literals and function names (an identifier
    followed by "(") are left alone."""
    out: list[str] = []
    changed = False
    i, n = 0, len(expr)
    while i < n:
        c = expr[i]
        if c == "'":
            j = i + 1
            while j < n:
                if expr[j] == "'":
                    if j + 1 < n and expr[j + 1] == "'":
                        j += 2
                        continue
                    j += 1
                    break
                j += 1
            out.append(expr[i:j])
            i = j
        elif _is_ident_start(c):
            j = i
            while j < n and _is_ident_part(expr[j]):
                j += 1
            ident = expr[i:j]
            k = j
            while k < n and expr[k] in " \t":
                k += 1
            if ident == old and (k >= n or expr[k] != "("):
                out.append(new)
                changed = True
            else:
                out.append(ident)
            i = j
        elif c.isascii() and c.isdigit():
            # A number: digits and dots, so "1e5"-like runs never start an identifier.
            j = i
            while j < n and (expr[j].isascii() and expr[j].isdigit() or expr[j] == "."):
                j += 1
            out.append(expr[i:j])
            i = j
        else:
            out.append(c)
            i += 1
    return "".join(out), changed


def rename_column_fks(pack: Pack, table: str, old: str, new: str) -> None:
    """Updates #fk from= / to= references to ``table.old`` after a column of
    that table was renamed."""
    if pack is None:
        return
    ref, repl = table + "." + old, table + "." + new
    for fk in pack.fks:
        if fk.from_ == ref:
            fk.from_ = repl
        if fk.to == ref:
            fk.to = repl


Document.rename_column = rename_column
Pack.rename_column_fks = rename_column_fks
