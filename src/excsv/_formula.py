"""Formula language for #column formula= computed columns
(implementation/columns.md#formula-language). Deliberately small and
portable: no dialect selector, so any conforming tool can evaluate it.

Grammar (case-insensitive keywords)::

    orExpr    := andExpr ('or' andExpr)*
    andExpr   := notExpr ('and' notExpr)*
    notExpr   := 'not' notExpr | cmpExpr
    cmpExpr   := addExpr (('='|'<>'|'<'|'<='|'>'|'>=') addExpr)?
    addExpr   := mulExpr (('+'|'-') mulExpr)*
    mulExpr   := unary (('*'|'/'|'%') unary)*
    unary     := '-' unary | primary
    primary   := NUMBER | STRING | 'true' | 'false' | 'null'
               | 'case' ('when' orExpr 'then' orExpr)+ ['else' orExpr] 'end'
               | '(' orExpr ')' | IDENT ['(' (orExpr (',' orExpr)*)? ')']

Function whitelist: abs round floor ceil coalesce nullif least greatest
length lower upper trim substr concat.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum, auto
from fractions import Fraction
from typing import Optional

FORMULA_FUNC_WHITELIST = {
    "abs", "round", "floor", "ceil",
    "coalesce", "nullif", "least", "greatest",
    "length", "lower", "upper", "trim",
    "substr", "concat",
}


# ---- value model -----------------------------------------------------

class FVKind(Enum):
    NULL = auto()
    BOOL = auto()
    NUMBER = auto()
    STRING = auto()


@dataclass
class FormulaValue:
    kind: FVKind
    b: bool = False
    n: Optional[Fraction] = None
    s: str = ""


def fv_null() -> FormulaValue:
    return FormulaValue(FVKind.NULL)


def fv_bool(b: bool) -> FormulaValue:
    return FormulaValue(FVKind.BOOL, b=b)


def fv_number(n: Fraction) -> FormulaValue:
    return FormulaValue(FVKind.NUMBER, n=n)


def fv_string(s: str) -> FormulaValue:
    return FormulaValue(FVKind.STRING, s=s)


def formula_value_from_cell(v: str, col_type: str) -> FormulaValue:
    """Converts a raw cell (already known non-null) into a FormulaValue for
    evaluation, based on the stored column's declared type."""
    ct = (col_type or "").strip().lower()
    if ct in ("int", "long", "float", "double", "decimal"):
        n = _to_fraction(v.strip())
        return fv_number(n) if n is not None else fv_string(v)
    if ct == "boolean":
        low = v.strip().lower()
        if low in ("true", "1"):
            return fv_bool(True)
        if low in ("false", "0"):
            return fv_bool(False)
        return fv_string(v)
    return fv_string(v)


def _to_fraction(s: str) -> Optional[Fraction]:
    try:
        return Fraction(s)
    except (ValueError, ZeroDivisionError):
        return None


class FormulaError(Exception):
    pass


# ---- AST ---------------------------------------------------------------

class Node:
    def eval(self, env: dict) -> FormulaValue:
        raise NotImplementedError

    def collect_refs(self, out: set) -> None:
        pass


@dataclass
class LitNode(Node):
    val: FormulaValue

    def eval(self, env):
        return self.val


@dataclass
class ColRefNode(Node):
    name: str

    def eval(self, env):
        if self.name not in env:
            raise FormulaError(f"unknown column reference: {self.name}")
        return env[self.name]

    def collect_refs(self, out):
        out.add(self.name)


@dataclass
class UnaryNode(Node):
    op: str
    x: Node

    def collect_refs(self, out):
        self.x.collect_refs(out)

    def eval(self, env):
        v = self.x.eval(env)
        if self.op == "-":
            if v.kind == FVKind.NULL:
                return fv_null()
            if v.kind != FVKind.NUMBER:
                raise FormulaError("unary - requires a number")
            return fv_number(-v.n)
        if self.op == "not":
            if v.kind == FVKind.NULL:
                return fv_null()
            if v.kind != FVKind.BOOL:
                raise FormulaError("not requires a boolean")
            return fv_bool(not v.b)
        raise FormulaError(f"unknown unary operator {self.op}")


