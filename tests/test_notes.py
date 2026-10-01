"""#note / link= / #link (v0.6): round trip, anchors, JSON mirror, editing."""

import json

import pytest

import excsv
from excsv import zip as excsvzip

NOTES_DOC = (
    "#!excsv version=0.6 header=1 rows=3\n"
    '#column name=id type=int role=id link="https://crm.example.com/o/{$}"\n'
    "#column name=amount type=decimal\n"
    '#note row=2 col=amount author=alex@example.com text="Refund ""pending""" x-color=red\n'
    '#note key=1 text="First order"\n'
    '#note col=amount text="Includes VAT"\n'
    '#link row=0 col=amount href="https://billing.example.com/inv/1"\n'
    "id,amount\n"
    "3,30.00\n"
    "1,10.00\n"
    "2,20.00\n"
)


def parse_doc(src: str) -> excsv.Document:
    res = excsv.parse_bytes(src.encode(), excsv.strict_options())
    assert not res.warnings, res.warnings
    return res.doc


def test_notes_round_trip():
    doc = parse_doc(NOTES_DOC)
    assert len(doc.meta.notes) == 3 and len(doc.meta.links) == 1
    assert doc.meta.notes[0].text == 'Refund "pending"'

    out = doc.serialize_canonical().decode()
    assert '#note row=2 col=amount author=alex@example.com text="Refund ""pending""" x-color=red\n' in out
    assert '#link row=0 col=amount href="https://billing.example.com/inv/1"\n' in out
    assert "link=https://crm.example.com/o/{$}" in out
    # Recommended order: #column, #chart, then #note/#link, then the data.
    assert out.index("#note") > out.rindex("#column")
    assert out.index("#link") < out.index("id,amount")

    again = parse_doc(out)
    assert len(again.meta.notes) == 3 and len(again.meta.links) == 1


def test_resolve_notes_targets_and_rows():
    notes = parse_doc(NOTES_DOC).resolve_notes()
    assert [n.target for n in notes] == [excsv.NoteTarget.CELL, excsv.NoteTarget.ROW, excsv.NoteTarget.COLUMN]
    assert [n.row for n in notes] == [2, 1, None]
    assert [n.col for n in notes] == [1, None, 1]
    assert all(n.resolved for n in notes)


def test_resolve_links_template_and_override():
    links = parse_doc(NOTES_DOC).resolve_links()
    assert links.cell_link(0, "id") == "https://crm.example.com/o/3"
    assert links.cell_link(0, "amount") == "https://billing.example.com/inv/1"
    assert links.cell_link(1, "amount") is None
    assert links.cell_link(0, "missing") is None
    assert len(links) == 4


def test_sort_keeps_row_anchors():
    doc = parse_doc(NOTES_DOC)
    doc.sort_rows([excsv.SortKey(index=doc.column_index("id"))])
    # Before: rows 3,1,2. After: 1,2,3. The row=2 note was on id=2 (now row 1);
    # the row=0 link was on id=3 (now row 2); key=1 needs no change.
    assert doc.meta.notes[0].attrs["row"] == "1"
    assert doc.meta.links[0].attrs["row"] == "2"
    assert doc.meta.notes[1].attrs == {"key": "1", "text": "First order"}
    notes = doc.resolve_notes()
    assert notes[0].row == 1 and notes[1].row == 0
    assert doc.resolve_links().cell_link(2, "amount") == "https://billing.example.com/inv/1"


def test_notes_json_mirror():
    got = json.loads(parse_doc(NOTES_DOC).export_json().data)
    assert got["excsv"] == "0.6"
    assert got["columns"][0]["link"] == "https://crm.example.com/o/{$}"
    assert len(got["notes"]) == 3 and len(got["links"]) == 1
    first = got["notes"][0]
    assert first["row"] == 2 and first["col"] == "amount" and first["x-color"] == "red"
    assert got["notes"][1]["key"] == "1", "key must stay a string"
    assert got["links"][0]["href"] == "https://billing.example.com/inv/1"


def test_notes_header0_json_index():
    doc = parse_doc("#!excsv version=0.6 header=0 rows=1\n#column index=0 type=int\n#note row=0 col=0 text=x\n7\n")
    assert '"col":0' in doc.export_json().data.decode()


def test_add_note_raises_version():
    doc = parse_doc("#!excsv version=0.5 header=1 rows=1\n#column name=id type=int role=id\nid\n42\n")
    key, value = doc.row_anchor(0)
    assert (key, value) == ("key", "42"), "the table has an id column"
    doc.add_note({key: value, "text": "hi"})
    assert doc.header.version == "0.6" and doc.header.fields["version"] == "0.6"
    with pytest.raises(excsv.ParseError) as exc_info:
        doc.add_note({"col": "id"})
    assert exc_info.value.kind == excsv.ErrorKind.NOTE_MISSING_TEXT


def test_row_anchor_without_id_column_is_positional():
    doc = parse_doc("#!excsv version=0.6 header=1 rows=1\n#column name=id type=int\nid\n42\n")
    assert doc.row_anchor(0) == ("row", "0")
    with pytest.raises(IndexError):
        doc.row_anchor(1)


