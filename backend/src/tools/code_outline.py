"""! @brief 基于 AST 的代码结构导航工具。

参考 AutoCodeRover 的 search_class / search_method_in_class 思路：模型先看
类与方法的签名骨架和行号范围，再按符号精确取源码，而不是整段 read_file。
只解析文本，不导入、不执行目标仓库代码。
"""

import ast
from pathlib import Path
from typing import Any

from .file_tool import resolve_safe_path

# 单个符号源码的最大行数，超出部分截断，避免一次性撑满上下文。
_MAX_SYMBOL_LINES = 160
# 骨架中最多列出的顶层定义数，防止超大模块输出失控。
_MAX_OUTLINE_ITEMS = 120


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """! @brief 还原函数签名文本（含装饰器提示），不包含函数体。"""

    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    returns = f" -> {ast.unparse(node.returns)}" if node.returns else ""
    return f"{prefix} {node.name}({ast.unparse(node.args)}){returns}"


def _decorators(node: ast.AST) -> list[str]:
    """! @brief 返回装饰器表达式文本，用于识别 property/classmethod 等语义。"""

    return [ast.unparse(item) for item in getattr(node, "decorator_list", [])]


def _outline(tree: ast.Module) -> list[dict[str, Any]]:
    """! @brief 只遍历模块顶层和类体，生成结构骨架。"""

    items: list[dict[str, Any]] = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            methods = [
                {
                    "name": child.name,
                    "signature": _signature(child),
                    "decorators": _decorators(child),
                    "lines": [child.lineno, child.end_lineno],
                }
                for child in node.body
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            ]
            items.append(
                {
                    "kind": "class",
                    "name": node.name,
                    "bases": [ast.unparse(base) for base in node.bases],
                    "decorators": _decorators(node),
                    "lines": [node.lineno, node.end_lineno],
                    "methods": methods,
                }
            )
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            items.append(
                {
                    "kind": "function",
                    "name": node.name,
                    "signature": _signature(node),
                    "decorators": _decorators(node),
                    "lines": [node.lineno, node.end_lineno],
                }
            )
        if len(items) >= _MAX_OUTLINE_ITEMS:
            break
    return items


def _find_symbol(tree: ast.Module, symbol: str) -> ast.AST | None:
    """! @brief 按 `Class`、`function` 或 `Class.method` 查找定义节点。"""

    parts = symbol.split(".")
    scope: list[ast.stmt] = tree.body
    found: ast.AST | None = None
    for part in parts:
        found = next(
            (
                node
                for node in scope
                if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == part
            ),
            None,
        )
        if found is None:
            return None
        scope = getattr(found, "body", [])
    return found


def code_outline(
    repo_path: str,
    file_path: str,
    symbol: str | None = None,
) -> dict[str, Any]:
    """! @brief 返回 Python 文件的结构骨架，或指定符号带行号的源码。

    @param repo_path 仓库根目录。
    @param file_path 相对仓库根目录的 .py 文件路径。
    @param symbol 可选，`Class`、`function` 或 `Class.method`。
    @return 无 symbol 时返回 outline；有 symbol 时返回该定义的行号与源码。
    @raise ValueError 路径越界、非 Python 文件或语法无法解析时抛出。
    """

    path = resolve_safe_path(Path(repo_path), relative_path=file_path)
    if path.suffix != ".py" or not path.is_file():
        raise ValueError(f"{file_path} 不是已有的 Python 文件")
    source = path.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(source, filename=file_path)
    except SyntaxError as exc:
        raise ValueError(f"{file_path} 无法解析: 第 {exc.lineno} 行 {exc.msg}") from exc

    if not symbol:
        return {"file_path": file_path, "outline": _outline(tree)}

    node = _find_symbol(tree, symbol)
    if node is None:
        return {
            "file_path": file_path,
            "symbol": symbol,
            "found": False,
            "hint": "未找到该定义；先不带 symbol 调用查看骨架中的准确名称",
        }

    start = min([node.lineno, *(item.lineno for item in _decorator_nodes(node))])
    end = node.end_lineno or node.lineno
    lines = source.splitlines()
    stop = min(end, start + _MAX_SYMBOL_LINES - 1)
    body = "\n".join(
        f"{number}: {lines[number - 1]}" for number in range(start, stop + 1)
    )
    return {
        "file_path": file_path,
        "symbol": symbol,
        "found": True,
        "lines": [start, end],
        "truncated": stop < end,
        "source": body,
    }


def _decorator_nodes(node: ast.AST) -> list[ast.expr]:
    """! @brief 返回装饰器节点，使源码起始行包含装饰器。"""

    return list(getattr(node, "decorator_list", []))
