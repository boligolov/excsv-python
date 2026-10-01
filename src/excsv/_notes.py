"""#note, link= and #link (implementation/notes.md), added in v0.6.

Notes and links attach human context to the data without changing it. They
are advisory: an address that does not resolve only warns, and the line is
kept so that a later fix to the data or schema can resolve it again.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ._agg import cell_at, is_null_cell
from ._column import is_virtual_column
from ._document import ColumnDef, Document, Link, Note, NoteTarget
from ._errors import ErrorKind, Issue, ParseError, fail, new_issue
from ._kv import split_header_pairs

# The first spec version that defines #note, link= and #link.
NOTES_VERSION = "0.6"

SAFE_LINK_SCHEMES = frozenset({"http", "https", "mailto"})

def _is_index(s: str) -> bool:
    """A non-negative decimal integer in ASCII digits."""
    return s.isascii() and s.isdigit()


_UNRESERVED = frozenset(b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")


# ---------------------------------------------------------------------------
# Parsing and formatting
# ---------------------------------------------------------------------------


def is_meta_keyword(line: str, keyword: str) -> bool:
    """Reports whether line is the meta keyword followed by end of line or
    whitespace ("#note" but not "#notes")."""
    if not line.startswith(keyword):
        return False
    rest = line[len(keyword):]
    return rest == "" or rest[0] in " \t"


def _parse_anchored_attrs(rest: str, line_no: int, malformed: ErrorKind, what: str) -> dict[str, str]:
    """Tokenizes the attributes of a #note / #link line with the #column
    tokenizer. Any token that is not key=value, or an unterminated quote, is
    the line's malformed code."""
    try:
        pairs = split_header_pairs(rest.strip(), line_no)
    except ParseError:
        raise fail(malformed, line_no, what + " line does not tokenize") from None
    attrs: dict[str, str] = {}
    for p in pairs:
        eq = p.find("=")
        if eq <= 0:
            raise fail(malformed, line_no, f"{what} token {p!r} is not key=value")
        attrs[p[:eq]] = p[eq + 1:]
    return attrs


def check_note(note: Note) -> None:
    """Applies the #note FAIL rules that need nothing but the line itself."""
    if "text" not in note.attrs:
        raise fail(ErrorKind.NOTE_MISSING_TEXT, note.line, "#note lacks text=")
    if "row" in note.attrs and "key" in note.attrs:
        raise fail(ErrorKind.NOTE_ROW_AND_KEY, note.line, "#note sets both row= and key=")


def check_link(link: Link) -> None:
    """Applies the #link FAIL rules that need nothing but the line itself."""
    if "href" not in link.attrs:
        raise fail(ErrorKind.LINK_MISSING_HREF, link.line, "#link lacks href=")
    has_row = "row" in link.attrs
    has_key = "key" in link.attrs
    if has_row and has_key:
        raise fail(ErrorKind.LINK_ROW_AND_KEY, link.line, "#link sets both row= and key=")
    if "col" not in link.attrs or not (has_row or has_key):
        raise fail(ErrorKind.LINK_MISSING_ADDRESS, link.line, "#link needs col= and one of row= / key=")


def parse_note_line(line: str, line_no: int) -> Note:
    attrs = _parse_anchored_attrs(line[len("#note"):], line_no, ErrorKind.NOTE_MALFORMED, "#note")
    note = Note(attrs=attrs, line=line_no)
    check_note(note)
    return note


def parse_link_line(line: str, line_no: int) -> Link:
    attrs = _parse_anchored_attrs(line[len("#link"):], line_no, ErrorKind.LINK_MALFORMED, "#link")
    link = Link(attrs=attrs, line=line_no)
    check_link(link)
    return link


def _format_anchored_line(prefix: str, attrs: dict[str, str], quoted: str) -> str:
    """Writes a #note / #link line. The free-text attribute (text= / href=) is
    always quoted, as the spec's examples do; the rest only when they contain
    whitespace or a quote, or are empty."""
    parts = [prefix]
    for k, v in attrs.items():
        if k == quoted or v == "" or any(c in v for c in ' \t"'):
            parts.append(k + '="' + v.replace('"', '""') + '"')
        else:
            parts.append(k + "=" + v)
    return " ".join(parts)


def note_meta_line(note: Note) -> str:
    """Renders the note back to its #note form."""
    return _format_anchored_line("#note", note.attrs, "text")


