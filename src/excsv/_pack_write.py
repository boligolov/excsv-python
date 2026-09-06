"""Pack container writing: PackFromDocument, AddTable/DropTable, Pack.serialize."""

from __future__ import annotations

import io
import zipfile

from ._document import CURRENT_VERSION, Document, Form, Header, MetaBlock, Pack, PackTable, TableDecl
from ._pack import (
    _materialize_pack_table,
    col_payload,
    column_names_from_header,
    safe_col_file_name,
    section_pad_width,
    section_starts,
)

_FIXED_MTIME = (2026, 1, 1, 0, 0, 0)


def pack_from_document(doc: Document, table_name: str = "") -> Pack:
    table_name = table_name or "table"
    pt = document_to_pack_table(doc, table_name)
    man = Document(
        form=Form.PACK,
        header=Header(
            fields={
                "version": CURRENT_VERSION,
                "layout": "pack",
                "table-count": "1",
                "single-table": table_name,
            },
            has_magic_line=True,
            version=CURRENT_VERSION,
        ),
        meta=MetaBlock(tables=[pt.decl]),
    )
    from ._dialect import apply_header_defaults

    apply_header_defaults(man.header)
    return Pack(manifest=man, tables=[pt], fks=[])


def document_to_pack_table(doc: Document, name: str) -> PackTable:
    names = column_names_from_header(doc)
    if not names and doc.data.has_header_row:
        names = list(doc.data.header_row)
    width = len(names)
    if width == 0 and doc.data.rows:
        width = len(doc.data.rows[0])
        names = [f"col{i}" for i in range(width)]
    cols: list[list[str]] = [[] for _ in range(width)]
    for row in doc.data.rows:
        for c in range(width):
            cols[c].append(row[c] if c < len(row) else "")
    header = _clone_document_meta(doc)
    header.form = Form.PACK
    header.header.fields["layout"] = "columnar"
    header.header.fields["rows"] = str(len(doc.data.rows))
    header.header.rows = len(doc.data.rows)
    if not header.meta.columns and names:
        for i, n in enumerate(names):
            header.meta.columns.append(_column_def(n, i))
    dir_ = name + "/"
    payload = sum(len(col_payload(c)) for c in cols)
    pt = PackTable(
        decl=TableDecl(name=name, dir=dir_, columns=width, original_size=payload),
        header=header,
        col_values=cols,
        col_names=names,
    )
    _materialize_pack_table(pt)
    return pt


def _column_def(name: str, index: int):
    from ._document import ColumnDef

    return ColumnDef(attrs={"name": name, "index": str(index)})


def _clone_document_meta(doc: Document) -> Document:
    import copy

    out = Document(form=Form.PACK, header=copy.deepcopy(doc.header), meta=copy.deepcopy(doc.meta), source=copy.deepcopy(doc.source))
    fields = {k: v for k, v in doc.header.fields.items() if k not in ("reference", "checksum", "original-size")}
    out.header.fields = fields
    out.header.checksum = None
    out.header.original_size = None
    return out


def add_table(pack: Pack, doc: Document, name: str) -> None:
    if pack.manifest is not None and len(pack.tables) >= 1:
        pack.manifest.header.fields.pop("single-table", None)
    pt = document_to_pack_table(doc, name)
    pack.tables.append(pt)
    sync_manifest(pack)


def drop_table(pack: Pack, name: str) -> None:
    idx = next((i for i, t in enumerate(pack.tables) if t.decl.name == name), -1)
    if idx < 0:
        raise ValueError(f"unknown table: {name}")
    del pack.tables[idx]
    pack.fks = [fk for fk in pack.fks if not (fk.from_.startswith(name + ".") or fk.to.startswith(name + "."))]
    sync_manifest(pack)


