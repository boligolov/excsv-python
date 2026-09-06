"""Port of pkg/excsv/computed_columns_test.go."""

import pytest

import excsv

COMPUTED_SRC = (
    "#!excsv version=0.5 header=1 rows=2\n"
    '#column name=price type=decimal\n'
    '#column name=quantity type=int\n'
    '#column name=total type=decimal formula="price * quantity"\n'
    "price,quantity\n"
    "10.00,3\n"
    "2.50,4\n"
)


def parse_computed_doc(src: str) -> excsv.Document:
    res = excsv.parse_bytes(src.encode(), excsv.strict_options())
    return res.doc


def test_materialize_column():
    doc = parse_computed_doc(COMPUTED_SRC)
    doc.materialize_column("total")
    assert doc.data.header_row == ["price", "quantity", "total"]
    assert doc.data.rows[0][2] == "30.00"
    assert doc.data.rows[1][2] == "10.00"
    idx = doc.column_index("total")
    assert idx == 2
    report = doc.validate(excsv.ValidateOptions(with_data=True))
    assert report.ok(), report.findings

    doc.dematerialize_column("total")
    assert len(doc.data.header_row) == 2
    out = doc.serialize_canonical().decode()
    assert "materialized" not in out


def test_materialize_column_rejects_non_formula():
    doc = parse_computed_doc(COMPUTED_SRC)
    with pytest.raises(Exception):
        doc.materialize_column("price")


def test_materialize_column_rejects_already_materialized():
    doc = parse_computed_doc(COMPUTED_SRC)
    doc.materialize_column("total")
    with pytest.raises(Exception):
        doc.materialize_column("total")


def test_dematerialize_column_rejects_virtual():
    doc = parse_computed_doc(COMPUTED_SRC)
    with pytest.raises(Exception):
        doc.dematerialize_column("total")


def test_virtual_column_declared_between_stored_columns():
    """Regression test for the declaration-order-vs-physical-position bug: a
    virtual computed column declared between two stored #column lines must
    not shift the physical index of the stored column that follows it."""
    src = (
        "#!excsv version=0.5 header=1 rows=1\n"
        "#column name=price type=decimal\n"
        '#column name=total type=decimal formula="price * qty"\n'
        "#column name=qty type=int\n"
        "price,qty\n"
        "10.00,3\n"
    )
    doc = parse_computed_doc(src)
    idx = doc.column_index("qty")
    assert idx == 1  # price=0, qty=1, total is virtual
    report = doc.validate(excsv.ValidateOptions(with_data=True))
    assert report.ok(), report.findings
    doc.materialize_column("total")
    assert doc.data.rows[0][2] == "30.00"


@pytest.mark.parametrize(
    "src,want",
    [
        (
            "#!excsv version=0.5 header=1 rows=1\n#column name=price type=decimal\n"
            '#column name=total type=decimal formula="price * missing"\nprice\n1\n',
            excsv.ErrorKind.FORMULA_UNKNOWN_REFERENCE,
        ),
        (
            "#!excsv version=0.5 header=1 rows=1\n#column name=price type=decimal\n"
            '#column name=a type=decimal formula="price * 2"\n'
            '#column name=b type=decimal formula="a * 2"\nprice\n1\n',
            excsv.ErrorKind.FORMULA_REFERENCES_COMPUTED,
        ),
        (
            "#!excsv version=0.5 header=1 rows=1\n#column name=price type=decimal\n"
            '#column name=total type=decimal formula="price *"\nprice\n1\n',
            excsv.ErrorKind.FORMULA_PARSE_ERROR,
        ),
    ],
)
def test_formula_validation_error_codes(src, want):
    doc = parse_computed_doc(src)
    report = doc.validate(excsv.ValidateOptions())
    assert any(f.issue.kind == want for f in report.findings), report.findings


def test_formula_index_forbidden_fails_at_parse():
    src = (
        "#!excsv version=0.5 header=1 rows=1\n#column name=price type=decimal\n"
        '#column name=total index=2 type=decimal formula="price * 2"\nprice\n1\n'
    )
    with pytest.raises(excsv.ParseError) as exc_info:
        excsv.parse_bytes(src.encode(), excsv.strict_options())
    assert exc_info.value.issue.kind == excsv.ErrorKind.FORMULA_INDEX_FORBIDDEN


def test_formula_requires_header_fails_at_parse():
    src = (
        "#!excsv version=0.5 header=0 rows=1\n#column index=0 name=price type=decimal\n"
        '#column index=1 name=total type=decimal formula="price * 2"\n1.00\n'
    )
    with pytest.raises(excsv.ParseError) as exc_info:
        excsv.parse_bytes(src.encode(), excsv.strict_options())
    assert exc_info.value.issue.kind == excsv.ErrorKind.FORMULA_REQUIRES_HEADER