def link_meta_line(link: Link) -> str:
    """Renders the link back to its #link form."""
    return _format_anchored_line("#link", link.attrs, "href")


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


@dataclass
class ResolvedNote:
    """A #note with its address resolved against the data.

    ``row`` and ``col`` are None when the note is not attached to a row / a
    column, or when that part of its address does not resolve. ``col`` is also
    None for a virtual computed column, which has no physical position;
    ``col_name`` still names it.
    """

    note: Note
    target: NoteTarget
    row: int | None = None
    col: int | None = None
    col_name: str = ""
    resolved: bool = True

    @property
    def text(self) -> str:
        return self.note.text


@dataclass(frozen=True)
class _ColRef:
    phys: int  # physical index; -1 for a virtual computed column
    name: str
    virtual: bool = False

    def key(self, row: int) -> tuple:
        if self.virtual:
            return (row, -1, self.name)
        return (row, self.phys, "")


class _AnchorResolver:
    """Resolves col= / row= / key= addresses, and link= placeholders, against
    one table."""

    def __init__(self, doc: Document):
        from ._warnings import physical_column_count

        self.doc = doc
        self.width = physical_column_count(doc)
        self.rows = len(doc.data.rows)
        self.id_col = -1
        self.id_why = ""
        self.key_rows: dict[str, int] = {}
        self._find_id_column()

    def _find_id_column(self) -> None:
        """Picks the table's id column (notes.md § The id column): the single
        role=id column, else the single unique=1 column."""
        for attr, want, label in (("role", "id", "role=id"), ("unique", "1", "unique=1")):
            found = [c for c in self.doc.meta.columns if c.attrs.get(attr) == want]
            if not found:
                continue
            if len(found) > 1:
                self.id_why = f"{len(found)} columns have {label}, so the table has no id column"
                return
            if is_virtual_column(found[0]):
                self.id_why = "the " + label + " column is virtual and has no raw values"
                return
            self.id_col = self.phys_index_of_decl(found[0])
            if self.id_col < 0:
                self.id_why = "the " + label + " column has no physical position"
                return
            for i, row in enumerate(self.doc.data.rows):
                v = cell_at(self.doc, row, self.id_col)
                if not is_null_cell(self.doc, v):
                    self.key_rows.setdefault(v, i)
            return
        self.id_why = "the table has no id column (role=id or unique=1)"

    def phys_index_of_decl(self, col: ColumnDef) -> int:
        """Maps a non-virtual #column to its physical position: index= when set,
        else (header=1) its header cell, else declaration order over non-virtual
        columns."""
        if "index" in col.attrs:
            try:
                return int(col.attrs["index"])
            except ValueError:
                return -1
        name = col.attrs.get("name", "")
        header_row = self.doc.data.header_row
        if self.doc.header.header_row and name:
            if name in header_row:
                return header_row.index(name)
            title = col.attrs.get("title", "")
            if title and title in header_row:
                return header_row.index(title)
        phys = 0
        for c in self.doc.meta.columns:
            if is_virtual_column(c):
                continue
            if c is col:
                return phys
            phys += 1
        return -1

    def column(self, ref: str) -> _ColRef | None:
        """Resolves a col= value or a link= placeholder: a name with header=1;
        with header=0 a declared name, else a zero-based index."""
        if ref == "":
            return None
        for col in self.doc.meta.columns:
            if col.attrs.get("name") != ref:
                continue
            if is_virtual_column(col):
                return _ColRef(phys=-1, name=ref, virtual=True)
            i = self.phys_index_of_decl(col)
            if i >= 0 and (self.width == 0 or i < self.width):
                return _ColRef(phys=i, name=ref)
            return None
        if self.doc.header.header_row:
            if ref in self.doc.data.header_row:
                return _ColRef(phys=self.doc.data.header_row.index(ref), name=ref)
            return None
        if not _is_index(ref):
            return None
        n = int(ref)
        if n >= self.width:
            return None
        return _ColRef(phys=n, name=ref)

    def row(self, attrs: dict[str, str]) -> tuple[int, bool, bool, str]:
        """Resolves row= / key=. Returns (row, present, ok, why); present is
        False when the line carries neither."""
        if "row" in attrs:
            v = attrs["row"]
            if not _is_index(v):
                return -1, True, False, "row=" + v + " is not a non-negative integer"
            n = int(v)
            if n >= self.rows:
                return -1, True, False, f"row={n} is past the last data row"
            return n, True, True, ""
        if "key" in attrs:
            v = attrs["key"]
            if self.id_col < 0:
                return -1, True, False, "key=" + v + ": " + self.id_why
            if v not in self.key_rows:
                return -1, True, False, "key=" + v + " matches no row"
            return self.key_rows[v], True, True, ""
        return -1, False, False, ""


