"""Port of pkg/excsv/formula_test.go."""

from fractions import Fraction

import pytest

from excsv._formula import (
    FVKind,
    format_formula_number,
    formula_referenced_names,
    formula_value_from_cell,
    fv_null,
    fv_string,
    parse_formula,
)


def num_env(vals: dict[str, str]) -> dict:
    return {k: formula_value_from_cell(v, "decimal") for k, v in vals.items()}


def eval_formula_str(expr: str, env: dict):
    node = parse_formula(expr)
    return node.eval(env)


def test_formula_arithmetic():
    env = num_env({"price": "10.00", "quantity": "3"})
    v = eval_formula_str("price * quantity", env)
    assert format_formula_number(v.n, "") == "30.00"

    env2 = num_env({"price": "10.00", "cost": "6.00"})
    v2 = eval_formula_str("(price - cost) / price", env2)
    assert format_formula_number(v2.n, "") == "0.40"


def test_formula_concat():
    env = {"first_name": fv_string("Ada"), "last_name": fv_string("Lovelace")}
    v = eval_formula_str("concat(first_name, ' ', last_name)", env)
    assert v.kind == FVKind.STRING and v.s == "Ada Lovelace"


def test_formula_case_when():
    env = num_env({"amount": "150"})
    v = eval_formula_str("case when amount > 100 then 'high' else 'low' end", env)
    assert v.kind == FVKind.STRING and v.s == "high"


def test_formula_coalesce_round_floor_ceil():
    env = {"a": fv_null()}
    v = eval_formula_str("coalesce(a, 5)", env)
    assert v.kind == FVKind.NUMBER and v.n == Fraction(5)

    v2 = eval_formula_str("round(2.005, 2)", {})
    assert format_formula_number(v2.n, "") == "2.01"

    v3 = eval_formula_str("floor(2.9)", {})
    assert format_formula_number(v3.n, "") == "2.00"

    v4 = eval_formula_str("ceil(2.1)", {})
    assert format_formula_number(v4.n, "") == "3.00"


def test_formula_unknown_function_rejected():
    with pytest.raises(Exception):
        parse_formula("now()")


def test_formula_double_pipe_rejected():
    with pytest.raises(Exception):
        parse_formula("a || b")


def test_formula_referenced_names():
    node = parse_formula("(price - cost) / price + tax_rate")
    names = formula_referenced_names(node)
    assert names == ["cost", "price", "tax_rate"]