@dataclass
class BinNode(Node):
    op: str
    l: Node
    r: Node

    def collect_refs(self, out):
        self.l.collect_refs(out)
        self.r.collect_refs(out)

    def eval(self, env):
        if self.op == "and":
            return _eval_and(env, self.l, self.r)
        if self.op == "or":
            return _eval_or(env, self.l, self.r)
        l = self.l.eval(env)
        r = self.r.eval(env)
        if self.op in ("+", "-", "*", "/", "%"):
            return _eval_arith(self.op, l, r)
        if self.op in ("=", "<>", "<", "<=", ">", ">="):
            return _eval_compare(self.op, l, r)
        raise FormulaError(f"unknown binary operator {self.op}")


def _eval_and(env, ln: Node, rn: Node) -> FormulaValue:
    l = ln.eval(env)
    if l.kind == FVKind.BOOL and not l.b:
        return fv_bool(False)
    r = rn.eval(env)
    if r.kind == FVKind.BOOL and not r.b:
        return fv_bool(False)
    if l.kind == FVKind.NULL or r.kind == FVKind.NULL:
        return fv_null()
    if l.kind != FVKind.BOOL or r.kind != FVKind.BOOL:
        raise FormulaError("and requires boolean operands")
    return fv_bool(l.b and r.b)


def _eval_or(env, ln: Node, rn: Node) -> FormulaValue:
    l = ln.eval(env)
    if l.kind == FVKind.BOOL and l.b:
        return fv_bool(True)
    r = rn.eval(env)
    if r.kind == FVKind.BOOL and r.b:
        return fv_bool(True)
    if l.kind == FVKind.NULL or r.kind == FVKind.NULL:
        return fv_null()
    if l.kind != FVKind.BOOL or r.kind != FVKind.BOOL:
        raise FormulaError("or requires boolean operands")
    return fv_bool(l.b or r.b)


def _eval_arith(op: str, l: FormulaValue, r: FormulaValue) -> FormulaValue:
    if l.kind == FVKind.NULL or r.kind == FVKind.NULL:
        return fv_null()
    if l.kind != FVKind.NUMBER or r.kind != FVKind.NUMBER:
        raise FormulaError(f"{op} requires numeric operands")
    if op == "+":
        return fv_number(l.n + r.n)
    if op == "-":
        return fv_number(l.n - r.n)
    if op == "*":
        return fv_number(l.n * r.n)
    if op == "/":
        if r.n == 0:
            raise FormulaError("division by zero")
        return fv_number(l.n / r.n)
    if op == "%":
        if r.n == 0:
            raise FormulaError("modulo by zero")
        if l.n.denominator != 1 or r.n.denominator != 1:
            raise FormulaError("% requires integer operands")
        return fv_number(Fraction(l.n.numerator % r.n.numerator))
    raise FormulaError(f"unknown arithmetic operator {op}")


def _eval_compare(op: str, l: FormulaValue, r: FormulaValue) -> FormulaValue:
    if l.kind == FVKind.NULL or r.kind == FVKind.NULL:
        return fv_null()
    cmp = _compare_values(l, r)
    if op == "=":
        return fv_bool(cmp == 0)
    if op == "<>":
        return fv_bool(cmp != 0)
    if op == "<":
        return fv_bool(cmp < 0)
    if op == "<=":
        return fv_bool(cmp <= 0)
    if op == ">":
        return fv_bool(cmp > 0)
    if op == ">=":
        return fv_bool(cmp >= 0)
    raise FormulaError(f"unknown comparison {op}")


def _compare_values(l: FormulaValue, r: FormulaValue) -> int:
    if l.kind == FVKind.NUMBER and r.kind == FVKind.NUMBER:
        return -1 if l.n < r.n else (1 if l.n > r.n else 0)
    if l.kind == FVKind.STRING and r.kind == FVKind.STRING:
        return -1 if l.s < r.s else (1 if l.s > r.s else 0)
    if l.kind == FVKind.BOOL and r.kind == FVKind.BOOL:
        if l.b == r.b:
            return 0
        return -1 if not l.b else 1
    raise FormulaError("cannot compare mismatched types")


