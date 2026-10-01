"""Core data model: Header, MetaBlock, DataSection, Document, Pack, ParseOptions."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

CURRENT_VERSION = "0.6"


class Form(Enum):
    PLAIN = "plain"
    ZIP_INNER = "zip_inner"
    PACK = "pack"


class Profile(str, Enum):
    NONE = ""
    INLINE = "inline"
    SIDECAR = "sidecar"
    STUB = "stub"


@dataclass
class Checksum:
    algorithm: str = ""
    hex: str = ""


@dataclass
class KV:
    key: str = ""
    value: str = ""


@dataclass
class ColumnDef:
    attrs: dict[str, str] = field(default_factory=dict)
    line: int = 0


@dataclass
class Aggregation:
    name: str = ""
    values: list[str] = field(default_factory=list)
    line: int = 0


@dataclass
class SQLStatement:
    verb: str = ""
    dialect: str = ""
    version: str = ""
    payload: str = ""
    raw_key: str = ""
    line: int = 0
    qualified: bool = False


class NoteTarget(str, Enum):
    """What a #note is attached to, decided by its address fields."""

    CELL = "cell"
    COLUMN = "column"
    ROW = "row"
    TABLE = "table"


# The reserved row-count value usable wherever a #chart channel expects a
# column name (e.g. y=count() for a histogram).
CHART_COUNT_LITERAL = "count()"


@dataclass
class Chart:
    """One #chart suggestion (implementation/charts.md).

    The compact form (``#chart type=bar name=... x=... y=...``) keeps its
    attributes in ``attrs``, in file order. The escape-hatch form
    (``#chart-<engine>: <payload>``) sets ``engine`` and keeps the raw payload.
    """

    attrs: dict[str, str] = field(default_factory=dict)
    engine: str = ""
    payload: str = ""
    line: int = 0

    @property
    def is_escape(self) -> bool:
        return self.engine != ""

    @property
    def type(self) -> str:
        return self.attrs.get("type", "")

    @property
    def name(self) -> str:
        return self.attrs.get("name", "")


@dataclass
class Note:
    """One #note line: a remark on a cell, a row, a column, or the table.

    ``attrs`` keeps every attribute in file order, unknown ones included, so
    the line round-trips unchanged.
    """

    attrs: dict[str, str] = field(default_factory=dict)
    line: int = 0

    @property
    def text(self) -> str:
        return self.attrs.get("text", "")

    @property
    def target(self) -> NoteTarget:
        has_col = "col" in self.attrs
        has_row = "row" in self.attrs or "key" in self.attrs
        if has_row and has_col:
            return NoteTarget.CELL
        if has_col:
            return NoteTarget.COLUMN
        if has_row:
            return NoteTarget.ROW
        return NoteTarget.TABLE


@dataclass
class Link:
    """One #link line: a URL pinned to a single cell, overriding the column's
    ``link=`` template."""

    attrs: dict[str, str] = field(default_factory=dict)
    line: int = 0

    @property
    def href(self) -> str:
        return self.attrs.get("href", "")


@dataclass
class TableDecl:
    name: str = ""
    dir: str = ""
    columns: int = 0
    original_size: int = 0
    line: int = 0
    attrs: dict[str, str] = field(default_factory=dict)


@dataclass
class ForeignKey:
    from_: str = ""
    to: str = ""
    line: int = 0


@dataclass
class UnknownMetaLine:
    text: str = ""
    line: int = 0


@dataclass
class MetaBlock:
    file_meta: list[KV] = field(default_factory=list)
    columns: list[ColumnDef] = field(default_factory=list)
    charts: list[Chart] = field(default_factory=list)
    notes: list[Note] = field(default_factory=list)
    links: list[Link] = field(default_factory=list)
    aggregations: list[Aggregation] = field(default_factory=list)
    sql: list[SQLStatement] = field(default_factory=list)
    human_comments: list[str] = field(default_factory=list)
    tables: list[TableDecl] = field(default_factory=list)
    fks: list[ForeignKey] = field(default_factory=list)
    # unknown holds every "#" meta line this version cannot interpret, verbatim.
    unknown: list[UnknownMetaLine] = field(default_factory=list)


