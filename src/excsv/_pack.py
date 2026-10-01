"""Pack container (.excsv.pack.zip) parsing: manifest, tables, .col files."""

from __future__ import annotations

import dataclasses

from . import zip as excsvzip
from ._column import is_virtual_column
from ._dialect import parse_int_field
from ._document import (
    CURRENT_VERSION,
    Document,
    ForeignKey,
    Form,
    Header,
    Pack,
    PackTable,
    ParseOptions,
    ParseResult,
    TableDecl,
)
from ._errors import ErrorKind, fail


def parse_pack_path(path_name: str, data: bytes, opts: ParseOptions) -> ParseResult:
    from ._dialect import apply_header_defaults
    from ._open import map_zip_error
    from ._parse import parse_bytes

    try:
        files, _order, first, comment = excsvzip.read_entries(data, opts.zip_password)
    except excsvzip.ZipError as e:
        raise map_zip_error(e)

    pack = Pack()
    if first == "_manifest.excsv":
        man_opts = dataclasses.replace(opts, pack_role="manifest", expect_zip_inner=False, source_path=path_name)
        res = parse_bytes(files["_manifest.excsv"], man_opts)
        if res.doc.header.fields.get("layout") != "pack":
            raise fail(ErrorKind.PACK_MANIFEST_MISSING_LAYOUT, 1, "_manifest.excsv lacks layout=pack")
        res.doc.form = Form.PACK
        res.doc.source.path = path_name
        res.doc.source.zip_path = path_name
        res.doc.source.comment = comment
        pack.manifest = res.doc
        pack.fks = res.doc.meta.fks
        if not res.doc.meta.tables:
            pack.discovered = True
            pack.tables = _discover_pack_tables(files)
        else:
            for decl in res.doc.meta.tables:
                pack.tables.append(_load_pack_table(files, decl, opts))
    else:
        pack.discovered = True
        doc = Document(
            form=Form.PACK,
            header=Header(fields={"version": CURRENT_VERSION, "layout": "pack"}, has_magic_line=True, version=CURRENT_VERSION),
        )
        doc.source.path = path_name
        doc.source.zip_path = path_name
        doc.source.comment = comment
        apply_header_defaults(doc.header)
        res = ParseResult(doc=doc)
        pack.tables = _discover_pack_tables(files)
        pack.manifest = doc

    from ._notes import check_notes_links

    for pt in pack.tables:
        _validate_pack_table(pt, files)
        _materialize_pack_table(pt)
        # A table's _header.excsv is parsed before its .col data is read, so
        # its #note / #link lines resolve only now.
        res.warnings.extend(check_notes_links(pt.header))

    res.pack = pack
    res.doc.form = Form.PACK
    return res


def _discover_pack_tables(files: dict) -> list[PackTable]:
    dirs = set()
    for name in files:
        if name.endswith("/_header.excsv"):
            dirs.add(name[: -len("_header.excsv")])
    out = []
    for dir_ in sorted(dirs):
        table_name = dir_.rstrip("/")
        if "/" in table_name:
            table_name = table_name.rsplit("/", 1)[1]
        decl = TableDecl(name=table_name, dir=dir_)
        out.append(_load_pack_table(files, decl, ParseOptions(pack_role="table", resolve_reference=True)))
    return out


