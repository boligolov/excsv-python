"""#chart / #chart-<engine>: parsing, checks, CRUD and the JSON mirror."""

import json

import pytest

import excsv

CHART_SCHEMA = (
    "#!excsv version=0.5 header=1 rows=2\n"
    "#column name=category type=string role=dimension\n"
    "#column name=amount type=decimal role=measure agg=sum\n"
)
CHART_DATA = "category,amount\nwidgets,1\ngadgets,2\n"


def parse(src: str) -> excsv.ParseResult:
    return excsv.parse_bytes(src.encode(), excsv.strict_options())


def test_chart_round_trip():
    src = (CHART_SCHEMA
           + '#chart type=bar name=top x=category y=amount sort=desc limit=10 title="Top categories"\n'
           + "#chart type=bar name=hist bin=20 x=amount y=count() tooltip=category,amount\n"
           + '#chart-vega: {"mark":"arc"}\n'
           + CHART_DATA)
    doc = parse(src).doc
    assert len(doc.meta.charts) == 3
    top = doc.chart_by_name("top")
    assert top.type == "bar" and top.attrs["title"] == "Top categories"
    assert doc.chart_by_name("hist").column_refs() == ["amount", "category", "amount"], "count() is not a column"
    vega = doc.meta.charts[2]
    assert vega.is_escape and vega.engine == excsv.CHART_ENGINE_VEGA

    out = doc.serialize_canonical().decode()
    assert '#chart type=bar name=top x=category y=amount sort=desc limit=10 title="Top categories"\n' in out
    assert "#chart type=bar name=hist bin=20 x=amount y=count() tooltip=category,amount\n" in out
    assert '#chart-vega: {"mark":"arc"}\n' in out
    assert out.index("#chart") > out.rindex("#column")
    assert len(parse(out).doc.meta.charts) == 3


@pytest.mark.parametrize("line,want", [
    ("#chart name=c x=category y=amount", excsv.ErrorKind.CHART_MISSING_TYPE),
    ("#chart type=bar x=category y=amount", excsv.ErrorKind.CHART_MISSING_NAME),
    ("#chart type=bar name=c x=category y=missing", excsv.ErrorKind.CHART_UNKNOWN_COLUMN),
    ("#chart type=bar name=c x=category", excsv.ErrorKind.CHART_MISSING_REQUIRED_CHANNEL),
    ("#chart type=arc name=c color=category", excsv.ErrorKind.CHART_MISSING_REQUIRED_CHANNEL),
    ("#chart type=rect name=c x=category y=amount", excsv.ErrorKind.CHART_MISSING_REQUIRED_CHANNEL),
    ("#chart type=bar name=c x=category y=amount tooltip=category,nope", excsv.ErrorKind.CHART_UNKNOWN_COLUMN),
    ('#chart-vega: {"mark":', excsv.ErrorKind.CHART_VEGA_INVALID_JSON),
])
def test_chart_parse_failures(line, want):
    with pytest.raises(excsv.ParseError) as exc_info:
        parse(CHART_SCHEMA + line + "\n" + CHART_DATA)
    assert exc_info.value.kind == want


@pytest.mark.parametrize("lines,want", [
    ("#chart type=radar name=c x=category y=amount", [excsv.ErrorKind.CHART_UNKNOWN_TYPE]),
    ("#chart-plotly: {}", [excsv.ErrorKind.CHART_UNKNOWN_TYPE]),
    ("#chart type=bar name=c x=category y=amount glow=1", [excsv.ErrorKind.CHART_UNKNOWN_CHANNEL]),
    ("#chart type=bar name=c x=category y=amount\n#chart type=arc name=c theta=amount",
     [excsv.ErrorKind.CHART_DUPLICATE_NAME]),
    # One-channel marks: point/tick/boxplot accept x or y alone.
    ("#chart type=point name=c x=amount\n#chart type=tick name=d y=amount\n#chart type=boxplot name=e y=amount", []),
])
def test_chart_warnings(lines, want):
    res = parse(CHART_SCHEMA + lines + "\n" + CHART_DATA)
    assert [w.kind for w in res.warnings] == want


def test_chart_duplicate_name_last_wins():
    doc = parse(CHART_SCHEMA + "#chart type=bar name=c x=category y=amount\n"
                "#chart type=arc name=c theta=amount\n" + CHART_DATA).doc
    assert doc.chart_by_name("c").type == "arc"


def test_chart_on_pack_manifest_ignored():
    opts = excsv.strict_options()
    opts.pack_role = "manifest"
    res = excsv.parse_bytes(b"#!excsv version=0.5 layout=pack original-size=0\n#chart type=bar name=c x=a y=b\n", opts)
    assert not res.doc.meta.charts
    assert [w.kind for w in res.warnings] == [excsv.ErrorKind.CHART_ON_MANIFEST]


def test_add_and_remove_chart():
    doc = parse(CHART_SCHEMA + CHART_DATA).doc
    with pytest.raises(excsv.ParseError) as exc_info:
        doc.add_chart({"type": "bar", "name": "c", "x": "category"})
    assert exc_info.value.kind == excsv.ErrorKind.CHART_MISSING_REQUIRED_CHANNEL
    assert not doc.meta.charts, "a failing chart must leave the document as is"

    doc.add_chart({"type": "bar", "name": "c", "x": "category", "y": "amount"})
    doc.add_chart({"type": "arc", "name": "c", "theta": "amount"})
    assert len(doc.meta.charts) == 1 and doc.meta.charts[0].type == "arc", "an existing name is replaced"
    assert doc.remove_chart("c") and not doc.meta.charts
    assert not doc.remove_chart("c")


def test_chart_json_mirror():
    doc = parse(CHART_SCHEMA
                + "#chart type=arc name=donut theta=amount color=category hole=0.5 stack=1 tooltip=category,amount\n"
                + "#chart type=bar name=hist bin=1 x=amount y=count() limit=5\n"
                + '#chart-vega: {"mark":"arc"}\n'
                + CHART_DATA).doc
    donut, hist, vega = json.loads(doc.export_json().data)["charts"]
    assert donut["hole"] == 0.5 and donut["stack"] is True
    assert donut["tooltip"] == ["category", "amount"]
    assert hist["bin"] is True and hist["limit"] == 5 and hist["y"] == "count()"
    assert vega == {"vega": {"mark": "arc"}}
