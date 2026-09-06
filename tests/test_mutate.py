"""Port of pkg/excsv/mutate_test.go."""

import excsv


def test_set_file_meta():
    res = excsv.parse_bytes(b"#!excsv version=0.2\n#@author: old\nid\n1\n", excsv.strict_options())
    res.doc.set_file_meta("author", "new@example.com")
    assert res.doc.meta_map()["author"] == "new@example.com"
    out = res.doc.serialize_canonical().decode()
    assert "#@author: new@example.com" in out


def test_human_comments():
    res = excsv.parse_bytes(b"#!excsv version=0.2\nid\n1\n", excsv.strict_options())
    res.doc.add_human_comment("note one")
    res.doc.add_human_comment("## already prefixed")
    text = res.doc.serialize_canonical().decode()
    assert "## note one" in text
    assert "## already prefixed" in text
    assert res.doc.remove_human_comment(0)
    text = res.doc.serialize_canonical().decode()
    assert "## note one" not in text


def test_set_sql():
    src = b"#!excsv version=0.2\n#$ddl: OLD\nid,amount\n1,10\n"
    res = excsv.parse_bytes(src, excsv.strict_options())
    res.doc.set_sql("ddl", "CREATE TABLE t (id INT)")
    assert len(res.doc.meta.sql) == 1
    assert res.doc.meta.sql[0].payload == "CREATE TABLE t (id INT)"
    res.doc.set_sql("dql", "SELECT 1")
    assert len(res.doc.meta.sql) == 2