def _load_pack_table(files: dict, decl: TableDecl, opts: ParseOptions) -> PackTable:
    from ._parse import parse_bytes

    dir_ = decl.dir
    if dir_ and not dir_.endswith("/"):
        dir_ += "/"
        decl.dir = dir_
    if dir_ and _table_dir_missing(files, dir_):
        raise fail(ErrorKind.PACK_TABLE_DIR_MISSING, decl.line, "missing table dir " + dir_)
    header_name = dir_ + "_header.excsv"
    header_bytes = files.get(header_name)
    if header_bytes is None:
        raise fail(ErrorKind.PACK_TABLE_HEADER_MISSING, 0, "missing " + header_name)
    t_opts = dataclasses.replace(opts, pack_role="table", expect_zip_inner=False)
    hres = parse_bytes(header_bytes, t_opts)
    pt = PackTable(decl=decl, header=hres.doc)
    ss = hres.doc.header.fields.get("section-size", "")
    if ss and ss != "0":
        try:
            n = parse_int_field(ss)
        except ValueError:
            raise fail(ErrorKind.HEADER_INVALID_VALUE, 1, "invalid section-size=" + ss)
        pt.section_size = n
        pt.sectioned = n > 0
    names = column_names_from_header(hres.doc)
    pt.col_names = names
    cols, paths, sectioned = _read_table_columns(files, dir_, names, pt.sectioned)
    pt.col_values = cols
    pt.col_paths = paths
    if sectioned:
        pt.sectioned = True
    if pt.decl.columns == 0:
        pt.decl.columns = len(cols)
    return pt


def column_names_from_header(doc: Document) -> list[str]:
    names = []
    for i, col in enumerate(doc.meta.columns):
        if is_virtual_column(col):
            continue
        name = col.attrs.get("name", "")
        if not name:
            name = col.attrs.get("index", str(i))
        names.append(name)
    return names


def _read_table_columns(files: dict, dir_: str, names: list[str], expect_sectioned: bool):
    flat: dict[str, bytes] = {}
    sections: dict[str, list[tuple[int, bytes]]] = {}
    for name, body in files.items():
        if not name.startswith(dir_):
            continue
        rest = name[len(dir_):]
        if rest == "_header.excsv":
            continue
        if not rest.endswith(".col"):
            continue
        parts = rest.split("/")
        if len(parts) == 1:
            flat[parts[0]] = body
        elif len(parts) == 2:
            try:
                start = int(parts[1][: -len(".col")])
            except ValueError:
                continue
            sections.setdefault(parts[0], []).append((start, body))

    sectioned = len(sections) > 0
    keys = list(sections.keys()) if sectioned else list(flat.keys())
    keys.sort(key=_col_key_index)

    values: list[list[str]] = []
    paths: list[str] = []
    if sectioned:
        for k in keys:
            ss = sorted(sections[k], key=lambda t: t[0])
            col: list[str] = []
            for _start, body in ss:
                col.extend(_col_lines(body))
            values.append(col)
            paths.append(dir_ + k + "/")
    else:
        for k in keys:
            values.append(_col_lines(flat[k]))
            paths.append(dir_ + k)
    return values, paths, sectioned


def _col_key_index(name: str) -> int:
    base = name
    dash = base.find("-")
    if dash > 0:
        base = base[:dash]
    if base.endswith(".col"):
        base = base[: -len(".col")]
    try:
        return int(base)
    except ValueError:
        return 1 << 30


def _col_lines(b: bytes) -> list[str]:
    s = b.decode("utf-8", errors="surrogateescape")
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    if s == "":
        return []
    if s.endswith("\n"):
        s = s[:-1]
    if s == "":
        return [""]
    return s.split("\n")


def _validate_pack_table(pt: PackTable, files: dict) -> None:
    dir_ = pt.decl.dir
    if dir_:
        if not any(name.startswith(dir_) for name in files):
            raise fail(ErrorKind.PACK_TABLE_DIR_MISSING, pt.decl.line, "missing table dir " + dir_)
    if (dir_ + "_header.excsv") not in files:
        raise fail(ErrorKind.PACK_TABLE_HEADER_MISSING, 0, "missing _header.excsv in " + dir_)

    physical = len(pt.col_values)
    header_cols = sum(1 for col in pt.header.meta.columns if not is_virtual_column(col))
    if pt.decl.columns > 0 and physical != pt.decl.columns:
        raise fail(ErrorKind.PACK_COLUMN_COUNT_MISMATCH, pt.decl.line, "columns= does not match .col count")
    if header_cols > 0 and physical != header_cols:
        raise fail(ErrorKind.PACK_COLUMN_COUNT_MISMATCH, 0, "column file count does not match #column")

    rows = pt.header.header.rows if pt.header.header.rows is not None else 0

    if pt.sectioned and pt.section_size > 0:
        _validate_sectioned_table(pt, files, rows)
        return

    for col in pt.col_values:
        if rows > 0 and len(col) != rows:
            raise fail(ErrorKind.PACK_COL_LINE_COUNT_MISMATCH, 0, "column line count does not match rows=")