@dataclass
class CaseWhenClause:
    when: Node
    then: Node


@dataclass
class CaseNode(Node):
    whens: list
    els: Optional[Node] = None

    def collect_refs(self, out):
        for w in self.whens:
            w.when.collect_refs(out)
            w.then.collect_refs(out)
        if self.els is not None:
            self.els.collect_refs(out)

    def eval(self, env):
        for w in self.whens:
            cond = w.when.eval(env)
            if cond.kind == FVKind.BOOL and cond.b:
                return w.then.eval(env)
        if self.els is not None:
            return self.els.eval(env)
        return fv_null()


@dataclass
class CallNode(Node):
    name: str
    args: list

    def collect_refs(self, out):
        for a in self.args:
            a.collect_refs(out)

    def eval(self, env):
        args = [a.eval(env) for a in self.args]
        return _call_formula_func(self.name, args)


def _arity(name: str, args: list, lo: int, hi: int) -> None:
    if len(args) < lo or (hi >= 0 and len(args) > hi):
        raise FormulaError(f"{name}() takes the wrong number of arguments")


def _call_formula_func(name: str, args: list[FormulaValue]) -> FormulaValue:
    if name == "abs":
        _arity(name, args, 1, 1)
        if args[0].kind == FVKind.NULL:
            return fv_null()
        if args[0].kind != FVKind.NUMBER:
            raise FormulaError("abs() requires a number")
        return fv_number(abs(args[0].n))

    if name == "round":
        _arity(name, args, 1, 2)
        return _round_func(args)

    if name in ("floor", "ceil"):
        _arity(name, args, 1, 1)
        if args[0].kind == FVKind.NULL:
            return fv_null()
        if args[0].kind != FVKind.NUMBER:
            raise FormulaError(f"{name}() requires a number")
        return fv_number(_rat_floor_ceil(args[0].n, name == "ceil"))

    if name == "coalesce":
        _arity(name, args, 1, -1)
        for a in args:
            if a.kind != FVKind.NULL:
                return a
        return fv_null()

    if name == "nullif":
        _arity(name, args, 2, 2)
        if args[0].kind == FVKind.NULL or args[1].kind == FVKind.NULL:
            return args[0]
        if _compare_values(args[0], args[1]) == 0:
            return fv_null()
        return args[0]

    if name in ("least", "greatest"):
        _arity(name, args, 1, -1)
        return _least_greatest(args, name == "greatest")

    if name == "length":
        _arity(name, args, 1, 1)
        if args[0].kind == FVKind.NULL:
            return fv_null()
        if args[0].kind != FVKind.STRING:
            raise FormulaError("length() requires a string")
        return fv_number(Fraction(len(args[0].s)))

    if name in ("lower", "upper", "trim"):
        _arity(name, args, 1, 1)
        if args[0].kind == FVKind.NULL:
            return fv_null()
        if args[0].kind != FVKind.STRING:
            raise FormulaError(f"{name}() requires a string")
        if name == "lower":
            return fv_string(args[0].s.lower())
        if name == "upper":
            return fv_string(args[0].s.upper())
        return fv_string(args[0].s.strip())

    if name == "substr":
        _arity(name, args, 2, 3)
        return _substr_func(args)

    if name == "concat":
        _arity(name, args, 1, -1)
        parts = []
        for a in args:
            if a.kind == FVKind.NULL:
                return fv_null()
            parts.append(_formula_value_to_display_string(a))
        return fv_string("".join(parts))

    raise FormulaError(f"unknown function {name}()")


def _round_func(args: list[FormulaValue]) -> FormulaValue:
    if args[0].kind == FVKind.NULL:
        return fv_null()
    if args[0].kind != FVKind.NUMBER:
        raise FormulaError("round() requires a number")
    prec = 0
    if len(args) == 2:
        if args[1].kind == FVKind.NULL:
            return fv_null()
        if args[1].kind != FVKind.NUMBER or args[1].n.denominator != 1:
            raise FormulaError("round() precision must be an integer")
        prec = int(args[1].n)
    return fv_number(_rat_round(args[0].n, prec))


