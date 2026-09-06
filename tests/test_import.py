"""Port of pkg/excsv/import_test.go."""

import pytest

import excsv


def test_import_delimited_minimal_csv():
    res = excsv.import_delimited(b"a,b\n1,2\n", excsv.ImportOptions(strict=True))
    doc = res.doc
    assert doc.header.version == excsv.CURRENT_VERSION
    assert doc.header.delim_name == "comma"
    assert doc.data.has_header_row and len(doc.data.header_row) == 2
    assert len(doc.data.rows) == 1 and doc.data.rows[0][0] == "1"
    assert doc.header.rows == 1

    out = doc.serialize_canonical()
    parsed = excsv.parse_bytes(out, excsv.strict_options())
    assert parsed.doc.row_count() == 1


def test_import_delimited_tsv_sniff():
    res = excsv.import_delimited(b"a\tb\n1\t2\n", excsv.ImportOptions(strict=True, source_path="data.tsv"))
    assert res.doc.header.delim_name == "tab"


def test_import_delimited_quoted_csv():
    res = excsv.import_delimited(b'"a,b",c\n"d,e",f\n', excsv.ImportOptions(strict=True))
    assert res.doc.header.quote_name == "double"
    assert res.doc.data.header_row[0] == "a,b"


def test_import_delimited_no_header():
    res = excsv.import_delimited(b"1,2\n3,4\n", excsv.ImportOptions(strict=True, no_header=True))
    assert not res.doc.header.header_row
    assert len(res.doc.data.rows) == 2


def test_import_delimited_columns():
    res = excsv.import_delimited(b"id,name\n1,alice\n", excsv.ImportOptions(strict=True))
    assert len(res.doc.meta.columns) == 2
    names = {col.attrs.get("name") for col in res.doc.meta.columns}
    assert "id" in names and "name" in names


def test_import_delimited_sanitized_column_name():
    res = excsv.import_delimited(b"Total Sales,x\n1,2\n", excsv.ImportOptions(strict=True))
    assert len(res.doc.meta.columns) == 2
    assert res.doc.meta.columns[0].attrs["name"] == "Total_Sales"
    assert res.doc.meta.columns[0].attrs["title"] == "Total Sales"


def test_import_delimited_checksum():
    res = excsv.import_delimited(b"a,b\n1,2\n", excsv.ImportOptions(strict=True))
    assert res.doc.header.checksum is not None
    out = res.doc.serialize_canonical()
    excsv.parse_bytes(out, excsv.strict_options())  # must not raise


def test_import_delimited_ragged_strict():
    with pytest.raises(Exception):
        excsv.import_delimited(b"a,b\n1,2,3\n", excsv.ImportOptions(strict=True))