def _validate_sectioned_table(pt: PackTable, files: dict, rows: int) -> None:
    ss = pt.section_size
    if ss <= 0:
        return
    per_col: list[dict[int, int]] = []
    col_keys: list[str] = []
    for name, body in files.items():
        if not name.startswith(pt.decl.dir):
            continue
        rest = name[len(pt.decl.dir):]
        parts = rest.split("/")
        if len(parts) != 2 or not parts[1].endswith(".col"):
            continue
        try:
            start = int(parts[1][: -len(".col")])
        except ValueError:
            continue
        n = len(_col_lines(body))
        if parts[0] not in col_keys:
            col_keys.append(parts[0])
            per_col.append({})
        idx = col_keys.index(parts[0])
        per_col[idx][start] = n
        remain = max(rows - start, 0)
        want_max = min(ss, remain)
        if n > want_max:
            raise fail(ErrorKind.PACK_SECTION_PARTITION_ERROR, 0, "section line count exceeds remaining rows")

    if not per_col:
        return
    ref = per_col[0]
    for other in per_col[1:]:
        if other != ref:
            raise fail(ErrorKind.PACK_SECTION_BOUNDARY_MISMATCH, 0, "section boundaries differ across columns")

    expected = {}
    start = 0
    while start < rows:
        expected[start] = min(ss, rows - start)
        start += ss
    if ref != expected:
        raise fail(ErrorKind.PACK_SECTION_PARTITION_ERROR, 0, "section partitioning does not match section-size=")


def _materialize_pack_table(pt: PackTable) -> None:
    if pt.header is None:
        return
    names = pt.col_names
    if not names:
        names = [f"col{i}" for i in range(len(pt.col_values))]
    n = pt.header.header.rows if pt.header.header.rows is not None else 0
    for col in pt.col_values:
        if len(col) > n:
            n = len(col)
    rows = []
    for r in range(n):
        row = []
        for col in pt.col_values:
            row.append(col[r] if r < len(col) else "")
        rows.append(row)
    pt.header.data.has_header_row = True
    pt.header.data.header_row = names
    pt.header.data.rows = rows
    pt.header.form = Form.PACK


def _table_dir_missing(files: dict, dir_: str) -> bool:
    if not dir_:
        return True
    return not any(name.startswith(dir_) for name in files)


def format_pack_table_line(d: TableDecl) -> str:
    dir_ = d.dir
    if dir_ and not dir_.endswith("/"):
        dir_ += "/"
    return f"#table name={d.name} dir={dir_} columns={d.columns} original-size={d.original_size}"


def format_fk_line(fk: ForeignKey) -> str:
    return f"#fk from={fk.from_} to={fk.to}"


def safe_col_file_name(index: int, name: str, header0: bool) -> str:
    safe = name.lower()
    out = "".join(c if (c.isalnum() and c.isascii()) or c == "_" else "_" for c in safe)
    safe = out.strip("_")
    if not safe:
        safe = "col"
    if header0:
        return f"{index:02d}.col"
    return f"{index:02d}-{safe}.col"


def col_payload(values: list[str]) -> bytes:
    if not values:
        return b""
    return ("\n".join(values) + "\n").encode("utf-8", errors="surrogateescape")


def section_starts(rows: int, section_size: int) -> list[int]:
    return list(range(0, rows, section_size))


def section_pad_width(rows: int) -> int:
    n = max(rows - 1, 0)
    w = len(str(n))
    return max(w, 1)