def _resolve_notes(doc: Document, r: _AnchorResolver) -> tuple[list[ResolvedNote], list[Issue]]:
    out: list[ResolvedNote] = []
    issues: list[Issue] = []
    for n in doc.meta.notes:
        rn = ResolvedNote(note=n, target=n.target)
        why: list[str] = []
        if "col" in n.attrs:
            c = r.column(n.attrs["col"])
            if c is not None:
                rn.col = None if c.virtual else c.phys
                rn.col_name = c.name
            else:
                rn.resolved = False
                why.append("col=" + n.attrs["col"] + " names no column")
        row, present, ok, msg = r.row(n.attrs)
        if present:
            if ok:
                rn.row = row
            else:
                rn.resolved = False
                why.append(msg)
        if not rn.resolved:
            rn.row = rn.col = None
            issues.append(new_issue(ErrorKind.NOTE_UNRESOLVED, n.line,
                                    "#note does not resolve (" + "; ".join(why) + "); kept"))
        out.append(rn)
    return out, issues


def resolve_notes(doc: Document) -> list[ResolvedNote]:
    """Resolves every #note against the data, in file order. Unresolved notes
    are kept, with ``resolved`` False."""
    out, _ = _resolve_notes(doc, _AnchorResolver(doc))
    return out


@dataclass
class _TemplatePart:
    lit: str = ""
    ref: str | None = None


def parse_link_template(template: str) -> list[_TemplatePart]:
    """Splits a link= template (notes.md § Template grammar). Raises
    ValueError on an unterminated ``{``."""
    parts: list[_TemplatePart] = []
    lit: list[str] = []
    i, n = 0, len(template)
    while i < n:
        ch = template[i]
        if ch != "{":
            lit.append(ch)
            i += 1
            continue
        if i + 1 < n and template[i + 1] == "{":
            lit.append("{")
            i += 2
            continue
        end = template.find("}", i + 1)
        if end < 0:
            raise ValueError("link= template has an unterminated {")
        if lit:
            parts.append(_TemplatePart(lit="".join(lit)))
            lit = []
        parts.append(_TemplatePart(ref=template[i + 1:end]))
        i = end + 1
    if lit:
        parts.append(_TemplatePart(lit="".join(lit)))
    return parts


def percent_encode_link_value(value: str) -> str:
    """Percent-encodes a substituted link= value: UTF-8 bytes, uppercase hex,
    only RFC 3986 unreserved characters left as is."""
    return "".join(chr(b) if b in _UNRESERVED else f"%{b:02X}" for b in value.encode("utf-8", "surrogateescape"))


def is_safe_link_scheme(url: str) -> bool:
    """Reports whether a final URL may be rendered as a link: its scheme is
    http, https or mailto (case-insensitive). A URL without a scheme is not
    safe."""
    colon = url.find(":")
    if colon <= 0:
        return False
    scheme = url[:colon]
    if not (scheme[0].isascii() and scheme[0].isalpha()):
        return False
    if not all(c.isascii() and (c.isalnum() or c in "+-.") for c in scheme):
        return False
    return scheme.lower() in SAFE_LINK_SCHEMES


@dataclass
class LinkSet:
    """Every link of a table, resolved: link= templates substituted per cell,
    #link overrides applied, and the scheme check done. Only links that passed
    the check are present."""

    _resolver: _AnchorResolver = field(repr=False)
    _cells: dict[tuple, str] = field(default_factory=dict)

    def cell_link(self, row: int, col: str) -> str | None:
        """The URL the cell at data row ``row``, column ``col`` (a name, or an
        index with header=0) links to; None when the cell has no link or its
        link failed the scheme check and is shown as plain text."""
        c = self._resolver.column(col)
        if c is None:
            return None
        return self._cells.get(c.key(row))

    def __len__(self) -> int:
        return len(self._cells)


def _column_label(col: ColumnDef) -> str:
    return col.attrs.get("name") or col.attrs.get("index", "")


