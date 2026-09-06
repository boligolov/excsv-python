"""Port of pkg/excsv/bom_test.go."""

from excsv._bom import strip_utf8_bom


def test_strip_utf8_bom():
    bom = b"\xef\xbb\xbf"
    body = b"#!excsv version=0.2\n"
    assert strip_utf8_bom(bom + body) == body
    assert strip_utf8_bom(body) == body