def _rat_round(n: Fraction, prec: int) -> Fraction:
    """Half-away-from-zero rounding to `prec` decimal digits."""
    scale = Fraction(10) ** abs(prec)
    scaled = n * scale if prec >= 0 else n / scale
    half = Fraction(1, 2)
    scaled = scaled + half if scaled >= 0 else scaled - half
    q = int(scaled)  # truncates toward zero, like Go's big.Int.Quo
    rounded = Fraction(q)
    return rounded / scale if prec >= 0 else rounded * scale


def _rat_floor_ceil(n: Fraction, ceil: bool) -> Fraction:
    return Fraction(math.ceil(n) if ceil else math.floor(n))


def _least_greatest(args: list[FormulaValue], greatest: bool) -> FormulaValue:
    best: Optional[FormulaValue] = None
    for a in args:
        if a.kind == FVKind.NULL:
            return fv_null()
        if best is None:
            best = a
            continue
        cmp = _compare_values(a, best)
        if (greatest and cmp > 0) or (not greatest and cmp < 0):
            best = a
    return best


def _substr_func(args: list[FormulaValue]) -> FormulaValue:
    if args[0].kind == FVKind.NULL:
        return fv_null()
    if args[0].kind != FVKind.STRING:
        raise FormulaError("substr() requires a string")
    if args[1].kind != FVKind.NUMBER or args[1].n.denominator != 1:
        raise FormulaError("substr() start must be an integer")
    runes = args[0].s
    start = int(args[1].n)
    if start < 1:
        start = 1
    if start > len(runes) + 1:
        return fv_string("")
    end = len(runes) + 1
    if len(args) == 3:
        if args[2].kind == FVKind.NULL:
            return fv_null()
        if args[2].kind != FVKind.NUMBER or args[2].n.denominator != 1:
            raise FormulaError("substr() length must be an integer")
        length = int(args[2].n)
        if length < 0:
            length = 0
        end = start + length
        if end > len(runes) + 1:
            end = len(runes) + 1
    return fv_string(runes[start - 1:end - 1])


def _formula_value_to_display_string(v: FormulaValue) -> str:
    if v.kind == FVKind.STRING:
        return v.s
    if v.kind == FVKind.BOOL:
        return "true" if v.b else "false"
    if v.kind == FVKind.NUMBER:
        return format_formula_number(v.n, "")
    return ""


# ---- formatting for materialize ---------------------------------------

def format_formula_result(col_type: str, fmt: str, v: FormulaValue, null_marker: str) -> str:
    """Renders a computed value as the text cell for the data section."""
    if v.kind == FVKind.NULL:
        return null_marker
    ct = (col_type or "").strip().lower()
    if ct in ("int", "long"):
        if v.kind != FVKind.NUMBER or v.n.denominator != 1:
            raise FormulaError(f"formula result is not an integer for type={ct}")
        return str(v.n.numerator)
    if ct in ("float", "double", "decimal", ""):
        if v.kind != FVKind.NUMBER:
            return _formula_value_to_display_string(v)
        return format_formula_number(v.n, fmt)
    if ct == "boolean":
        if v.kind != FVKind.BOOL:
            raise FormulaError("formula result is not a boolean for type=boolean")
        return _formula_value_to_display_string(v)
    return _formula_value_to_display_string(v)


def format_formula_number(n: Fraction, fmt: str) -> str:
    """Renders a Fraction without float rounding: money-style (>=2 decimals,
    no trailing zeros beyond that) unless format= pins an exact pattern
    ("0.00")."""
    decimals = _decimals_from_format(fmt)
    if decimals is not None:
        return _fraction_to_fixed_string(n, decimals)
    s = _fraction_to_fixed_string(n, 10)
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    if "." not in s:
        return s + ".00"
    parts = s.split(".", 1)
    if len(parts[1]) == 1:
        return s + "0"
    return s


def _fraction_to_fixed_string(fr: Fraction, prec: int) -> str:
    rounded = _rat_round(fr, prec)
    scale = 10 ** prec
    scaled_int = rounded.numerator * scale // rounded.denominator
    sign = "-" if scaled_int < 0 else ""
    abs_val = abs(scaled_int)
    if prec == 0:
        return sign + str(abs_val)
    s = str(abs_val).rjust(prec + 1, "0")
    int_part, frac_part = s[:-prec], s[-prec:]
    return f"{sign}{int_part}.{frac_part}"


