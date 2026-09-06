"""Port of pkg/excsv/repair_test.go."""

import pytest

import excsv


def parse_doc(src: str) -> excsv.Document:
    res = excsv.parse_bytes(src.encode(), excsv.strict_options())
    return res.doc


def test_set_header_field_converts_delim():
    doc = parse_doc("#!excsv version=0.3\n#column name=id\n#column name=n\nid,n\n1,a\n2,b\n")
    doc.set_header_field("delim", "tab")
    text = doc.serialize_canonical().decode()
    assert "delim=tab" in text
    assert "1\ta" in text
    assert "1,a" not in text


def test_set_header_field_header_zero():
    doc = parse_doc("#!excsv version=0.3\n#column name=id\n#column name=n\nid,n\n1,a\n")
    doc.set_header_field("header", "0")
    text = doc.serialize_canonical().decode()
    assert "header=0" in text
    assert "\nid,n\n" not in text
    assert "1,a" in text


def test_set_header_field_null_remap():
    doc = parse_doc("#!excsv version=0.3\n#column name=id\n#column name=n\nid,n\n1,\n")
    doc.set_header_field("null", "NA")
    assert doc.data.rows[0][1] == "NA"


def test_tidy_pads_rows():
    doc = parse_doc("#!excsv version=0.3\n#column name=a\n#column name=b\na,b\n1,2\n")
    doc.data.rows[0] = ["1"]
    doc.tidy()
    assert len(doc.data.rows[0]) == 2


def test_fix_infers_and_checksums():
    doc = parse_doc("#!excsv version=0.4\nid,amount\n1,10.5\n2,3\n")
    report = doc.fix(excsv.FixOptions(only=["columns", "checksum", "stamp"]))
    assert report.changed
    assert len(doc.meta.columns) == 2
    assert doc.meta.columns[1].attrs["type"] in ("double", "decimal", "int")
    assert doc.header.checksum is not None
    exported = next((kv.value for kv in doc.meta.file_meta if kv.key == "exported"), "")
    assert exported
    assert len(doc.meta.aggregations) == 0  # fix must not invent aggregations


def test_validate_escalates_rows_mismatch():
    res = excsv.parse_bytes(b"#!excsv version=0.4 rows=99\n#column name=a\na\n1\n", excsv.strict_options())
    report = res.doc.validate(excsv.ValidateOptions(with_data=True))
    assert not report.ok()
    assert report.findings[0].issue.kind == excsv.ErrorKind.ROWS_MISMATCH


def test_quote_none_rejects_delimiter_in_value():
    doc = parse_doc('#!excsv version=0.3 quote=double\n#column name=a\n#column name=b\na,b\n"x,y",z\n')
    with pytest.raises(Exception):
        doc.set_header_field("quote", "none")