def test_set_link_replaces_same_cell():
    doc = parse_doc(NOTES_DOC)
    doc.set_link({"row": "0", "col": "amount", "href": "https://new.example.com"})
    assert len(doc.meta.links) == 1 and doc.meta.links[0].href == "https://new.example.com"
    with pytest.raises(excsv.ParseError) as exc_info:
        doc.set_link({"col": "amount", "href": "x"})
    assert exc_info.value.kind == excsv.ErrorKind.LINK_MISSING_ADDRESS


def test_remove_note_and_link():
    doc = parse_doc(NOTES_DOC)
    assert doc.remove_note(1) and [n.text for n in doc.meta.notes] == ['Refund "pending"', "Includes VAT"]
    assert not doc.remove_note(5)
    assert doc.remove_link(0) and not doc.meta.links


@pytest.mark.parametrize("version", ["0.1", "0.2", "0.5", "0.6"])
def test_earlier_versions_read_without_warning(version):
    res = excsv.parse_bytes(f"#!excsv version={version} header=1 rows=1\nid\n1\n".encode(), excsv.strict_options())
    assert not any(w.kind == excsv.ErrorKind.UNKNOWN_VERSION for w in res.warnings)


def test_newer_version_warns():
    res = excsv.parse_bytes(b"#!excsv version=0.7 header=1 rows=1\nid\n1\n", excsv.strict_options())
    assert [w.kind for w in res.warnings] == [excsv.ErrorKind.UNKNOWN_VERSION]


@pytest.mark.parametrize("url,want", [
    ("https://x", True), ("HTTP://x", True), ("mailto:a@b", True),
    ("javascript:alert(1)", False), ("file:///etc", False), ("/relative", False), ("", False),
    ("1http://x", False),
])
def test_is_safe_link_scheme(url, want):
    assert excsv.is_safe_link_scheme(url) is want


def test_percent_encode_link_value():
    assert excsv.percent_encode_link_value("a b/é~") == "a%20b%2F%C3%A9~"


@pytest.mark.parametrize("line,want", [
    ("#note col=amount", excsv.ErrorKind.NOTE_MISSING_TEXT),
    ("#note row=0 key=1 text=x", excsv.ErrorKind.NOTE_ROW_AND_KEY),
    ('#note text="unterminated', excsv.ErrorKind.NOTE_MALFORMED),
    ("#note text=x stray", excsv.ErrorKind.NOTE_MALFORMED),
    ("#link row=0 col=amount", excsv.ErrorKind.LINK_MISSING_HREF),
    ("#link row=0 href=x", excsv.ErrorKind.LINK_MISSING_ADDRESS),
    ("#link row=0 key=1 col=amount href=x", excsv.ErrorKind.LINK_ROW_AND_KEY),
    ("#link =x", excsv.ErrorKind.LINK_MALFORMED),
])
def test_note_link_parse_failures(line, want):
    src = "#!excsv version=0.6 header=1 rows=1\n#column name=amount type=decimal\n" + line + "\namount\n1\n"
    with pytest.raises(excsv.ParseError) as exc_info:
        excsv.parse_bytes(src.encode(), excsv.strict_options())
    assert exc_info.value.kind == want


def test_similar_keywords_are_unknown_meta_lines():
    doc = parse_doc("#!excsv version=0.6 header=1 rows=1\n#notes free text\n#linkage x\nid\n1\n")
    assert not doc.meta.notes and not doc.meta.links
    assert [u.text for u in doc.meta.unknown] == ["#notes free text", "#linkage x"]


def test_unresolved_lines_are_kept_on_round_trip():
    src = ("#!excsv version=0.6 header=1 rows=1\n#column name=id type=int\n"
           "#note row=9 text=gone\n#link key=1 col=id href=https://x.example.com\nid\n1\n")
    res = excsv.parse_bytes(src.encode(), excsv.strict_options())
    assert {w.kind for w in res.warnings} == {excsv.ErrorKind.NOTE_UNRESOLVED, excsv.ErrorKind.LINK_UNRESOLVED}
    out = res.doc.serialize_canonical().decode()
    assert '#note row=9 text="gone"' in out and "#link key=1 col=id" in out


def test_validate_reports_note_link_warnings():
    doc = excsv.parse_bytes(
        b"#!excsv version=0.6 header=1 rows=1\n#column name=id type=int\n#note row=9 text=x\nid\n1\n",
        excsv.lenient_options(),
    ).doc
    kinds = [f.issue.kind for f in doc.validate().findings]
    assert excsv.ErrorKind.NOTE_UNRESOLVED in kinds


def test_zip_comment_priority():
    inner = (
        "#!excsv version=0.6 header=1 rows=1\n"
        "#@note-to-self: misc\n"
        "#column name=id type=int\n"
        "#chart type=bar name=c x=id y=id\n"
        '#note text="table note"\n'
        "#$dql: SELECT 1\n"
        "#@source: unit-test\n"
        "#%sum: 1\n"
        "id\n1\n"
    )
    z = excsvzip.wrap(inner.encode(), "t.excsv", "")
    comment = excsvzip.inspect("t.excsv.zip", z).comment
    prefixes = [line.split(" ", 1)[0] for line in comment.split("\n")[1:]]
    assert prefixes == ["#@source:", "#column", "#%sum:", "#@note-to-self:", "#$dql:", "#chart", "#note"]
