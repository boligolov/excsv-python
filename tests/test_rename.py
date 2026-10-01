"""Document.rename_column: every by-name reference follows the rename."""

import pytest

import excsv

RENAME_DOC = (
    "#!excsv version=0.6 header=1 rows=2\n"
    '#column name=id type=int role=id link="https://x.example.com/{$}?c={qty}&lit={{qty}"\n'
    "#column name=qty type=int\n"
    "#column name=price type=decimal\n"
    '#column name=total type=decimal formula="qty * price + coalesce(qty, 0)"\n'
    "#column name=label type=string formula=\"concat('qty', ' ', label_src)\"\n"
    "#column name=label_src type=string\n"
    "#chart type=bar name=c x=id y=qty tooltip=qty,price\n"
    '#chart-vega: {"encoding":{"x":{"field":"qty"}}}\n'
    '#note row=0 col=qty text="check"\n'
    '#link key=1 col=qty href="https://y.example.com"\n'
    "#$ddl: CREATE TABLE t (id INT, qty INT)\n"
    "id,qty,price,label_src\n"
    "1,2,3.00,a\n"
    "2,5,1.50,b\n"
)


def parse_doc(src: str) -> excsv.Document:
    return excsv.parse_bytes(src.encode(), excsv.strict_options()).doc


def test_rename_column_rewrites_references():
    doc = parse_doc(RENAME_DOC)
    notices = doc.rename_column("qty", "quantity")
    out = doc.serialize_canonical().decode()
    for want in (
        "#column name=quantity type=int\n",
        'formula="quantity * price + coalesce(quantity, 0)"',
        "formula=\"concat('qty', ' ', label_src)\"",  # string literal untouched
        "x=id y=quantity tooltip=quantity,price",
        '#note row=0 col=quantity text="check"',
        "#link key=1 col=quantity href=",
        "link=https://x.example.com/{$}?c={quantity}&lit={{qty}",  # {{ is a literal, not a placeholder
        "\nid,quantity,price,label_src\n",
    ):
        assert want in out, f"output lacks {want!r}:\n{out}"
    joined = "\n".join(notices)
    assert "#chart-vega" in joined and "#$ddl" in joined

    # The result is still a valid document with every reference resolving.
    res = excsv.parse_bytes(out.encode(), excsv.strict_options())
    assert not res.warnings, res.warnings


@pytest.mark.parametrize("old,new", [
    ("missing", "x"),
    ("qty", "price"),  # already exists
    ("qty", "two words"),  # spaces
    ("qty", "1qty"),  # used in a formula, not a formula identifier
    ("qty", "case"),  # reserved formula keyword
    ("qty", "qty"),
])
def test_rename_column_rejects(old, new):
    doc = parse_doc(RENAME_DOC)
    before = doc.serialize_canonical()
    with pytest.raises((ValueError, excsv.ParseError)):
        doc.rename_column(old, new)
    assert doc.serialize_canonical() == before, "a rejected rename must leave the document unchanged"


def test_rename_column_updates_checksum():
    doc = parse_doc("#!excsv version=0.6 header=1 rows=1\n#column name=a type=int\na\n1\n")
    doc.set_data_checksum("sha256")
    doc.rename_column("a", "b")
    res = excsv.parse_bytes(doc.serialize_canonical(), excsv.strict_options())
    assert not any(w.kind == excsv.ErrorKind.CHECKSUM_MISMATCH for w in res.warnings)


def test_rename_column_fks():
    pack = excsv.Pack(fks=[excsv.ForeignKey(from_="orders.cust", to="customers.id")])
    pack.rename_column_fks("orders", "cust", "customer_id")
    assert (pack.fks[0].from_, pack.fks[0].to) == ("orders.customer_id", "customers.id")