def _template_columns(r: _AnchorResolver, parts: list[_TemplatePart], self_index: int) -> tuple[list[int], str]:
    """Resolves each placeholder to a physical column. A placeholder must name
    a stored or materialized column; the second value is the first one that
    does not (empty when all resolve)."""
    refs = []
    for p in parts:
        if p.ref is None:
            refs.append(-1)
        elif p.ref == "$":
            refs.append(self_index)
        else:
            c = r.column(p.ref)
            if c is None or c.virtual:
                return [], p.ref
            refs.append(c.phys)
    return refs, ""


def _expand_link_template(doc: Document, parts: list[_TemplatePart], refs: list[int],
                          row: list[str], single: bool) -> str | None:
    """Substitutes one row; None when a placeholder's value is null (that cell
    has no link)."""
    out = []
    for p, ref in zip(parts, refs):
        if ref < 0:
            out.append(p.lit)
            continue
        v = cell_at(doc, row, ref)
        if is_null_cell(doc, v):
            return None
        out.append(v if single else percent_encode_link_value(v))
    return "".join(out)


def _resolve_links(doc: Document, r: _AnchorResolver) -> tuple[LinkSet, list[Issue]]:
    links = LinkSet(_resolver=r)
    issues: list[Issue] = []

    for col in doc.meta.columns:
        template = col.attrs.get("link")
        if template is None or is_virtual_column(col):
            continue
        label = "column " + _column_label(col) + ": "
        try:
            parts = parse_link_template(template)
        except ValueError as e:
            issues.append(new_issue(ErrorKind.LINK_TEMPLATE_MALFORMED, col.line,
                                    label + str(e) + "; shown without links"))
            continue
        self_index = r.phys_index_of_decl(col)
        if self_index < 0:
            continue
        refs, bad = _template_columns(r, parts, self_index)
        if bad:
            issues.append(new_issue(ErrorKind.LINK_UNKNOWN_COLUMN, col.line,
                                    label + "link= placeholder {" + bad +
                                    "} names no stored or materialized column; shown without links"))
            continue
        single = len(parts) == 1 and parts[0].ref is not None
        unsafe = 0
        for i, row in enumerate(doc.data.rows):
            href = _expand_link_template(doc, parts, refs, row, single)
            if href is None:
                continue
            if not is_safe_link_scheme(href):
                unsafe += 1
                continue
            links._cells[(i, self_index, "")] = href
        if unsafe:
            issues.append(new_issue(ErrorKind.LINK_UNSAFE_SCHEME, col.line,
                                    label + f"{unsafe} link(s) not http/https/mailto; shown as plain text"))

    seen: dict[tuple, int] = {}
    for link in doc.meta.links:
        col_value = link.attrs.get("col", "")
        c = r.column(col_value)
        row, _, ok_row, why = r.row(link.attrs)
        if c is None or not ok_row:
            if c is None:
                why = "col=" + col_value + " names no column"
            issues.append(new_issue(ErrorKind.LINK_UNRESOLVED, link.line, "#link does not resolve (" + why + "); kept"))
            continue
        key = c.key(row)
        if key in seen:
            issues.append(new_issue(ErrorKind.LINK_DUPLICATE, link.line,
                                    f"#link targets the same cell as line {seen[key]}; last one wins"))
        seen[key] = link.line
        if not is_safe_link_scheme(link.href):
            links._cells.pop(key, None)
            issues.append(new_issue(ErrorKind.LINK_UNSAFE_SCHEME, link.line,
                                    "#link href is not http/https/mailto; shown as plain text"))
            continue
        links._cells[key] = link.href
    return links, issues


def resolve_links(doc: Document) -> LinkSet:
    """Resolves every link= template and #link line against the data."""
    links, _ = _resolve_links(doc, _AnchorResolver(doc))
    return links


def _has_link_templates(doc: Document) -> bool:
    return any("link" in col.attrs for col in doc.meta.columns)


def check_notes_links(doc: Document) -> list[Issue]:
    """Runs the #note / #link / link= resolution rules (all WARN) against the
    data: unresolved addresses, duplicate #link cells, unknown or malformed
    template placeholders, and links failing the scheme check."""
    if not doc.meta.notes and not doc.meta.links and not _has_link_templates(doc):
        return []
    r = _AnchorResolver(doc)
    _, issues = _resolve_notes(doc, r)
    _, link_issues = _resolve_links(doc, r)
    return issues + link_issues


# ---------------------------------------------------------------------------
# Editing
# ---------------------------------------------------------------------------


