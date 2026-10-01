"""Error-code registry, matching docs/implementation/error-handling.md upstream."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ErrorKind(str, Enum):
    HEADER_MISSING_VERSION = "header_missing_version"
    HEADER_MISSING_ROWS = "header_missing_rows"
    HEADER_MALFORMED_KV = "header_malformed_kv"
    HEADER_MALFORMED_MAGIC = "header_malformed_magic"
    HEADER_UNCLOSED_QUOTE = "header_unclosed_quote"
    HEADER_INVALID_VALUE = "header_invalid_value"
    UNKNOWN_VERSION = "unknown_version"
    COLUMN_MISSING_NAME = "column_missing_name"
    COLUMN_MISSING_INDEX = "column_missing_index"
    COLUMN_TITLE_HEADER_MISMATCH = "column_title_header_mismatch"
    COLUMN_NAME_HEADER_MISMATCH = "column_name_header_mismatch"
    COLUMN_MALFORMED_ATTRIBUTE = "column_malformed_attribute"
    COLUMN_UNKNOWN_ATTRIBUTE = "column_unknown_attribute"
    COLUMN_VALUE_INVALID = "column_value_invalid"
    COLUMN_UNKNOWN_TYPE = "column_unknown_type"
    COLUMN_NOT_UNIQUE = "column_not_unique"
    AGG_STALE = "agg_stale"
    UNKNOWN_META_LINE = "unknown_meta_line"
    DUPLICATE_COLUMN = "duplicate_column"
    COLUMN_COUNT_MISMATCH = "column_count_mismatch"
    DEFAULT_WITH_NULLS = "default_with_nulls"
    AGG_ARITY_MISMATCH = "agg_arity_mismatch"
    AGG_TYPE_INCOMPATIBLE = "agg_type_incompatible"
    SQL_MISSING_COLON = "sql_missing_colon"
    SQL_UNKNOWN_VERB = "sql_unknown_verb"
    SQL_EMBEDDED_NEWLINE = "sql_embedded_newline"
    SQL_UNKNOWN_DIALECT = "sql_unknown_dialect"
    SQL_DIALECT_FAMILY = "sql_dialect_family"
    SQL_VERSION_MISMATCH = "sql_version_mismatch"
    SQL_NO_MATCH = "sql_no_match"
    DDL_COLUMN_MISMATCH = "ddl_column_mismatch"
    DATA_ROW_ARITY_MISMATCH = "data_row_arity_mismatch"
    QUOTE_NONE_DELIMITER_IN_VALUE = "quote_none_delimiter_in_value"
    FIRST_FIELD_HASH_UNQUOTED = "first_field_hash_unquoted"
    QUOTED_VALUE_RAW_NEWLINE = "quoted_value_raw_newline"
    CHECKSUM_MISMATCH = "checksum_mismatch"
    CHECKSUM_UNKNOWN_ALGORITHM = "checksum_unknown_algorithm"
    CHECKSUM_MALFORMED = "checksum_malformed"
    INVALID_UTF8 = "invalid_utf8"
    ENCODING_MISMATCH = "encoding_mismatch"
    ENCODING_NOT_ASCII_COMPATIBLE = "encoding_not_ascii_compatible"
    ENCODING_UNSUPPORTED = "encoding_unsupported"
    ZIP_MISSING_ORIGINAL_SIZE = "zip_missing_original_size"
    ZIP_ORIGINAL_SIZE_MISMATCH = "zip_original_size_mismatch"
    ZIP_PRIMARY_NOT_FIRST = "zip_primary_not_first"
    ZIP_PRIMARY_BAD_NAME = "zip_primary_bad_name"
    ZIP_PRIMARY_MISSING = "zip_primary_missing"
    ZIP_COMMENT_NOT_EXCSV_PREFIX = "zip_comment_not_excsv_prefix"
    ZIP_COMMENT_NOT_UTF8 = "zip_comment_not_utf8"
    ZIP_COMMENT_HEADER_DISAGREE = "zip_comment_header_disagree"
    ZIP_UNSUPPORTED_COMPRESSION = "zip_unsupported_compression"
    ZIP_ENCRYPTED = "zip_encrypted"
    ZIP_PASSWORD_REQUIRED = "zip_password_required"
    ZIP_WRONG_PASSWORD = "zip_wrong_password"
    ROW_PARSER_GOT_PACK = "row_parser_got_pack"
    PACK_KEY_ON_PLAIN = "pack_key_on_plain"
    PACK_MANIFEST_MISSING_LAYOUT = "pack_manifest_missing_layout"
    PACK_TABLE_DIR_MISSING = "pack_table_dir_missing"
    PACK_TABLE_HEADER_MISSING = "pack_table_header_missing"
    PACK_COLUMN_COUNT_MISMATCH = "pack_column_count_mismatch"
    PACK_COL_LINE_COUNT_MISMATCH = "pack_col_line_count_mismatch"
    PACK_SECTION_PARTITION_ERROR = "pack_section_partition_error"
    PACK_SECTION_BOUNDARY_MISMATCH = "pack_section_boundary_mismatch"
    ORIGINAL_SIZE_ON_PLAIN = "original_size_on_plain"
    ROWS_MISMATCH = "rows_mismatch"
    COLUMNS_MISMATCH = "columns_mismatch"
    SIDECAR_HAS_DATA_SECTION = "sidecar_has_data_section"
    SIDECAR_MISSING_REFERENCE = "sidecar_missing_reference"
    SIDECAR_REFERENCE_NOT_FOUND = "sidecar_reference_not_found"
    SIDECAR_REFERENCE_ESCAPES_DIR = "sidecar_reference_escapes_dir"
    EXTSV_DELIM_MISMATCH = "extsv_delim_mismatch"
    SIDECAR_CHECKSUM_MISMATCH = "sidecar_checksum_mismatch"
    REFERENCE_ON_INLINE = "reference_on_inline"

    # Computed columns (formula=/materialized=), added in v0.5.
    FORMULA_REFERENCES_COMPUTED = "formula_references_computed"
    FORMULA_UNKNOWN_REFERENCE = "formula_unknown_reference"
    FORMULA_PARSE_ERROR = "formula_parse_error"
    FORMULA_INDEX_FORBIDDEN = "formula_index_forbidden"
    FORMULA_REQUIRES_HEADER = "formula_requires_header"
    COMPUTED_MATERIALIZED_MISMATCH = "computed_materialized_mismatch"
    COMPUTED_DEFAULT_IGNORED = "computed_default_ignored"
    COMPUTED_STALE = "computed_stale"

    # Charts (#chart / #chart-<engine>:).
    CHART_MISSING_TYPE = "chart_missing_type"
    CHART_MISSING_NAME = "chart_missing_name"
    CHART_UNKNOWN_COLUMN = "chart_unknown_column"
    CHART_MISSING_REQUIRED_CHANNEL = "chart_missing_required_channel"
    CHART_VEGA_INVALID_JSON = "chart_vega_invalid_json"
    CHART_DUPLICATE_NAME = "chart_duplicate_name"
    CHART_ON_MANIFEST = "chart_on_manifest"
    CHART_UNKNOWN_TYPE = "chart_unknown_type"
    CHART_UNKNOWN_CHANNEL = "chart_unknown_channel"

    # Notes and links (#note, link=, #link), added in v0.6.
    NOTE_MALFORMED = "note_malformed"
    NOTE_MISSING_TEXT = "note_missing_text"
    NOTE_ROW_AND_KEY = "note_row_and_key"
    NOTE_UNRESOLVED = "note_unresolved"
    NOTE_ON_MANIFEST = "note_on_manifest"
    LINK_MALFORMED = "link_malformed"
    LINK_MISSING_HREF = "link_missing_href"
    LINK_MISSING_ADDRESS = "link_missing_address"
    LINK_ROW_AND_KEY = "link_row_and_key"
    LINK_UNRESOLVED = "link_unresolved"
    LINK_DUPLICATE = "link_duplicate"
    LINK_ON_MANIFEST = "link_on_manifest"
    LINK_UNKNOWN_COLUMN = "link_unknown_column"
    LINK_TEMPLATE_MALFORMED = "link_template_malformed"
    LINK_UNSAFE_SCHEME = "link_unsafe_scheme"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


@dataclass
class Issue:
    kind: ErrorKind
    message: str = ""
    line: int = 0

    def __str__(self) -> str:
        if self.line > 0:
            return f"{self.kind.value} at line {self.line}: {self.message}"
        return f"{self.kind.value}: {self.message}"


class ParseError(Exception):
    """Wraps an Issue -- mirrors Go's *ParseError."""

    def __init__(self, issue: Issue):
        super().__init__(str(issue))
        self.issue = issue

    @property
    def kind(self) -> ErrorKind:
        return self.issue.kind


