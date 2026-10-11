"""A calculator that evaluates ONLY arithmetic. It never calls eval(): the expression is parsed to a
syntax tree and every node type is checked against a short allow-list."""
from __future__ import annotations

import ast
import operator

MAX_LENGTH, MAX_DEPTH, MAX_EXPONENT, MAX_BITS = 200, 30, 100, 4096

_BINARY = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
           ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_FUNCTIONS = {"round": round, "abs": abs, "min": min, "max": max}
_ALLOWED = "Only numbers, + - * / // % **, brackets and the functions round, abs, min, max are allowed."


def evaluate(expression: str) -> int | float:
    if len(expression) > MAX_LENGTH:
        raise ValueError(f"Expression is too long ({MAX_LENGTH} characters at most).")
    try:
        tree = ast.parse(expression.strip(), mode="eval")
    except SyntaxError as exc:
        raise ValueError("That is not a valid arithmetic expression.") from exc
    return _eval(tree.body, 0)


def _eval(node: ast.AST, depth: int):
    if depth > MAX_DEPTH:
        raise ValueError("Expression is nested too deeply.")
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
        return _UNARY[type(node.op)](_eval(node.operand, depth + 1))
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
        left, right = _eval(node.left, depth + 1), _eval(node.right, depth + 1)
        if isinstance(node.op, ast.Pow) and abs(right) > MAX_EXPONENT:
            raise ValueError(f"Exponent is too large ({MAX_EXPONENT} at most).")
        try:
            out = _BINARY[type(node.op)](left, right)
        except ZeroDivisionError as exc:
            raise ValueError("Division by zero.") from exc
        except OverflowError as exc:
            raise ValueError("The number is too large.") from exc
        if isinstance(out, complex):
            raise ValueError("The result is not a real number.")
        if isinstance(out, int) and out.bit_length() > MAX_BITS:
            raise ValueError("The number is too large.")
        return out
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCTIONS \
            and not node.keywords and 1 <= len(node.args) <= 10:
        try:
            return _FUNCTIONS[node.func.id](*[_eval(a, depth + 1) for a in node.args])
        except TypeError as exc:
            raise ValueError("Those arguments do not work with that function.") from exc
    raise ValueError(_ALLOWED)
