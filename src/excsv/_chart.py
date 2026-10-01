"""#chart suggestions (implementation/charts.md): parsing, checks, CRUD.

A chart is advisory: a consumer that ignores every #chart line still has a
fully valid file. The FAIL codes (missing type=/name=, unknown column, missing
required channel, invalid #chart-vega JSON) are still enforced on read.
"""

from __future__ import annotations

import json

from ._column import format_column_attr
from ._document import CHART_COUNT_LITERAL, Chart, Document
from ._errors import ErrorKind, Issue, ParseError, is_fail_kind, new_issue
from ._kv import split_header_pairs

# The only #chart-<engine>: suffix the spec defines.
CHART_ENGINE_VEGA = "vega"

# Encoding channels; each takes a column name.
CHART_CHANNELS = (
    "x", "y", "x2", "y2", "color", "size", "theta", "radius", "shape",
    "opacity", "column", "row", "detail", "order", "tooltip", "text",
)

# Non-channel attributes of a compact-form line.
CHART_MODIFIERS = ("type", "name", "title", "aggregate", "bin", "stack", "sort", "limit", "hole")

# Per recognized mark, alternatives of channel sets: at least one alternative
# must be fully present.
CHART_REQUIRED_CHANNELS: dict[str, tuple[tuple[str, ...], ...]] = {
    "bar": (("x", "y"),),
    "line": (("x", "y"),),
    "area": (("x", "y"),),
    "point": (("x",), ("y",)),
    "circle": (("x", "y"),),
    "arc": (("theta",),),
    "rect": (("x", "y", "color"),),
    "tick": (("x",), ("y",)),
    "boxplot": (("x",), ("y",)),
    "text": (("x", "y", "text"),),
    "sparkline": (("y",),),
}

CHART_MARKS = tuple(CHART_REQUIRED_CHANNELS)

_KNOWN_CHANNELS = frozenset(CHART_CHANNELS)
_KNOWN_MODIFIERS = frozenset(CHART_MODIFIERS)


def chart_channel_values(key: str, value: str) -> list[str]:
    """Splits a channel value into column names: tooltip= takes a
    comma-separated list, every other channel exactly one name."""
    if key != "tooltip":
        return [value]
    return [part.strip() for part in value.split(",") if part.strip()]


def chart_column_refs(chart: Chart) -> list[str]:
    """Every column name the chart's channels reference, in attribute order.
    The count() literal is not a column and is left out."""
    out = []
    for k, v in chart.attrs.items():
        if k not in _KNOWN_CHANNELS:
            continue
        out.extend(ref for ref in chart_channel_values(k, v) if ref != CHART_COUNT_LITERAL)
    return out


def chart_meta_line(chart: Chart) -> str:
    """Renders the chart back to its meta-line form."""
    if chart.is_escape:
        return "#chart-" + chart.engine + ": " + chart.payload
    parts = ["#chart"] + [format_column_attr(k, v) for k, v in chart.attrs.items()]
    return " ".join(parts)


def parse_chart_line(line: str, line_no: int) -> tuple[Chart | None, list[str]]:
    """Parses a line starting with "#chart".

    Returns (chart, dropped_tokens). chart is None when the line only looks
    like a chart (e.g. "#chartx", or "#chart-vega" without a colon) and must be
    carried through as an unrecognized meta line. dropped_tokens are compact
    tokens that are not key=value; the caller warns chart_unknown_channel.
    """
    rest = line[len("#chart"):]
    if rest.startswith("-"):
        colon = rest.find(":")
        if colon < 2:
            return None, []
        payload = rest[colon + 1:]
        if payload.startswith(" "):
            payload = payload[1:]
        return Chart(engine=rest[1:colon], payload=payload, line=line_no), []
    if rest and rest[0] not in " \t":
        return None, []
    try:
        pairs = split_header_pairs(rest.strip(), line_no)
    except ParseError as e:
        e.issue.line = line_no
        raise
    chart = Chart(line=line_no)
    dropped = []
    for p in pairs:
        eq = p.find("=")
        if eq <= 0:
            dropped.append(p)
            continue
        chart.attrs[p[:eq]] = p[eq + 1:]
    return chart, dropped


