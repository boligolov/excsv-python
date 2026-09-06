"""ImportDelimited: CSV/TSV -> ExCSV conversion, with delimiter/quote sniffing."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ._bom import strip_utf8_bom
from ._csv import split_csv_fields
from ._dialect import apply_header_defaults, resolve_delim, resolve_quote
from ._document import CURRENT_VERSION, DataSection, Dialect, Document, Form, Header, KV, Profile
from ._errors import ErrorKind, Issue, ParseError, fail
from ._kv import valid_utf8
from ._path_delim import delim_name_for_path
from ._records import lines_from_records, split_records
from ._rowparse import first_field_hash_error, parse_csv_row

# Swappable so tests can pin #@created.
import_clock = lambda: datetime.now(timezone.utc)  # noqa: E731


@dataclass
class ColumnAttr:
    column: str
    attr: str
    value: str


@dataclass
class ImportOptions:
    delim_name: str = ""
    quote_name: str = ""
    no_header: bool = False
    encoding: str = ""
    null: str = ""
    no_checksum: bool = False
    strict: bool = False
    sidecar: bool = False
    reference: str = ""
    file_meta: list[KV] = field(default_factory=list)
    source_path: str = ""

    aggregations: list[str] = field(default_factory=list)
    comments: list[str] = field(default_factory=list)
    sql: list[KV] = field(default_factory=list)
    column_attrs: list[ColumnAttr] = field(default_factory=list)


@dataclass
class ImportResult:
    doc: Document
    warnings: list[Issue] = field(default_factory=list)


def import_delimited(data: bytes, opts: ImportOptions) -> ImportResult:
    from ._records import extract_data_section

    data = strip_utf8_bom(data)
    if data and not valid_utf8(data):
        raise fail(ErrorKind.INVALID_UTF8, 0, "invalid UTF-8")

    if not data:
        return _minimal_import_doc()

    lines, line_nums = lines_from_records(split_records(data))

    if _all_lines_empty(lines):
        return _minimal_import_doc()

    input_delim_name = _resolve_import_delim("", lines, opts.source_path)
    input_delim = resolve_delim(input_delim_name)

    input_quote_name = _sniff_input_quote(lines, input_delim)
    input_quote, input_quote_enabled = resolve_quote(input_quote_name)
    input_d = Dialect(delim=input_delim, quote=input_quote, quote_enabled=input_quote_enabled)

    output_delim_name = opts.delim_name or input_delim_name
    if opts.delim_name:
        resolve_delim(output_delim_name)

    output_quote_name = opts.quote_name or input_quote_name
    if opts.quote_name:
        resolve_quote(output_quote_name)

    has_header = not opts.no_header
    warnings: list[Issue] = []
    header_row: list[str] = []
    data_rows: list[list[str]] = []
    expected_cols = 0

    if has_header:
        try:
            fields = split_csv_fields(lines[0], input_d)
        except ParseError as e:
            e.issue.line = line_nums[0]
            raise
        first_field_hash_error(lines[0], fields, input_d, line_nums[0])
        header_row = fields
        expected_cols = len(fields)
        for i in range(1, len(lines)):
            row, w = parse_csv_row(lines[i], line_nums[i], input_d, expected_cols, input_quote_name, opts.strict)
            warnings.extend(w)
            data_rows.append(row)
    else:
        for i, line in enumerate(lines):
            row, w = parse_csv_row(line, line_nums[i], input_d, expected_cols, input_quote_name, opts.strict)
            warnings.extend(w)
            data_rows.append(row)
            if expected_cols == 0 and row:
                expected_cols = len(row)

    if not output_quote_name:
        output_quote_name = "none"
    fields = {
        "version": CURRENT_VERSION,
        "delim": output_delim_name,
        "quote": output_quote_name,
        "rows": str(len(data_rows)),
    }
    if not has_header:
        fields["header"] = "0"
    enc = opts.encoding.strip()
    if enc:
        if enc.upper() not in ("UTF-8", "UTF8"):
            raise fail(ErrorKind.ENCODING_UNSUPPORTED, 1, "convert writes UTF-8 only, got encoding=" + enc)
        fields["encoding"] = "UTF-8"

    doc = Document(
        form=Form.PLAIN,
        header=Header(fields=fields, has_magic_line=True, version=CURRENT_VERSION),
        data=DataSection(has_header_row=has_header, header_row=header_row, rows=data_rows),
    )
    apply_header_defaults(doc.header)

    if opts.null:
        from ._repair import _convert_null

        _convert_null(doc, opts.null)

    doc.set_file_meta("created", import_clock().astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    if opts.source_path:
        doc.set_file_meta("source", os.path.basename(opts.source_path))
    doc.set_file_meta("tool", "excsv-python")
    for kv in opts.file_meta:
        doc.set_file_meta(kv.key, kv.value)

    doc.infer_columns_from_header(header_row, has_header)
    for ca in opts.column_attrs:
        doc.upsert_column(ca.column, {ca.attr: ca.value})

    from ._repair import cells_contain_rune

    if not doc.header.quote_enabled and cells_contain_rune(doc, doc.header.delim):
        doc.header.fields["quote"] = "double"
        apply_header_defaults(doc.header)
        warnings.append(Issue(
            ErrorKind.QUOTE_NONE_DELIMITER_IN_VALUE, "promoted quote=none to quote=double because a value contains the delimiter", 1,
        ))

    for name in opts.aggregations:
        doc.add_aggregation(name)
    for kv in opts.sql:
        doc.set_sql(kv.key, kv.value)
    for text in opts.comments:
        doc.add_human_comment(text)

    if opts.sidecar:
        ref = opts.reference or os.path.basename(opts.source_path)
        doc.source.profile = Profile.SIDECAR
        doc.source.reference = ref
        doc.source.sidecar_path = opts.source_path
        doc.header.fields["reference"] = ref
        if not opts.no_checksum:
            records = split_records(data)
            section = extract_data_section(data.decode("utf-8", errors="surrogateescape"), records, 0)
            doc.set_data_checksum_from_section(section, "sha256")
        doc.data = DataSection()
    elif not opts.no_checksum:
        doc.set_data_checksum("sha256")

    return ImportResult(doc=doc, warnings=warnings)


def parse_column_attr(spec: str) -> ColumnAttr:
    if "=" not in spec:
        raise ValueError(f"invalid --column-attr {spec!r} (expected COLUMN.ATTR=VALUE)")
    lhs, value = spec.split("=", 1)
    lhs = lhs.strip()
    if "." not in lhs:
        raise ValueError(f"invalid --column-attr {spec!r} (expected COLUMN.ATTR=VALUE)")
    col, attr = lhs.split(".", 1)
    if not col or not attr:
        raise ValueError(f"invalid --column-attr {spec!r} (expected COLUMN.ATTR=VALUE)")
    return ColumnAttr(column=col, attr=attr, value=value.strip())


def expand_aggregation_list(list_str: str) -> list[str]:
    from ._agg import ALL_AGGREGATIONS
    from ._repair import DEFAULT_AGGREGATIONS

    list_str = list_str.strip()
    if not list_str:
        return []
    out: list[str] = []
    seen: set[str] = set()

    def add(name: str) -> None:
        if not name or name in seen:
            return
        seen.add(name)
        out.append(name)

    for part in list_str.split(","):
        part = part.strip()
        if part == "":
            continue
        if part == "default":
            for name in DEFAULT_AGGREGATIONS:
                add(name)
        elif part == "all":
            for name in ALL_AGGREGATIONS:
                add(name)
        else:
            add(part)
    return out


_SNIFF_DELIMS = [("comma", ","), ("tab", "\t"), ("semicolon", ";"), ("pipe", "|")]


def _resolve_import_delim(explicit: str, lines: list[str], source_path: str) -> str:
    if explicit:
        resolve_delim(explicit)
        return explicit
    preferred = delim_name_for_path(source_path)

    sample = _non_empty_lines(lines, 5)
    if not sample:
        return "comma"

    best_name = "comma"
    best_score = -1
    for name, r in _SNIFF_DELIMS:
        score = _score_delimiter(sample, r)
        if preferred == name:
            score += 1
        if score > best_score:
            best_score = score
            best_name = name
    if best_score <= 0:
        return preferred or "comma"
    return best_name


def _non_empty_lines(lines: list[str], max_n: int) -> list[str]:
    out = []
    for line in lines:
        if line == "":
            continue
        out.append(line)
        if len(out) >= max_n:
            break
    return out


def _score_delimiter(lines: list[str], delim: str) -> int:
    if not any(delim in line for line in lines):
        return -1
    d = Dialect(delim=delim, quote="", quote_enabled=False)
    counts = []
    for line in lines:
        try:
            fields = split_csv_fields(line, d)
        except ParseError:
            return -1
        if not fields:
            return -1
        counts.append(len(fields))
    if not counts:
        return 0
    first = counts[0]
    if any(c != first for c in counts[1:]):
        return 0
    return first * len(counts)


def _sniff_input_quote(lines: list[str], delim: str) -> str:
    sample = _non_empty_lines(lines, 8)
    if not sample:
        return "none"
    dq = Dialect(delim=delim, quote='"', quote_enabled=True)
    dn = Dialect(delim=delim, quote="", quote_enabled=False)
    double_ok = True
    none_ok = True
    for line in sample:
        if '"' in line:
            try:
                split_csv_fields(line, dq)
            except ParseError:
                double_ok = False
        try:
            split_csv_fields(line, dn)
        except ParseError:
            none_ok = False
    if double_ok and '"' in "\n".join(sample):
        return "double"
    if none_ok:
        return "none"
    return _sniff_quote(sample[0], delim)


def _sniff_quote(line: str, delim: str) -> str:
    d_quote = Dialect(delim=delim, quote='"', quote_enabled=True)
    try:
        fields = split_csv_fields(line, d_quote)
    except ParseError:
        return "none"
    trimmed = line.strip()
    if trimmed.startswith('"'):
        return "double"
    d_none = Dialect(delim=delim, quote="", quote_enabled=False)
    try:
        unquoted = split_csv_fields(line, d_none)
        if len(fields) > len(unquoted):
            return "double"
    except ParseError:
        pass
    return "none"


def _minimal_import_doc() -> ImportResult:
    doc = Document(
        form=Form.PLAIN,
        header=Header(fields={"version": CURRENT_VERSION}, has_magic_line=True, version=CURRENT_VERSION),
    )
    apply_header_defaults(doc.header)
    return ImportResult(doc=doc)


def _all_lines_empty(lines: list[str]) -> bool:
    return all(line == "" for line in lines)
