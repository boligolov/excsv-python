"""Export-side tests: JSON (.excsv.json) and CSVW sidecar."""

import json

import pytest

import excsv


def parse_doc(src: str) -> excsv.Document:
    return excsv.parse_bytes(src.encode(), excsv.strict_options()).doc


SRC = (
    "#!excsv version=0.5 header=1 rows=2\n"
    "#column name=id type=int unique=1\n"
    "#column name=amount type=decimal format=\"0.00\"\n"
    "#column name=status type=string enum=ok|bad\n"
    "#$ddl: CREATE TABLE t (id INT)\n"
    "#%sum: ,30.00,\n"
    "id,amount,status\n"
    "1,10.00,ok\n"
    "2,20.00,bad\n"
)


def test_export_json_round_trips_scalars():
    doc = parse_doc(SRC)
    result = doc.export_json()
    obj = json.loads(result.data)
    assert obj["excsv"] == "0.5"
    assert obj["layout"] == "inline"
    assert obj["rows"] == 2
    assert obj["data"] == [[1, "10.00", "ok"], [2, "20.00", "bad"]]
    assert obj["columns"][0]["unique"] is True
    assert obj["columns"][2]["enum"] == ["ok", "bad"]
    assert obj["sql"]["ddl"][0]["stmt"] == "CREATE TABLE t (id INT)"
    assert obj["aggregates"]["sum"] == [None, "30.00", None]


def test_export_json_drops_human_comments():
    doc = parse_doc(SRC)
    doc.add_human_comment("a note")
    result = doc.export_json()
    assert any("human comments" in d for d in result.dropped)


def test_export_json_sidecar_uses_reference():
    doc = excsv.Document(
        header=excsv.Header(has_magic_line=True, version="0.5", fields={"reference": "sales.csv"}),
        source=excsv.SourceInfo(reference="sales.csv"),
    )
    from excsv._dialect import apply_header_defaults

    apply_header_defaults(doc.header)
    result = doc.export_json()
    obj = json.loads(result.data)
    assert obj["layout"] == "sidecar"
    assert obj["reference"] == "sales.csv"
    assert "data" not in obj


def test_export_csvw_requires_url_for_inline():
    doc = parse_doc(SRC)
    with pytest.raises(ValueError):
        doc.export_csvw(excsv.CSVWExportOptions())


def test_export_csvw_basic():
    doc = parse_doc(SRC)
    result = doc.export_csvw(excsv.CSVWExportOptions(url="data.csv"))
    obj = json.loads(result.data)
    assert obj["url"] == "data.csv"
    cols = obj["tableSchema"]["columns"]
    assert cols[0]["name"] == "id"
    assert cols[0]["datatype"]["base"] == "int"
    assert any("unique=" in d for d in result.dropped)