def check_charts(doc: Document) -> list[Issue]:
    """Runs every #chart rule against the document's #column declarations.
    FAIL and WARN codes are both returned; split them with is_fail_kind."""
    if not doc.meta.charts:
        return []
    declared = {col.attrs["name"] for col in doc.meta.columns if col.attrs.get("name")}
    issues: list[Issue] = []
    seen_names: set[str] = set()
    for c in doc.meta.charts:
        if c.is_escape:
            if c.engine != CHART_ENGINE_VEGA:
                issues.append(new_issue(ErrorKind.CHART_UNKNOWN_TYPE, c.line,
                                        "unrecognized #chart-" + c.engine + ": engine; ignored"))
                continue
            try:
                json.loads(c.payload)
            except ValueError:
                issues.append(new_issue(ErrorKind.CHART_VEGA_INVALID_JSON, c.line,
                                        "#chart-vega: payload is not valid JSON"))
            continue

        mark = c.attrs.get("type", "")
        if not mark:
            issues.append(new_issue(ErrorKind.CHART_MISSING_TYPE, c.line, "#chart lacks type="))
            continue
        name = c.attrs.get("name", "")
        if not name:
            issues.append(new_issue(ErrorKind.CHART_MISSING_NAME, c.line, "#chart lacks name="))
            continue
        label = "chart " + name + ": "
        if name in seen_names:
            issues.append(new_issue(ErrorKind.CHART_DUPLICATE_NAME, c.line, label + "duplicate name=; last-wins"))
        seen_names.add(name)

        for k in c.attrs:
            if k not in _KNOWN_CHANNELS and k not in _KNOWN_MODIFIERS:
                issues.append(new_issue(ErrorKind.CHART_UNKNOWN_CHANNEL, c.line,
                                        label + "unknown attribute " + k + "; ignored"))
        for ref in chart_column_refs(c):
            if ref not in declared:
                issues.append(new_issue(ErrorKind.CHART_UNKNOWN_COLUMN, c.line,
                                        label + "channel references undeclared column " + ref))

        required = CHART_REQUIRED_CHANNELS.get(mark)
        if required is None:
            issues.append(new_issue(ErrorKind.CHART_UNKNOWN_TYPE, c.line,
                                    label + "unrecognized type=" + mark + "; preserved"))
            continue
        if not any(all(c.attrs.get(ch) for ch in alt) for alt in required):
            want = " or ".join("+".join(alt) for alt in required)
            issues.append(new_issue(ErrorKind.CHART_MISSING_REQUIRED_CHANNEL, c.line,
                                    label + "type=" + mark + " requires " + want))
    return issues


def chart_by_name(doc: Document, name: str) -> Chart | None:
    """The compact-form chart addressed by name=. When several lines share a
    name, the last one wins (chart_duplicate_name)."""
    for c in reversed(doc.meta.charts):
        if not c.is_escape and c.name == name:
            return c
    return None


def add_chart(doc: Document, attrs: dict[str, str]) -> None:
    """Appends a compact-form #chart built from attrs (in the given order).

    type= and name= are required, and every channel must reference a declared
    column; a failing chart raises ParseError and leaves the document as is. A
    chart whose name= is already used replaces the earlier one in place.
    """
    chart = Chart(attrs=dict(attrs))
    saved = doc.meta.charts
    doc.meta.charts = [chart]
    try:
        issues = check_charts(doc)
    finally:
        doc.meta.charts = saved
    for iss in issues:
        if is_fail_kind(iss.kind):
            raise ParseError(iss)
    for i, existing in enumerate(doc.meta.charts):
        if not existing.is_escape and existing.name == chart.name:
            doc.meta.charts[i] = chart
            return
    doc.meta.charts.append(chart)


def remove_chart(doc: Document, name: str) -> bool:
    """Removes every compact-form #chart with the given name=. Returns False
    if none matched."""
    before = len(doc.meta.charts)
    doc.meta.charts = [c for c in doc.meta.charts if c.is_escape or c.name != name]
    return len(doc.meta.charts) != before


Chart.column_refs = chart_column_refs
Chart.meta_line = chart_meta_line
Document.chart_by_name = chart_by_name
Document.add_chart = add_chart
Document.remove_chart = remove_chart
