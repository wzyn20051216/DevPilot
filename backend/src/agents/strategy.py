"""! @brief 按任务特征选择单 Agent 工作流。

路由器只读取仓库结构和用户问题，不构建索引、不调用网络，因此 API、
Worker 和离线评测可以共用同一个可测、可解释的决策函数。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..config import Settings, get_settings
from ..rag.policy import SOURCE_EXTENSIONS, count_source_files, extract_query_signals


DEFAULT_MAX_ITERATIONS = 14
PYTHON_EXTENSIONS = frozenset({".py"})

# 这些词表是问题分类特征，不是仓库或评测题的特判。
BEHAVIOR_KEYWORDS = (
    "attributeerror",
    "typeerror",
    "迭代",
    "迭代器",
    "生成器",
    "协议",
    "iteration",
    "iterator",
    "generator",
    "protocol",
    "序列化",
    "serialization",
    "__iter__",
    "__next__",
    "next(",
)
NON_BEHAVIOR_KEYWORDS = (
    "文档",
    "documentation",
    "docstring",
    "readme",
    "字符串格式化",
    "string formatting",
    "配置",
    "configuration",
    "config file",
)


@dataclass(frozen=True)
class AgentStrategy:
    """! @brief 一次任务级策略决策的结果。"""

    use_outline: bool
    enforce_probe: bool
    max_iterations: int
    mode: str
    reasons: list[str]
    metrics: dict[str, object]


def _source_file_signals(signals: list[str]) -> list[str]:
    """! @brief 从通用定位信号中保留源码文件引用。"""

    return [
        signal
        for signal in signals
        if Path(signal).suffix.lower() in SOURCE_EXTENSIONS
    ]


def _safe_python_line_count(repo: Path, reference: str) -> int | None:
    """! @brief 安全读取仓库内明确 Python 目标文件的行数。

    绝对路径、越界路径、非 Python 文件和不存在的文件都返回 None。
    """

    relative = Path(reference.replace("\\", "/"))
    if relative.is_absolute() or relative.suffix.lower() != ".py":
        return None
    root = repo.resolve()
    target = (root / relative).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        return None
    try:
        with target.open("r", encoding="utf-8", errors="replace") as source:
            return sum(1 for _line in source)
    except OSError:
        return None


def decide_strategy(
    repo_path: str | Path,
    question: str,
    settings: Settings | None = None,
) -> AgentStrategy:
    """! @brief 按仓库结构与问题语义决定本轮 Agent 策略。

    @param repo_path 任务工作区根目录。
    @param question 用户原始问题。
    @param settings 可注入的配置，默认使用进程单例。
    @return 可解释、可观测的任务级策略。
    """

    active_settings = settings if settings is not None else get_settings()
    repo = Path(repo_path)
    source_files = count_source_files(repo)
    python_files = count_source_files(repo, PYTHON_EXTENSIONS)
    python_ratio = python_files / source_files if source_files else 0.0
    signals = extract_query_signals(question)
    file_references = _source_file_signals(signals)
    target_line_counts = {
        reference: line_count
        for reference in file_references
        if (line_count := _safe_python_line_count(repo, reference)) is not None
    }

    metrics: dict[str, object] = {
        "source_files": source_files,
        "python_files": python_files,
        "python_ratio": round(python_ratio, 6),
        "signals": signals,
        "file_references": file_references,
        "target_line_counts": target_line_counts,
    }
    reasons: list[str] = []

    # code_outline 只理解 Python AST；非 Python 主导仓库先短路关闭。
    if python_ratio < active_settings.strategy_python_min_ratio:
        use_outline = False
        reasons.append(
            f"Python 文件占比 {python_ratio:.1%} 低于阈值 "
            f"{active_settings.strategy_python_min_ratio:.1%}，code_outline 不适用"
        )
    elif file_references:
        # 显式目标优先：只在至少一个已定位 Python 文件达到行数阈值时开启。
        large_targets = {
            reference: lines
            for reference, lines in target_line_counts.items()
            if lines >= active_settings.strategy_outline_min_lines
        }
        use_outline = bool(large_targets)
        if large_targets:
            reasons.append(
                f"目标文件 {large_targets} 达到 "
                f"{active_settings.strategy_outline_min_lines} 行阈值，启用结构导航"
            )
        elif target_line_counts:
            reasons.append(
                f"明确目标文件行数 {target_line_counts} 均低于 "
                f"{active_settings.strategy_outline_min_lines}，直接局部读取更经济"
            )
        else:
            reasons.append(
                "问题含文件引用但无可安全定位的 Python 目标，"
                "不冒进启用结构导航"
            )
    else:
        # 无明确文件时才用仓库规模作为代理特征，避免小修改被过度导航。
        use_outline = source_files >= active_settings.strategy_outline_min_repo_files
        comparison = "达到" if use_outline else "低于"
        reasons.append(
            f"问题无明确文件引用，仓库 {source_files} 个源码文件"
            f"{comparison} {active_settings.strategy_outline_min_repo_files} 的结构导航阈值"
        )

    normalized_question = question.casefold()
    behavior_hits = [
        keyword for keyword in BEHAVIOR_KEYWORDS if keyword in normalized_question
    ]
    non_behavior_hits = [
        keyword for keyword in NON_BEHAVIOR_KEYWORDS if keyword in normalized_question
    ]
    # 行为语义特征优先级更高：即使问题同时提到文档，也要保留真实探针。
    enforce_probe = bool(behavior_hits)
    if enforce_probe:
        reasons.append(f"命中行为语义特征 {behavior_hits}，强制先探针后编辑")
    elif non_behavior_hits:
        reasons.append(f"命中文档/格式/配置特征 {non_behavior_hits}，不强制运行时探针")
    else:
        reasons.append("未命中行为语义特征，保持标准验证流程")

    metrics["behavior_hits"] = behavior_hits
    metrics["non_behavior_hits"] = non_behavior_hits
    if use_outline and enforce_probe:
        mode = "outline_and_probe"
    elif use_outline:
        mode = "outline_only"
    elif enforce_probe:
        mode = "probe_only"
    else:
        mode = "standard"

    return AgentStrategy(
        use_outline=use_outline,
        enforce_probe=enforce_probe,
        max_iterations=DEFAULT_MAX_ITERATIONS,
        mode=mode,
        reasons=reasons,
        metrics=metrics,
    )