def _decimals_from_format(fmt: str) -> Optional[int]:
    fmt = (fmt or "").strip()
    if not fmt or "." not in fmt:
        return None
    _, frac = fmt.split(".", 1)
    if not all(ch in "0#" for ch in frac):
        return None
    return len(frac)


# ---- lexer --------------------------------------------------------------

class TokKind(Enum):
    EOF = auto()
    NUMBER = auto()
    STRING = auto()
    IDENT = auto()
    OP = auto()
    LPAREN = auto()
    RPAREN = auto()
    COMMA = auto()


@dataclass
class Token:
    kind: TokKind
    text: str


def _is_ident_start(c: str) -> bool:
    return c == "_" or c.isalpha() and c.isascii()


def _is_ident_part(c: str) -> bool:
    return _is_ident_start(c) or c.isdigit()


def lex_formula(s: str) -> list[Token]:
    out: list[Token] = []
    r = s
    i, n = 0, len(r)
    while i < n:
        c = r[i]
        if c in " \t\n\r":
            i += 1
        elif c == "(":
            out.append(Token(TokKind.LPAREN, "("))
            i += 1
        elif c == ")":
            out.append(Token(TokKind.RPAREN, ")"))
            i += 1
        elif c == ",":
            out.append(Token(TokKind.COMMA, ","))
            i += 1
        elif c == "'":
            j = i + 1
            buf = []
            closed = False
            while j < n:
                if r[j] == "'":
                    if j + 1 < n and r[j + 1] == "'":
                        buf.append("'")
                        j += 2
                        continue
                    closed = True
                    j += 1
                    break
                buf.append(r[j])
                j += 1
            if not closed:
                raise FormulaError("unterminated string literal")
            out.append(Token(TokKind.STRING, "".join(buf)))
            i = j
        elif c.isdigit():
            j = i
            while j < n and (r[j].isdigit() or r[j] == "."):
                j += 1
            out.append(Token(TokKind.NUMBER, r[i:j]))
            i = j
        elif _is_ident_start(c):
            j = i
            while j < n and _is_ident_part(r[j]):
                j += 1
            out.append(Token(TokKind.IDENT, r[i:j]))
            i = j
        elif c == "<":
            if i + 1 < n and r[i + 1] in (">", "="):
                out.append(Token(TokKind.OP, r[i:i + 2]))
                i += 2
            else:
                out.append(Token(TokKind.OP, "<"))
                i += 1
        elif c == ">":
            if i + 1 < n and r[i + 1] == "=":
                out.append(Token(TokKind.OP, ">="))
                i += 2
            else:
                out.append(Token(TokKind.OP, ">"))
                i += 1
        elif c in "=+-*/%":
            out.append(Token(TokKind.OP, c))
            i += 1
        elif c == "|":
            raise FormulaError("'||' is not supported; use concat(...)")
        else:
            raise FormulaError(f"unexpected character {c!r}")
    out.append(Token(TokKind.EOF, ""))
    return out


# ---- parser --------------------------------------------------------------

