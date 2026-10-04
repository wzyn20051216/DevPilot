"""! @brief 静态代码语义分析工具。

为超出运行时探针范围的静态分析问题提供观察手段,包括 AST 分析、类型推断、
符号解析等能力。解决 astroid-1333/1978 类型的"深层语义"问题。
"""
import ast
import json
from pathlib import Path
from typing import Any

from ..sandbox.docker_runner import run_in_sandbox


def _analyze_ast_structure(repo_path: str, file_path: str, target_node: str | None) -> dict[str, Any]:
    """分析文件的 AST 结构

    Args:
        repo_path: 仓库根目录
        file_path: 要分析的文件路径(相对于仓库根目录)
        target_node: 可选,目标节点名称(类名/函数名)

    Returns:
        包含 AST 结构信息的字典
    """
    full_path = Path(repo_path) / file_path
    if not full_path.exists():
        return {"error": f"文件不存在: {file_path}"}

    try:
        source_code = full_path.read_text(encoding="utf-8")
        tree = ast.parse(source_code, filename=file_path)
    except SyntaxError as e:
        return {"error": f"语法错误: {e}"}
    except Exception as e:
        return {"error": f"解析失败: {e}"}

    result = {
        "file_path": file_path,
        "classes": [],
        "functions": [],
        "imports": [],
    }

    # 提取类定义
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            class_info = {
                "name": node.name,
                "line": node.lineno,
                "methods": [m.name for m in node.body if isinstance(m, ast.FunctionDef)],
                "bases": [_get_node_name(base) for base in node.bases],
            }
            # 如果指定了目标节点,只返回匹配的类
            if target_node is None or target_node == node.name:
                result["classes"].append(class_info)

        elif isinstance(node, ast.FunctionDef):
            # 只收集模块级函数(不在类内的)
            if not any(isinstance(parent, ast.ClassDef) for parent in ast.walk(tree)):
                func_info = {
                    "name": node.name,
                    "line": node.lineno,
                    "args": [arg.arg for arg in node.args.args],
                }
                if target_node is None or target_node == node.name:
                    result["functions"].append(func_info)

        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    result["imports"].append({
                        "module": alias.name,
                        "alias": alias.asname,
                    })
            else:  # ImportFrom
                module = node.module or ""
                for alias in node.names:
                    result["imports"].append({
                        "from": module,
                        "name": alias.name,
                        "alias": alias.asname,
                    })

    return result


def _get_node_name(node: ast.AST) -> str:
    """获取 AST 节点的名称"""
    if isinstance(node, ast.Name):
        return node.id
    elif isinstance(node, ast.Attribute):
        value = _get_node_name(node.value)
        return f"{value}.{node.attr}"
    elif isinstance(node, ast.Constant):
        return str(node.value)
    else:
        return ast.unparse(node)


def _analyze_module_structure(repo_path: str, module_path: str) -> dict[str, Any]:
    """分析模块的导入关系和结构

    Args:
        repo_path: 仓库根目录
        module_path: 模块路径(如 'pydicom.valuerep')

    Returns:
        模块结构信息
    """
    # 将模块路径转换为文件路径
    file_path = module_path.replace(".", "/")
    potential_paths = [
        f"{file_path}.py",
        f"{file_path}/__init__.py",
    ]

    for path in potential_paths:
        full_path = Path(repo_path) / path
        if full_path.exists():
            return _analyze_ast_structure(repo_path, path, None)

    return {"error": f"未找到模块: {module_path}"}


def _check_type_hints(repo_path: str, file_path: str) -> dict[str, Any]:
    """检查文件中的类型注解

    Args:
        repo_path: 仓库根目录
        file_path: 要检查的文件路径

    Returns:
        类型注解信息
    """
    full_path = Path(repo_path) / file_path
    if not full_path.exists():
        return {"error": f"文件不存在: {file_path}"}

    try:
        source_code = full_path.read_text(encoding="utf-8")
        tree = ast.parse(source_code, filename=file_path)
    except Exception as e:
        return {"error": f"解析失败: {e}"}

    annotated_functions = []

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            annotations = {}
            # 参数类型注解
            for arg in node.args.args:
                if arg.annotation:
                    annotations[arg.arg] = ast.unparse(arg.annotation)
            # 返回类型注解
            if node.returns:
                annotations["return"] = ast.unparse(node.returns)

            if annotations:
                annotated_functions.append({
                    "function": node.name,
                    "line": node.lineno,
                    "annotations": annotations,
                })

    return {
        "file_path": file_path,
        "annotated_functions": annotated_functions,
        "has_type_hints": len(annotated_functions) > 0,
    }


def analyze_code_semantics(
    repo_path: str,
    file_path: str | None = None,
    target_class: str | None = None,
    target_function: str | None = None,
    check_types: bool = False,
) -> dict[str, Any]:
    """! @brief 分析代码的静态语义。

    @param repo_path 仓库根目录
    @param file_path 可选,要分析的文件路径(相对于仓库根)
    @param target_class 可选,目标类名
    @param target_function 可选,目标函数名
    @param check_types 是否检查类型注解
    @return 包含 AST 结构、类型信息的字典
    """

    result = {}

    if file_path:
        # 分析指定文件的 AST
        target_node = target_class or target_function
        result["ast_analysis"] = _analyze_ast_structure(repo_path, file_path, target_node)

        # 检查类型注解
        if check_types:
            result["type_analysis"] = _check_type_hints(repo_path, file_path)

    # 汇总关键信息
    summary = []
    if "ast_analysis" in result and not result["ast_analysis"].get("error"):
        ast_result = result["ast_analysis"]
        if ast_result.get("classes"):
            summary.append(f"找到 {len(ast_result['classes'])} 个类定义")
        if ast_result.get("functions"):
            summary.append(f"找到 {len(ast_result['functions'])} 个函数")
        if ast_result.get("imports"):
            summary.append(f"找到 {len(ast_result['imports'])} 个导入")

    if check_types and "type_analysis" in result:
        if result["type_analysis"].get("has_type_hints"):
            count = len(result["type_analysis"]["annotated_functions"])
            summary.append(f"{count} 个函数有类型注解")
        else:
            summary.append("未找到类型注解")

    result["summary"] = "; ".join(summary) if summary else "未找到分析结果"

    return result
