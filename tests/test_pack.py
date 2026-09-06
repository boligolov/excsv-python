"""Port of pkg/excsv/pack_test.go."""

import io
import zipfile

import pytest

import excsv


def pack_zip(entries: list[tuple[str, str]]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, body in entries:
            zf.writestr(name, body)
    return buf.getvalue()


def test_pack_section_partition_error():
    data = pack_zip([
        ("_manifest.excsv",
         "#!excsv version=0.3 layout=pack table-count=1 original-size=1\n"
         "#table name=items dir=items/ columns=2 original-size=1\n"),
        ("items/_header.excsv",
         "#!excsv version=0.3 layout=columnar rows=5 section-size=2\n"
         "#column name=id type=int\n#column name=name type=string\n"),
        ("items/00-id/0.col", "1\n2\n"),
        ("items/00-id/2.col", "3\n4\n"),
        ("items/00-id/4.col", "5\n6\n"),
        ("items/01-name/0.col", "a\nb\n"),
        ("items/01-name/2.col", "c\nd\n"),
        ("items/01-name/4.col", "e\n"),
    ])
    with pytest.raises(excsv.ParseError) as exc_info:
        excsv.parse_path("x.excsv.pack.zip", data, excsv.strict_options())
    assert exc_info.value.issue.kind == excsv.ErrorKind.PACK_SECTION_PARTITION_ERROR


def test_pack_section_boundary_mismatch():
    data = pack_zip([
        ("_manifest.excsv",
         "#!excsv version=0.3 layout=pack table-count=1 original-size=1\n"
         "#table name=items dir=items/ columns=2 original-size=1\n"),
        ("items/_header.excsv",
         "#!excsv version=0.3 layout=columnar rows=5 section-size=2\n"
         "#column name=id type=int\n#column name=name type=string\n"),
        ("items/00-id/0.col", "1\n2\n"),
        ("items/00-id/2.col", "3\n4\n"),
        ("items/00-id/4.col", "5\n"),
        ("items/01-name/0.col", "a\nb\n"),
        ("items/01-name/2.col", "c\n"),
        ("items/01-name/4.col", "e\n"),
    ])
    with pytest.raises(excsv.ParseError) as exc_info:
        excsv.parse_path("x.excsv.pack.zip", data, excsv.strict_options())
    assert exc_info.value.issue.kind == excsv.ErrorKind.PACK_SECTION_BOUNDARY_MISMATCH


def test_pack_create_round_trip():
    src = b"#!excsv version=0.3\n#column name=id type=int\n#column name=n type=string\nid,n\n1,a\n2,b\n"
    res = excsv.parse_bytes(src, excsv.strict_options())
    pack = excsv.pack_from_document(res.doc, "contacts")
    zipped = pack.serialize()
    got = excsv.parse_path("out.excsv.pack.zip", zipped, excsv.strict_options())
    assert got.pack is not None and len(got.pack.tables) == 1
    assert got.pack.tables[0].decl.name == "contacts"
    assert got.pack.tables[0].header.row_count() == 2


def test_pack_encrypted_requires_password():
    pyzipper = pytest.importorskip("pyzipper")
    buf = io.BytesIO()
    with pyzipper.AESZipFile(buf, "w", encryption=pyzipper.WZ_AES) as zf:
        zf.setpassword(b"secret")
        zf.setencryption(pyzipper.WZ_AES, nbits=256)
        zf.writestr("_manifest.excsv", "#!excsv version=0.3 layout=pack table-count=0 original-size=0\n")
    enc = buf.getvalue()

    with pytest.raises(excsv.ParseError) as exc_info:
        excsv.parse_path("x.excsv.pack.zip", enc, excsv.strict_options())
    assert exc_info.value.issue.kind == excsv.ErrorKind.ZIP_ENCRYPTED

    opts = excsv.strict_options()
    opts.zip_password = "secret"
    got = excsv.parse_path("x.excsv.pack.zip", enc, opts)
    assert got.pack is not None