@dataclass
class DataSection:
    has_header_row: bool = False
    header_row: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)


@dataclass
class SourceInfo:
    path: str = ""
    zip_path: str = ""
    comment: str = ""
    primary_name: str = ""
    reference: str = ""
    reference_path: str = ""
    sidecar_path: str = ""
    profile: Profile = Profile.NONE


@dataclass
class Dialect:
    delim: str = ","
    quote: str = '"'
    quote_enabled: bool = True


@dataclass
class Header:
    fields: dict[str, str] = field(default_factory=dict)
    version: str = ""
    delim_name: str = ""
    delim: str = ""
    quote_name: str = ""
    quote: str = ""
    quote_enabled: bool = False
    null: str = ""
    encoding: str = ""
    sql_dialect: str = ""
    header_row: bool = False
    rows: Optional[int] = None
    checksum: Optional[Checksum] = None
    original_size: Optional[int] = None
    has_magic_line: bool = False

    def dialect(self) -> Dialect:
        return Dialect(delim=self.delim, quote=self.quote, quote_enabled=self.quote_enabled)


@dataclass
class Document:
    form: Form = Form.PLAIN
    header: Header = field(default_factory=Header)
    meta: MetaBlock = field(default_factory=MetaBlock)
    data: DataSection = field(default_factory=DataSection)
    source: SourceInfo = field(default_factory=SourceInfo)


@dataclass
class PackTable:
    decl: TableDecl = field(default_factory=TableDecl)
    header: Optional[Document] = None
    col_values: list[list[str]] = field(default_factory=list)
    col_names: list[str] = field(default_factory=list)
    col_paths: list[str] = field(default_factory=list)
    sectioned: bool = False
    section_size: int = 0

    def document(self) -> Optional[Document]:
        return self.header


@dataclass
class Pack:
    manifest: Optional[Document] = None
    tables: list[PackTable] = field(default_factory=list)
    fks: list[ForeignKey] = field(default_factory=list)
    discovered: bool = False

    def table(self, name: str) -> PackTable:
        for t in self.tables:
            if t.decl.name == name:
                return t
        raise KeyError(f"unknown table: {name}")

    def default_table(self) -> Optional[PackTable]:
        if not self.tables:
            return None
        if len(self.tables) == 1:
            return self.tables[0]
        if self.manifest is not None:
            name = self.manifest.header.fields.get("single-table", "").strip()
            if name:
                try:
                    return self.table(name)
                except KeyError:
                    pass
        return None


@dataclass
class ParseOptions:
    strict: bool = False
    clear_human_comments: bool = False
    expect_zip_inner: bool = False
    zip_uncompressed_size: int = 0
    zip_load_data: bool = False
    zip_password: str = ""
    source_path: str = ""
    expect_profile: str = ""
    resolve_reference: bool = False
    pack_role: str = ""  # "", "manifest", "table"


def strict_options() -> ParseOptions:
    return ParseOptions(strict=True, resolve_reference=True, zip_load_data=True)


def lenient_options() -> ParseOptions:
    return ParseOptions(strict=False, resolve_reference=True, zip_load_data=True)


@dataclass
class ParseResult:
    doc: Optional[Document] = None
    pack: Optional[Pack] = None
    warnings: list = field(default_factory=list)

    def ok(self) -> bool:
        return self.doc is not None or self.pack is not None

    def warn(self, kind, line: int, msg: str) -> None:
        from ._errors import new_issue

        self.warnings.append(new_issue(kind, line, msg))


def is_pack_path(path: str) -> bool:
    lower = path.lower()
    return ".pack.zip" in lower or ".pack." in lower
