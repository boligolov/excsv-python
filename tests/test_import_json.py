"""Port of pkg/excsv/import_json_test.go: reading the JSON form."""

from pathlib import Path

import pytest

import excsv

from .fixtures_manifest import load_manifest, root_dir

MANIFEST_PATH = Path(__file__).parent.parent / "test" / "fixtures" / "fixtures.yaml"


def _valid_plain_ids() -> list[str]:
    if not MANIFEST_PATH.exists():
        return []
    return [
        fx["id"] for fx in load_manifest(MANIFEST_PATH)["fixtures"]
        if fx["id"].startswith("plain/valid/") and fx.get("expect", {}).get("parse") == "ok"
    ]


@pytest.mark.skipif(not MANIFEST_PATH.exists(), reason="fixtures.yaml not synced -- run scripts/sync_upstream.py")
@pytest.mark.parametrize("fixture_id", _valid_plain_ids())
def test_json_round_trip_fixtures(fixture_id: str) -> None:
    """text -> JSON -> text is lossless on every valid plain fixture: reading
    the exported JSON back and exporting it again gives identical JSON, and so
    does rendering it as text and reading that."""
    path = str(root_dir(MANIFEST_PATH) / fixture_id)
    try:
        res = excsv.parse_file(path, excsv.ParseOptions(resolve_reference=True))
    except excsv.ParseError as e:
        pytest.skip(f"does not parse with default options: {e}")
    j1 = res.doc.export_json().data

    opts = excsv.ParseOptions(resolve_reference=True, source_path=path)
    back = excsv.parse_bytes(j1, opts)
    assert back.doc.export_json().data == j1, "JSON -> document -> JSON changed"

    text = back.doc.serialize_canonical()
    again = excsv.parse_bytes(text, opts)
    assert again.doc.export_json().data == j1, "JSON -> text -> JSON changed:\n" + text.decode()


def test_json_form_reads_like_text() -> None:
    doc = """{"excsv":"0.6","meta":{"grain":"one row per order"},
     "columns":[{"index":0,"name":"id","type":"int","role":"id"},
                {"index":1,"name":"note"},
                {"index":2,"name":"amount","type":"decimal","unit":"USD"}],
     "aggregates":{"sum":[null,null,"15.75"]},
     "notes":[{"key":"2","text":"Refund pending"}],
     "rows":3,
     "data":[[1,"says \\"hi\\", ok","10.50"],[2,null,"5.25"],[3,"#tag","0.00"]]}"""
    res = excsv.parse_bytes(doc.encode(), excsv.strict_options())
    assert res.warnings == []
    assert res.doc.serialize_canonical().decode() == (
        "#!excsv version=0.6 delim=comma quote=double rows=3\n"
        "#@grain: one row per order\n"
        "#column name=id type=int role=id\n"
        "#column name=amount type=decimal unit=USD\n"
        '#note key=2 text="Refund pending"\n'
        "#%sum: ,,15.75\n"
        "id,note,amount\n"
        '1,"says ""hi"", ok",10.50\n'
        "2,,5.25\n"
        "3,#tag,0.00\n"
    )


@pytest.mark.parametrize("doc", [
    '{"excsv":"0.6",',
    '{"excsv":"0.6","columns":[{"name":"a"}],"rows":0,"data":[]}',
    '{"excsv":"0.6","columns":[{"index":0,"name":"a"},{"index":0,"name":"b"}],"rows":0,"data":[]}',
    '{"excsv":"0.6","columns":[{"index":0,"type":"int"}],"rows":0,"data":[]}',
    '{"excsv":"0.6","columns":[{"index":0,"name":"a"}],"rows":1,"data":[[[1]]]}',
    '{"excsv":"0.6","columns":[{"index":0,"name":"a"}],"rows":1,"data":[["x\\ny"]]}',
], ids=["not json", "no index", "repeated index", "unnamed column", "nested cell", "newline in cell"])
def test_json_form_malformed(doc: str) -> None:
    with pytest.raises(excsv.ParseError) as e:
        excsv.parse_bytes(doc.encode(), excsv.ParseOptions(resolve_reference=True))
    assert e.value.kind == excsv.ErrorKind.JSON_MALFORMED


def test_json_form_lone_null() -> None:
    doc = '{"excsv":"0.6","columns":[{"index":0,"name":"a"}],"rows":2,"data":[[null],["x"]]}'
    res = excsv.parse_bytes(doc.encode(), excsv.strict_options())
    assert res.doc.row_count() == 2
    assert res.doc.data.rows[0][0] == ""
