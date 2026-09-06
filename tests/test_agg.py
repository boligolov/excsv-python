"""Port of pkg/excsv/agg_test.go."""

from pathlib import Path

import pytest

import excsv
from excsv._agg import compute_aggregation_values

FIXTURE_PATH = (
    Path(__file__).parent.parent / "test" / "fixtures" / "plain" / "valid" / "011_aggregations_standard.excsv"
)


@pytest.mark.skipif(not FIXTURE_PATH.exists(), reason="fixtures not synced")
def test_compute_aggregation_values():
    data = FIXTURE_PATH.read_bytes()
    res = excsv.parse_bytes(data, excsv.strict_options())
    doc = res.doc

    cases = {
        "count_nonnull": "3,3,3",
        "count_null": "0,0,0",
        "count_distinct": "3,3,3",
        "sum": ",,60.00",
        "avg": ",,20.00",
        "min": ",,10.00",
        "max": ",,30.00",
        "len_min": ",3,",
        "len_max": ",5,",
    }
    for name, want in cases.items():
        got = compute_aggregation_values(doc, name)
        have = excsv.join_csv_fields(got, doc.header.dialect())
        assert have == want, f"{name}: got {have!r} want {want!r}"


def test_add_aggregation_no_op_when_exists():
    doc = excsv.Document(
        header=excsv.Header(header_row=True, fields={"version": "0.2"}),
        meta=excsv.MetaBlock(aggregations=[excsv.Aggregation(name="sum", values=["", "", "1"])]),
        data=excsv.DataSection(has_header_row=True, header_row=["a"], rows=[["1"]]),
    )
    added = doc.add_aggregation("sum")
    assert not added
    assert doc.meta.aggregations[0].values[2] == "1"
