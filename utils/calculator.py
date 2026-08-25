"""Safe deterministic arithmetic for explicit conversational calculations."""

from __future__ import annotations

import ast
from decimal import Decimal, InvalidOperation
import operator
import re

_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}
_REQUEST = re.compile(
    r"^(?:what(?:'s| is)|calculate|compute|work out)\s+([\d\s().+*/%^-]+)\??$", re.I
)


def _evaluate(node, depth: int = 0):
    if depth > 12:
        raise ValueError("Expression is too complex")
    if isinstance(node, ast.Expression):
        return _evaluate(node.body, depth + 1)
    if isinstance(node, ast.Constant) and type(node.value) in {int, float}:
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        left, right = _evaluate(node.left, depth + 1), _evaluate(node.right, depth + 1)
        if isinstance(node.op, ast.Pow) and (abs(right) > 12 or abs(left) > 10**9):
            raise ValueError("Exponent is too large")
        value = _OPS[type(node.op)](left, right)
        if abs(value) > 10**18:
            raise ValueError("Result is too large")
        return value
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_evaluate(node.operand, depth + 1))
    raise ValueError("Only arithmetic expressions are supported")


def calculate_request(text: str) -> str | None:
    match = _REQUEST.fullmatch(text.strip())
    if not match:
        return None
    expression = match.group(1).replace("^", "**")
    try:
        value = _evaluate(ast.parse(expression, mode="eval"))
    except (SyntaxError, ValueError, ZeroDivisionError, OverflowError):
        return None
    if isinstance(value, float):
        try:
            rendered = format(Decimal(str(value)).normalize(), "f")
        except InvalidOperation:
            rendered = str(value)
    else:
        rendered = str(value)
    return f"{match.group(1).strip()} = {rendered}"