class _Parser:
    def __init__(self, toks: list[Token]):
        self.toks = toks
        self.pos = 0

    def cur(self) -> Token:
        return self.toks[self.pos]

    def advance(self) -> Token:
        t = self.toks[self.pos]
        if self.pos < len(self.toks) - 1:
            self.pos += 1
        return t

    def is_keyword(self, kw: str) -> bool:
        t = self.cur()
        return t.kind == TokKind.IDENT and t.text.lower() == kw

    def parse_or(self) -> Node:
        left = self.parse_and()
        while self.is_keyword("or"):
            self.advance()
            right = self.parse_and()
            left = BinNode("or", left, right)
        return left

    def parse_and(self) -> Node:
        left = self.parse_not()
        while self.is_keyword("and"):
            self.advance()
            right = self.parse_not()
            left = BinNode("and", left, right)
        return left

    def parse_not(self) -> Node:
        if self.is_keyword("not"):
            self.advance()
            return UnaryNode("not", self.parse_not())
        return self.parse_compare()

    def parse_compare(self) -> Node:
        left = self.parse_add()
        t = self.cur()
        if t.kind == TokKind.OP and t.text in ("=", "<>", "<", "<=", ">", ">="):
            self.advance()
            right = self.parse_add()
            return BinNode(t.text, left, right)
        return left

    def parse_add(self) -> Node:
        left = self.parse_mul()
        while True:
            t = self.cur()
            if t.kind == TokKind.OP and t.text in ("+", "-"):
                self.advance()
                right = self.parse_mul()
                left = BinNode(t.text, left, right)
                continue
            break
        return left

    def parse_mul(self) -> Node:
        left = self.parse_unary()
        while True:
            t = self.cur()
            if t.kind == TokKind.OP and t.text in ("*", "/", "%"):
                self.advance()
                right = self.parse_unary()
                left = BinNode(t.text, left, right)
                continue
            break
        return left

    def parse_unary(self) -> Node:
        t = self.cur()
        if t.kind == TokKind.OP and t.text == "-":
            self.advance()
            return UnaryNode("-", self.parse_unary())
        return self.parse_primary()

    def parse_primary(self) -> Node:
        t = self.cur()
        if t.kind == TokKind.NUMBER:
            self.advance()
            try:
                n = Fraction(t.text)
            except (ValueError, ZeroDivisionError):
                raise FormulaError(f"invalid number literal {t.text!r}")
            return LitNode(fv_number(n))
        if t.kind == TokKind.STRING:
            self.advance()
            return LitNode(fv_string(t.text))
        if t.kind == TokKind.LPAREN:
            self.advance()
            inner = self.parse_or()
            if self.cur().kind != TokKind.RPAREN:
                raise FormulaError("expected )")
            self.advance()
            return inner
        if t.kind == TokKind.IDENT:
            low = t.text.lower()
            if low == "true":
                self.advance()
                return LitNode(fv_bool(True))
            if low == "false":
                self.advance()
                return LitNode(fv_bool(False))
            if low == "null":
                self.advance()
                return LitNode(fv_null())
            if low == "case":
                return self.parse_case()
            self.advance()
            name = t.text
            if self.cur().kind == TokKind.LPAREN:
                self.advance()
                args = []
                if self.cur().kind != TokKind.RPAREN:
                    while True:
                        args.append(self.parse_or())
                        if self.cur().kind == TokKind.COMMA:
                            self.advance()
                            continue
                        break
                if self.cur().kind != TokKind.RPAREN:
                    raise FormulaError(f"expected ) after arguments to {name}(")
                self.advance()
                lname = name.lower()
                if lname not in FORMULA_FUNC_WHITELIST:
                    raise FormulaError(f"function {name}() is not in the formula whitelist")
                return CallNode(lname, args)
            return ColRefNode(name)
        raise FormulaError(f"unexpected token {t.text!r}")

    def parse_case(self) -> Node:
        self.advance()  # 'case'
        whens = []
        while self.is_keyword("when"):
            self.advance()
            cond = self.parse_or()
            if not self.is_keyword("then"):
                raise FormulaError("expected then")
            self.advance()
            then = self.parse_or()
            whens.append(CaseWhenClause(cond, then))
        if not whens:
            raise FormulaError("case requires at least one when clause")
        els = None
        if self.is_keyword("else"):
            self.advance()
            els = self.parse_or()
        if not self.is_keyword("end"):
            raise FormulaError("expected end")
        self.advance()
        return CaseNode(whens, els)


def parse_formula(expr: str) -> Node:
    try:
        toks = lex_formula(expr)
        p = _Parser(toks)
        node = p.parse_or()
        if p.cur().kind != TokKind.EOF:
            raise FormulaError(f"unexpected trailing input {p.cur().text!r}")
        return node
    except FormulaError as e:
        raise FormulaError(f"formula_parse_error: {e}") from e


def formula_referenced_names(n: Node) -> list[str]:
    out: set = set()
    n.collect_refs(out)
    return sorted(out)
