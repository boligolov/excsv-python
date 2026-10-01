"""Pack.add_table / Pack.drop_table round trip."""

import excsv


def parse_doc(src: str) -> excsv.Document:
    return excsv.parse_bytes(src.encode(), excsv.strict_options()).doc


def test_add_and_drop_table():
    doc1 = parse_doc("#!excsv version=0.5 rows=2\n#column name=id type=int\nid\n1\n2\n")
    doc2 = parse_doc("#!excsv version=0.5 rows=3\n#column name=name type=string\nname\na\nb\nc\n")

    pack = excsv.pack_from_document(doc1, "orders")
    assert pack.manifest.header.fields.get("single-table") == "orders"

    pack.add_table(doc2, "customers")
    assert "single-table" not in pack.manifest.header.fields
    assert len(pack.tables) == 2

    zipped = pack.serialize()
    got = excsv.parse_path("multi.excsv.pack.zip", zipped, excsv.strict_options())
    assert len(got.pack.tables) == 2
    names = {t.decl.name for t in got.pack.tables}
    assert names == {"orders", "customers"}
    customers = got.pack.table("customers")
    assert customers.header.row_count() == 3

    pack.drop_table("customers")
    assert len(pack.tables) == 1
    assert pack.manifest.header.fields.get("single-table") == "orders"
