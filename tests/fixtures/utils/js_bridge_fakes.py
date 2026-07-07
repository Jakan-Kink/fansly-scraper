"""Fakes for the JSPyBridge leaf boundary used by ``helpers/checkkey.py``.

``helpers/checkkey.py`` extracts the Fansly checkKey by running real
regex + AST reconciliation logic over a JavaScript string, delegating
only two operations to the ``javascript`` (JSPyBridge) package:

- ``eval_js(expr)`` — evaluate a JS expression and return its value.
- ``require("acorn")`` / ``require("acorn-walk")`` — parse JS into an
  AST and walk it.

These are the true *leaves* (the Node-subprocess boundary). Tests patch
them so the real Python-side extraction code paths — the regex matcher,
the expression slicer, and the regex-vs-AST reconciliation branches in
``extract_checkkey_from_js`` — all run for real over real JS input.

Two builders are provided:

- ``make_eval_js`` — maps normalized JS expressions to return values so a
  single ``helpers.checkkey.eval_js`` patch can serve both the regex path
  and the AST path with different (or matching) results.
- ``make_acorn_require`` — builds a fake ``require`` whose ``acorn`` /
  ``acorn-walk`` modules drive ``_extract_checkkey_ast_fallback``'s walk
  callback, simulating discovery of ``this.checkKey_`` assignment nodes.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock


def normalize_js_expr(expression: str) -> str:
    """Match the whitespace normalization production applies before eval_js.

    ``helpers/checkkey.py`` runs ``" ".join(expression.split())`` on the
    sliced expression before passing it to ``eval_js``. Test maps key on
    the normalized form so lookups line up with what production sends.
    """
    return " ".join(expression.split())


def make_eval_js(
    expr_to_value: dict[str, str | None],
    *,
    default: str | None = None,
    raises: Exception | None = None,
) -> Callable[[str], str | None]:
    """Build an ``eval_js`` leaf replacement mapping JS exprs to values.

    :param expr_to_value: normalized-expression → return value. Keys are
        normalized via :func:`normalize_js_expr`, so callers may pass raw
        (pre-normalized) expressions.
    :param default: value returned for an unmapped expression.
    :param raises: if set, every call raises this instead of returning —
        models ``eval_js`` blowing up on a malformed expression.
    """
    normalized = {normalize_js_expr(k): v for k, v in expr_to_value.items()}

    def fake_eval_js(expression: str) -> str | None:
        if raises is not None:
            raise raises
        return normalized.get(normalize_js_expr(expression), default)

    return fake_eval_js


def _make_checkkey_node(start: int, end: int) -> SimpleNamespace:
    """Build a fake acorn ``AssignmentExpression`` node for ``this.checkKey_``.

    Mirrors the attribute structure ``_extract_checkkey_ast_fallback``'s
    ``check_node`` inspects: ``node.left.object.type == "ThisExpression"``,
    ``node.left.property.name == "checkKey_"``, and ``node.right`` carrying
    the source ``start``/``end`` offsets.
    """
    return SimpleNamespace(
        type="AssignmentExpression",
        left=SimpleNamespace(
            type="MemberExpression",
            object=SimpleNamespace(type="ThisExpression"),
            property=SimpleNamespace(type="Identifier", name="checkKey_"),
        ),
        right=SimpleNamespace(start=start, end=end),
    )


def make_acorn_require(
    spans: Iterable[tuple[int, int]] | None = None,
    *,
    nodes: Sequence[Any] | None = None,
    parse_error: Exception | None = None,
) -> Callable[[str], Any]:
    """Build a fake ``require`` driving the AST-fallback walk.

    :param spans: ``(start, end)`` source offsets of ``this.checkKey_``
        assignments the simulated walk should "discover". The production
        code slices ``js_content[start:end]`` and feeds it to ``eval_js``.
    :param nodes: raw fake nodes to feed the walk callback instead of
        ``spans`` — for exercising the non-matching-node skip branches.
    :param parse_error: if set, ``acorn.parse`` raises it (drives the
        ``except`` path in the fallback).
    """
    walk_nodes: list[Any] = (
        list(nodes)
        if nodes is not None
        else [_make_checkkey_node(start, end) for start, end in (spans or [])]
    )

    mock_acorn = MagicMock(name="acorn")
    if parse_error is not None:
        mock_acorn.parse.side_effect = parse_error
    else:
        mock_acorn.parse.return_value = MagicMock(name="ast")

    mock_walk = MagicMock(name="acorn_walk")

    def simulate_walk(ast: Any, callbacks: dict[str, Any]) -> None:
        callback = callbacks.get("AssignmentExpression")
        if callback is None:
            return
        for node in walk_nodes:
            callback(node, None)

    mock_walk.simple.side_effect = simulate_walk

    def fake_require(module: str) -> Any:
        if module == "acorn":
            return mock_acorn
        if module == "acorn-walk":
            return mock_walk
        return MagicMock()

    return fake_require


def make_fake_connection() -> MagicMock:
    """Build a fake JSPyBridge ``connection`` with drained queues.

    ``_extract_checkkey_ast_fallback`` polls ``connection.sendQ`` and
    ``connection.com_items`` to decide when the bridge has drained.
    Empty lists make the drain loop exit on its first iteration.
    """
    conn = MagicMock(name="connection")
    conn.sendQ = []
    conn.com_items = []
    return conn
