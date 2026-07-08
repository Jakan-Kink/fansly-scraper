"""AST audit helpers for verifying lock discipline in source files."""

import ast


def _is_self_lock_with(node: ast.With, lock_attr: str) -> bool:
    for item in node.items:
        ctx = item.context_expr
        if (
            isinstance(ctx, ast.Attribute)
            and ctx.attr == lock_attr
            and isinstance(ctx.value, ast.Name)
            and ctx.value.id == "self"
        ):
            return True
    return False


def iter_lock_blocks(tree: ast.AST, lock_attr: str) -> list[ast.With]:
    """Return every ``with self.<lock_attr>:`` block in the tree."""
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.With) and _is_self_lock_with(node, lock_attr)
    ]


def forbidden_nodes_in_lock_blocks(
    tree: ast.AST, lock_attr: str
) -> list[tuple[int, str]]:
    """Return (lineno, kind) for every await/yield inside a lock block."""
    violations: list[tuple[int, str]] = []
    for block in iter_lock_blocks(tree, lock_attr):
        for node in ast.walk(block):
            if isinstance(node, ast.Await):
                violations.append((node.lineno, "await"))
            elif isinstance(node, ast.Yield | ast.YieldFrom):
                violations.append((node.lineno, "yield"))
    return violations


def methods_containing_lock(tree: ast.AST, lock_attr: str) -> set[str]:
    """Names of functions containing a ``with self.<lock_attr>:`` block."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            for child in ast.walk(node):
                if isinstance(child, ast.With) and _is_self_lock_with(child, lock_attr):
                    names.add(node.name)
                    break
    return names
