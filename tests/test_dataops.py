"""Port of pkg/excsv/dataops_test.go."""

import excsv


def parse_doc(src: str) -> excsv.Document:
    res = excsv.parse_bytes(src.encode(), excsv.strict_options())
    return res.doc


def test_append_and_sort():
    doc = parse_doc(
        "#!excsv version=0.2\n"
        "#column name=id type=int role=id\n"
        "#column name=amount type=decimal role=measure\n"
        "id,amount\n2,20\n1,10\n"
    )
    doc.append_rows([["3", "5"]], True)
    assert doc.row_count() == 3
    idx = doc.column_index("amount")
    doc.sort_rows([excsv.SortKey(index=idx, desc=False)])
    assert doc.data.rows[0][0] == "3"
    assert doc.data.rows[1][0] == "1"
    assert doc.header.rows == 3


def test_check_schema():
    doc = parse_doc(
        "#!excsv version=0.2\n"
        "#column name=id type=int required=1\n"
        "#column name=status type=string enum=ok|bad\n"
        "id,status\n1,ok\nx,nope\n"
    )
    issues = doc.check_schema()
    assert len(issues) >= 2
    joined = " | ".join(f"{iss.kind.value}:{iss.message}" for iss in issues)
    assert "not an int" in joined
    assert "enum" in joined


def test_check_schema_ok():
    doc = parse_doc(
        "#!excsv version=0.2\n#column name=id type=int\n#column name=when type=date\nid,when\n1,2026-01-02\n"
    )
    assert doc.check_schema() == []


def test_upsert_column():
    doc = parse_doc("#!excsv version=0.2\nid,amount\n1,10\n")
    doc.upsert_column("amount", {"type": "decimal", "unit": "USD"})
    text = doc.serialize_canonical().decode()
    assert "#column name=amount type=decimal unit=USD" in text


def test_numeric_agg_skips_id_role():
    doc = parse_doc(
        "#!excsv version=0.2\n#column name=id type=int role=id\n#column name=amount type=decimal\n"
        "id,amount\n1,10.00\n2,20.00\n"
    )
    got = excsv._agg.compute_aggregation_values(doc, "sum")
    have = excsv.join_csv_fields(got, doc.header.dialect())
    assert have == ",30.00"

    doc = parse_doc(
        "#!excsv version=0.2\n#column name=id type=int role=id\n#column name=qty type=int role=measure\n"
        "id,qty\n1,2\n2,3\n"
    )
    got = excsv._agg.compute_aggregation_values(doc, "sum")
    have = excsv.join_csv_fields(got, doc.header.dialect())
    assert have == ",5.00"