# Codes found by a declaration check (computed columns, charts, notes and
# links) that are FAIL severity in the spec's registry, as opposed to WARN.
_DECLARATION_FAIL_KINDS = frozenset({
    ErrorKind.FORMULA_REFERENCES_COMPUTED,
    ErrorKind.FORMULA_UNKNOWN_REFERENCE,
    ErrorKind.FORMULA_PARSE_ERROR,
    ErrorKind.FORMULA_INDEX_FORBIDDEN,
    ErrorKind.FORMULA_REQUIRES_HEADER,
    ErrorKind.COMPUTED_MATERIALIZED_MISMATCH,
    ErrorKind.CHART_MISSING_TYPE,
    ErrorKind.CHART_MISSING_NAME,
    ErrorKind.CHART_UNKNOWN_COLUMN,
    ErrorKind.CHART_MISSING_REQUIRED_CHANNEL,
    ErrorKind.CHART_VEGA_INVALID_JSON,
    ErrorKind.NOTE_MALFORMED,
    ErrorKind.NOTE_MISSING_TEXT,
    ErrorKind.NOTE_ROW_AND_KEY,
    ErrorKind.LINK_MALFORMED,
    ErrorKind.LINK_MISSING_HREF,
    ErrorKind.LINK_MISSING_ADDRESS,
    ErrorKind.LINK_ROW_AND_KEY,
})


def is_fail_kind(kind: ErrorKind) -> bool:
    """Reports whether a declaration-check code is FAIL severity (vs WARN)."""
    return kind in _DECLARATION_FAIL_KINDS


def new_issue(kind: ErrorKind, line: int, msg: str) -> Issue:
    return Issue(kind=kind, message=msg, line=line)


def fail(kind: ErrorKind, line: int, msg: str) -> ParseError:
    return ParseError(new_issue(kind, line, msg))
