"""! @brief 仓库模式分析工具。

帮助 Agent 理解仓库特定的架构、约定和代码模式,解决"仓库特定知识缺失"问题。
"""
import json
import os
from pathlib import Path
from typing import Any

from ..sandbox.docker_runner import run_in_sandbox


def _find_architecture_docs(repo_path: str) -> list[dict[str, str]]:
    """查找仓库中的架构文档"""
    doc_patterns = [
        "ARCHITECTURE.md",
        "CONTRIBUTING.md",
        "docs/architecture.md",
        "docs/ARCHITECTURE.md",
        "README.md",
        "docs/README.md",
    ]

    docs = []
    for pattern in doc_patterns:
        doc_path = Path(repo_path) / pattern
        if doc_path.exists() and doc_path.is_file():
            try:
                content = doc_path.read_text(encoding="utf-8", errors="ignore")
                # 只提取前 3000 字符,避免超长文档
                if len(content) > 3000:
                    content = content[:3000] + "\n...(文档已截断)"
                docs.append({
                    "path": pattern,
                    "content": content,
                })
            except Exception:
                pass

    return docs


def _analyze_code_patterns(repo_path: str, target_function: str) -> dict[str, Any]:
    """分析仓库中类似功能的实现模式

    使用 grep 查找相似的函数/类,提取实现模式
    """
    # 在沙箱中运行 grep 查找类似函数
    outcome = run_in_sandbox(
        repo_path=repo_path,
        argv=[
            "bash",
            "-c",
            f"grep -r 'def {target_function}' --include='*.py' . | head -10",
        ],
        timeout=10,
    )

    stdout = str(outcome.get("stdout", ""))
    matches = []

    for line in stdout.strip().split("\n"):
        if ":" in line and line.strip():
            file_path, match = line.split(":", 1)
            matches.append({
                "file": file_path.strip(),
                "line": match.strip(),
            })

    return {
        "target_function": target_function,
        "similar_implementations": matches[:5],  # 最多返回 5 个
    }


def _extract_api_patterns(repo_path: str, focus_area: str | None) -> dict[str, Any]:
    """提取仓库特定的 API 模式和约定"""
    patterns = {
        "common_imports": [],
        "utility_modules": [],
        "naming_conventions": {},
    }

    # 查找常用的工具模块
    util_dirs = ["utils", "helpers", "common", "core"]
    for util_dir in util_dirs:
        util_path = Path(repo_path) / util_dir
        if util_path.exists() and util_path.is_dir():
            py_files = list(util_path.glob("*.py"))
            patterns["utility_modules"].extend([
                f"{util_dir}/{f.name}" for f in py_files[:10]
            ])

    # 如果指定了关注领域,查找相关模块
    if focus_area:
        outcome = run_in_sandbox(
            repo_path=repo_path,
            argv=[
                "bash",
                "-c",
                f"find . -name '*.py' -path '*{focus_area}*' | head -10",
            ],
            timeout=10,
        )
        stdout = str(outcome.get("stdout", ""))
        related_files = [line.strip() for line in stdout.split("\n") if line.strip()]
        patterns["related_modules"] = related_files

    return patterns


def analyze_repository_context(
    repo_path: str,
    focus_area: str | None = None,
    target_function: str | None = None,
) -> dict[str, Any]:
    """! @brief 分析仓库上下文,提取架构约定和代码模式。

    @param repo_path 仓库根目录
    @param focus_area 可选,关注的领域/模块(如 'linter', 'parser')
    @param target_function 可选,目标函数名,将查找类似实现
    @return 包含架构文档、代码模式和 API 约定的字典
    """

    result = {
        "architecture_docs": _find_architecture_docs(repo_path),
        "api_patterns": _extract_api_patterns(repo_path, focus_area),
    }

    if target_function:
        result["code_patterns"] = _analyze_code_patterns(repo_path, target_function)

    # 汇总关键信息
    summary = []
    if result["architecture_docs"]:
        summary.append(f"找到 {len(result['architecture_docs'])} 个架构文档")
    if result["api_patterns"]["utility_modules"]:
        summary.append(
            f"常用工具模块: {', '.join(result['api_patterns']['utility_modules'][:3])}"
        )
    if target_function and result.get("code_patterns"):
        pattern_count = len(result["code_patterns"]["similar_implementations"])
        summary.append(f"找到 {pattern_count} 个类似实现")

    result["summary"] = "; ".join(summary) if summary else "未找到明显的架构信息"

    return result