def sync_manifest(pack: Pack) -> None:
    from ._dialect import apply_header_defaults

    if pack.manifest is None:
        pack.manifest = Document(
            form=Form.PACK,
            header=Header(fields={"version": CURRENT_VERSION, "layout": "pack"}, has_magic_line=True, version=CURRENT_VERSION),
        )
        apply_header_defaults(pack.manifest.header)
    decls = []
    total = 0
    for t in pack.tables:
        payload = sum(len(col_payload(c)) for c in t.col_values)
        t.decl.original_size = payload
        t.decl.columns = len(t.col_values)
        decls.append(t.decl)
        total += payload
    pack.manifest.meta.tables = decls
    pack.manifest.meta.fks = pack.fks
    pack.manifest.header.fields["layout"] = "pack"
    pack.manifest.header.fields["version"] = CURRENT_VERSION
    pack.manifest.header.fields["table-count"] = str(len(pack.tables))
    pack.manifest.header.fields["original-size"] = str(total)
    pack.manifest.header.original_size = total
    if len(pack.tables) == 1:
        pack.manifest.header.fields.setdefault("single-table", pack.tables[0].decl.name)
    else:
        pack.manifest.header.fields.pop("single-table", None)


def pack_serialize(pack: Pack) -> bytes:
    sync_manifest(pack)
    entries: list[tuple[str, bytes]] = []
    man_bytes = pack.manifest.serialize_canonical()
    entries.append(("_manifest.excsv", man_bytes))
    for t in pack.tables:
        hb = t.header.serialize_canonical()
        entries.append((t.decl.dir + "_header.excsv", hb))
        header0 = t.header.header.fields.get("header") == "0"
        rows = len(t.col_values[0]) if t.col_values else 0
        if t.sectioned and t.section_size > 0 and rows > 0:
            width = section_pad_width(rows)
            for ci, values in enumerate(t.col_values):
                name = t.col_names[ci] if ci < len(t.col_names) else f"col{ci}"
                folder = t.decl.dir + safe_col_file_name(ci, name, False)[:-len(".col")] + "/"
                for start in section_starts(rows, t.section_size):
                    end = min(start + t.section_size, rows)
                    rel = folder + f"{start:0{width}d}.col"
                    entries.append((rel, col_payload(values[start:end])))
        else:
            for ci, values in enumerate(t.col_values):
                name = t.col_names[ci] if ci < len(t.col_names) else f"col{ci}"
                rel = t.decl.dir + safe_col_file_name(ci, name, header0)
                entries.append((rel, col_payload(values)))

    comment = _pack_comment(man_bytes)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.comment = comment
        for name, body in entries:
            info = zipfile.ZipInfo(name, date_time=_FIXED_MTIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, body)
    return buf.getvalue()


def _pack_comment(manifest: bytes) -> bytes:
    text = manifest.decode("utf-8", errors="surrogateescape")
    lines = []
    for line in text.split("\n"):
        line = line.rstrip("\r")
        if line == "":
            continue
        if line.startswith("#"):
            lines.append(line)
            continue
        break
    c = "\n".join(lines)
    if len(c) > 65535:
        marker = "\n#@comment-truncated: 1"
        keep = max(65535 - len(marker), 0)
        c = c[:keep] + marker
    return c.encode("utf-8", errors="surrogateescape")


def sync_from_document(table: PackTable, doc: Document) -> None:
    if table is None or doc is None:
        return
    table.header = doc
    width = len(doc.data.header_row)
    if width == 0 and doc.data.rows:
        width = len(doc.data.rows[0])
    n_names = len(column_names_from_header(doc))
    if n_names > width:
        width = n_names
    cols: list[list[str]] = [[] for _ in range(width)]
    for row in doc.data.rows:
        for c in range(width):
            cols[c].append(row[c] if c < len(row) else "")
    table.col_values = cols
    if doc.data.header_row:
        table.col_names = list(doc.data.header_row)
    else:
        table.col_names = column_names_from_header(doc)
    n = len(doc.data.rows)
    table.header.header.rows = n
    if table.header.header.fields is None:
        table.header.header.fields = {}
    table.header.header.fields["rows"] = str(n)
    table.header.header.fields["layout"] = "columnar"


def extract_document(table: PackTable) -> Document:
    from ._document import Profile

    doc = _clone_document_meta(table.header)
    doc.header.fields.pop("layout", None)
    doc.header.fields.pop("section-size", None)
    doc.form = Form.PLAIN
    doc.data = table.header.data
    doc.source.profile = Profile.INLINE
    return doc


Pack.add_table = add_table
Pack.drop_table = drop_table
Pack.serialize = pack_serialize
PackTable.sync_from_document = sync_from_document
PackTable.extract_document = extract_document
