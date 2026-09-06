"""excsv -- Python reference implementation of ExCSV (Extended CSV) v0.5.

ExCSV is CSV that describes itself: a conforming document is still plain,
delimiter-separated data, but schema, units, summary statistics, SQL
DDL/DQL, and an integrity checksum ride along as "#" comment lines that any
existing CSV reader already skips.

This package parses, validates, repairs, and serializes all four shapes the
spec defines: plain/sidecar ``.excsv`` (data inline, or meta-only with
``reference=`` pointing at an untouched sibling CSV/TSV), row-oriented ZIP
(``.excsv.zip``), the multi-table columnar pack (``.excsv.pack.zip``), and
the JSON form (``.excsv.json``, a lossless bijection with the text form).

Quick start::

    import excsv

    res = excsv.parse_file("data.excsv", excsv.strict_options())
    doc = res.doc

Opening ``sales.csv`` with a sibling ``sales.excsv`` auto-discovers and loads
the sidecar; set ``ParseOptions.expect_profile`` when the caller needs
sidecar-specific errors (e.g. a missing ``reference=``).

Computed columns: a ``#column formula=`` declares a value derived from other
stored columns instead of storing it (v0.5). ``Document.materialize_column``
evaluates the formula and writes the values in as an ordinary column;
``Document.dematerialize_column`` reverses that, dropping the cached data but
keeping ``formula=``.

Parse and validation failures are reported as :class:`ParseError` wrapping an
:class:`Issue`, whose ``kind`` is one of the :class:`ErrorKind` members -- the
same vocabulary as the spec's normative error-code registry
(``docs/implementation/error-handling.md`` upstream). ``Document.validate()``
returns every finding in one pass rather than stopping at the first problem.

The format itself is specified at https://github.com/boligolov/excsv.
"""

from . import zip  # noqa: F401  (submodule import binds excsv.zip)

# Core data model
from ._document import (
    CURRENT_VERSION,
    Aggregation,
    Checksum,
    ColumnDef,
    DataSection,
    Dialect,
    Document,
    Form,
    ForeignKey,
    Header,
    KV,
    MetaBlock,
    Pack,
    PackTable,
    ParseOptions,
    ParseResult,
    Profile,
    SourceInfo,
    SQLStatement,
    TableDecl,
    UnknownMetaLine,
    is_pack_path,
    lenient_options,
    strict_options,
)
from ._errors import ErrorKind, Issue, ParseError

# Field-level helpers useful to callers building tooling on top of the model
from ._csv import join_csv_fields, split_csv_fields

# Parsing
from ._parse import parse_bytes

# The rest of the pipeline attaches methods onto Document/Pack as a side
# effect of import (mirrors upstream's one-package-many-files layout);
# importing them here guarantees the methods exist before any caller uses
# excsv.Document / excsv.Pack.
from . import _column  # noqa: F401
from . import _schema  # noqa: F401
from . import _agg  # noqa: F401
from . import _checksum  # noqa: F401
from . import _serialize  # noqa: F401
from . import _dataops  # noqa: F401
from . import _mutate  # noqa: F401
from . import _repair  # noqa: F401
from . import _validate  # noqa: F401
from . import _fix  # noqa: F401
from . import _pack  # noqa: F401
from . import _pack_write  # noqa: F401
from . import _export_json  # noqa: F401
from . import _export_csvw  # noqa: F401

from ._open import parse_file, parse_path, wrap_zip, wrap_zip_with_password
from ._sidecar import is_sidecar_meta_only

from ._validate import Finding, ValidateOptions, ValidateReport, is_data_level_warning
from ._fix import ALL_FIX_TARGETS, FixOptions, FixReport, parse_fix_targets
from ._dataops import SortKey
from ._agg import ALL_AGGREGATIONS
from ._repair import DEFAULT_AGGREGATIONS

from ._import import (
    ColumnAttr,
    ImportOptions,
    ImportResult,
    expand_aggregation_list,
    import_delimited,
    parse_column_attr,
)
from ._export_json import JSONExportOptions, JSONExportResult
from ._export_csvw import CSVWExportOptions, CSVWExportResult
from ._pack_write import pack_from_document
from ._formula import FormulaError
from ._sql import effective_dialect

__all__ = [
    "CURRENT_VERSION",
    "Aggregation",
    "Checksum",
    "ColumnDef",
    "DataSection",
    "Dialect",
    "Document",
    "Form",
    "ForeignKey",
    "Header",
    "KV",
    "MetaBlock",
    "Pack",
    "PackTable",
    "ParseOptions",
    "ParseResult",
    "Profile",
    "SourceInfo",
    "SQLStatement",
    "TableDecl",
    "UnknownMetaLine",
    "is_pack_path",
    "lenient_options",
    "strict_options",
    "ErrorKind",
    "Issue",
    "ParseError",
    "FormulaError",
    "join_csv_fields",
    "split_csv_fields",
    "parse_bytes",
    "parse_file",
    "parse_path",
    "wrap_zip",
    "wrap_zip_with_password",
    "Finding",
    "ValidateOptions",
    "ValidateReport",
    "is_data_level_warning",
    "ALL_FIX_TARGETS",
    "FixOptions",
    "FixReport",
    "parse_fix_targets",
    "SortKey",
    "ALL_AGGREGATIONS",
    "DEFAULT_AGGREGATIONS",
    "ColumnAttr",
    "ImportOptions",
    "ImportResult",
    "expand_aggregation_list",
    "import_delimited",
    "parse_column_attr",
    "JSONExportOptions",
    "JSONExportResult",
    "CSVWExportOptions",
    "CSVWExportResult",
    "pack_from_document",
    "is_sidecar_meta_only",
    "effective_dialect",
    "zip",
]