def row_anchor(doc: Document, row: int) -> tuple[str, str]:
    """The address a writer should use for data row ``row``: ("key", id) when
    the table has an id column and that value leads back to this row, else
    ("row", str(row)) (notes.md § Positional vs. key anchors)."""
    if row < 0 or row >= len(doc.data.rows):
        raise IndexError(f"row {row} is out of range (the table has {len(doc.data.rows)} data rows)")
    r = _AnchorResolver(doc)
    if r.id_col >= 0:
        v = cell_at(doc, doc.data.rows[row], r.id_col)
        if r.key_rows.get(v) == row:
            return "key", v
    return "row", str(row)


def resolve_column(doc: Document, ref: str) -> bool:
    """Reports whether col= value ``ref`` names a column of the table."""
    return _AnchorResolver(doc).column(ref) is not None


def require_version(doc: Document, version: str) -> None:
    """Raises a declared version= that predates ``version``, once the document
    uses a construct that version introduced. A version this package does not
    know is left alone."""
    from ._warnings import compare_versions, is_known_version

    h = doc.header
    if not h.has_magic_line or not h.version or not is_known_version(h.version):
        return
    if compare_versions(h.version, version) >= 0:
        return
    h.version = version
    h.fields["version"] = version


def add_note(doc: Document, attrs: dict[str, str]) -> Note:
    """Appends a #note built from attrs (in the given order)."""
    note = Note(attrs=dict(attrs))
    check_note(note)
    doc.meta.notes.append(note)
    require_version(doc, NOTES_VERSION)
    return note


def remove_note(doc: Document, index: int) -> bool:
    """Removes the note at ``index`` of ``doc.meta.notes``."""
    if index < 0 or index >= len(doc.meta.notes):
        return False
    del doc.meta.notes[index]
    return True


def _same_anchor(a: dict[str, str], b: dict[str, str]) -> bool:
    return all(a.get(k) == b.get(k) for k in ("col", "row", "key"))


def set_link(doc: Document, attrs: dict[str, str]) -> Link:
    """Adds a #link, or replaces an existing #link with the same address
    (col= plus row= or key=)."""
    link = Link(attrs=dict(attrs))
    check_link(link)
    require_version(doc, NOTES_VERSION)
    for i, existing in enumerate(doc.meta.links):
        if _same_anchor(existing.attrs, link.attrs):
            doc.meta.links[i] = link
            return link
    doc.meta.links.append(link)
    return link


def remove_link(doc: Document, index: int) -> bool:
    """Removes the #link at ``index`` of ``doc.meta.links``."""
    if index < 0 or index >= len(doc.meta.links):
        return False
    del doc.meta.links[index]
    return True


def anchor_resolver(doc: Document) -> _AnchorResolver:
    """Snapshot of the row/column addressing before a structural edit; pass it
    to :func:`remap_row_anchors` afterwards."""
    return _AnchorResolver(doc)


def remap_row_anchors(doc: Document, r: _AnchorResolver, new_index: list[int]) -> None:
    """Keeps row= on notes and links pointing at the same row after rows were
    reordered or removed (notes.md § Writer obligations).

    ``new_index`` maps an old row position to its new one, -1 when the row was
    removed; lines whose row was removed are dropped, whether anchored by row=
    or key=. Unresolved lines are kept unchanged. ``r`` must be a resolver
    built before the rows changed.
    """

    def remap(attrs: dict[str, str]) -> dict[str, str] | None:
        row, present, ok, _ = r.row(attrs)
        if not present or not ok or row >= len(new_index):
            return attrs
        to = new_index[row]
        if to < 0:
            return None
        if "row" in attrs:
            attrs = dict(attrs)
            attrs["row"] = str(to)
        return attrs

    notes = []
    for n in doc.meta.notes:
        attrs = remap(n.attrs)
        if attrs is not None:
            n.attrs = attrs
            notes.append(n)
    doc.meta.notes = notes
    links = []
    for link in doc.meta.links:
        attrs = remap(link.attrs)
        if attrs is not None:
            link.attrs = attrs
            links.append(link)
    doc.meta.links = links


Note.meta_line = note_meta_line
Link.meta_line = link_meta_line
Document.resolve_notes = resolve_notes
Document.resolve_links = resolve_links
Document.check_notes_links = check_notes_links
Document.row_anchor = row_anchor
Document.resolve_column = resolve_column
Document.add_note = add_note
Document.remove_note = remove_note
Document.set_link = set_link
Document.remove_link = remove_link
