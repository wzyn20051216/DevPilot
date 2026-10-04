"""! @brief 动态 RAG 启用策略（技术手册 10.2.3）。

背景：RAG 此前是全局开关（execution_mode 里的 single_rag / multi_rag）。
小样本未观察到 RAG 修复率收益；大仓库是否受益仍待对照验证。因此本模块把「是否启用 RAG」从全局开关拆成
按仓库规模与查询歧义动态决定的纯函数决策，保证离线、确定性、无网络副作用。

决策输入只有两类：仓库源码文件数（文件系统遍历）和查询文本（正则提取）。
决策不构建索引、不发请求，除遍历仓库目录外没有任何副作用。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from ..config import Settings, get_settings


# --- 源码文件统计相关常量 ---

# 计入仓库规模的源码扩展名：涵盖主流后端/前端/系统语言，
# 与 chunker.SUPPORTED_EXTENSIONS 的「代码文件」语义一致（不含文档/配置）。
SOURCE_EXTENSIONS = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".java", ".go", ".rs",
    ".c", ".cc", ".cpp", ".h", ".hpp", ".rb", ".php", ".cs",
    ".kt", ".swift", ".scala", ".m",
}

# 遍历时需要跳过的目录：依赖、构建产物、缓存和 IDE 元数据。
# 它们不是业务源码，统计进去会把仓库规模误判成大仓库，徒增 RAG 开销。
IGNORED_DIRS = {
    ".git", "node_modules", ".venv", "venv", "__pycache__", ".devpilot",
    "dist", "build", "target", ".idea", ".vscode", ".pytest_cache", ".mypy_cache",
}

# 遍历文件数上限。决策必须毫秒级完成，不能像索引构建那样全量扫描巨型仓库；
# 一旦命中该上限即按上限计数返回，防止超大 monorepo 拖慢任务启动。
MAX_WALK_FILES = 20_000


# --- 查询定位信号提取相关常量 ---

# 符号名最短长度：太短的 token（如 foo/bar）多为普通单词，不具备定位价值。
MIN_SYMBOL_LENGTH = 6

# 符号名提取上限：堆栈/长报错里可能刷出大量符号，截断避免过度解读。
MAX_SYMBOL_SIGNALS = 10

# 全部定位信号的上限：去重保序后最多保留这么多，保证决策可解释且稳定。
MAX_SIGNALS = 20

# 文件引用：带路径的源码文件名。\b 保证只匹配完整文件名，不被标点打断。
FILE_REFERENCE_PATTERN = re.compile(
    r"\b[\w./\\-]+\.(?:py|ts|tsx|js|jsx|java|go|rs|c|cc|cpp|h|hpp)\b"
)

# 符号名：CamelCase（首字母大写）或 snake_case（小写字母 + 下划线分组）。
# 路径（含 / 或 \\）无法进入字符类，天然被排除，不会与文件引用重复。
SYMBOL_PATTERN = re.compile(
    r"\b[A-Z][A-Za-z0-9]{5,}\b"
    r"|\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b"
)

# 报错类型：Traceback / Exception / Error 及形如 *Error / *Exception 的具体类型名。
ERROR_TYPE_PATTERN = re.compile(
    r"\b(?:Traceback|Exception|Error|[A-Za-z_]\w*(?:Error|Exception))\b"
)

# 引号内代码片段：单引号、双引号、反引号包裹的片段，通常是用户贴出的具体代码。
QUOTED_PATTERN = re.compile(
    r"`[^`\n]{1,}`|\"[^\"\n]{1,}\"|'[^'\n]{1,}'"
)


@dataclass(frozen=True)
class RagDecision:
    """! @brief 一次动态 RAG 决策的结果。

    @attribute enabled 是否启用 RAG（对应 SingleDeveloperAgent 的 enable_rag）。
    @attribute mode 决策命中的分支：
        - small_repo：小仓库，不启用；
        - large_repo：大仓库，启用；
        - ambiguous_query：中间地带 + 歧义查询，启用；
        - specific_query：中间地带 + 定位明确，不启用。
    @attribute reasons 中文理由列表，供日志与前端解释。
    @attribute metrics 决策依据的可观测指标：
        source_files（源码文件数）、query_chars（问题长度）、signals（定位信号列表）。
    """

    enabled: bool
    mode: str
    reasons: list[str]
    metrics: dict[str, object]


def count_source_files(
    repo_path: str | Path,
    extensions: set[str] | frozenset[str] | None = None,
) -> int:
    """! @brief 统计仓库内会被索引的源码文件数。

    用 os.walk 自上而下遍历，并在进入噪声目录前剪枝（dirs[:] 原地过滤），
    避免对 node_modules / .venv 等大目录做无意义的递归展开。

    @param repo_path 代码仓库根目录。
    @param extensions 可选的扩展名白名单；默认统计全部受支持源码。
    @return 源码文件数；路径不存在时返回 0；命中遍历上限时按上限返回。
    """

    repo = Path(repo_path)
    if not repo.is_dir():
        return 0

    selected_extensions = SOURCE_EXTENSIONS if extensions is None else extensions
    count = 0
    for _root, dirs, files in os.walk(repo):
        # topdown 剪枝：把忽略目录从待遍历列表中原地移除，os.walk 就不会再深入。
        dirs[:] = [name for name in dirs if name not in IGNORED_DIRS]

        for name in files:
            if Path(name).suffix.lower() not in selected_extensions:
                continue
            count += 1
            # 防性能坑：巨型仓库不追求精确计数，达到上限即返回。
            if count >= MAX_WALK_FILES:
                return count

    return count


def extract_query_signals(question: str) -> list[str]:
    """! @brief 从问题描述中提取定位信号，判断查询是否「指向明确」。

    按置信度从高到低依次提取，去重保序：
    1. 文件引用（如 src/utils/helpers.py）；
    2. 符号名（CamelCase 或 snake_case，长度 ≥ 6，最多 10 个）；
    3. 报错类型（Traceback / AttributeError 等具体类型名）；
    4. 引号内代码片段（单/双/反引号）。

    @param question 用户原始任务描述。
    @return 去重保序的定位信号列表，最多 20 个。
    """

    signals: list[str] = []
    seen: set[str] = set()

    def add(signal: str) -> None:
        """去重并追加一个非空信号。"""
        signal = signal.strip()
        if signal and signal not in seen:
            seen.add(signal)
            signals.append(signal)

    # 1) 文件引用：最高置信度，直接给出要改的文件。
    for match in FILE_REFERENCE_PATTERN.finditer(question):
        add(match.group(0))

    # 2) 符号名：定位类/函数/变量；单独限制数量，避免长堆栈刷屏。
    symbol_count = 0
    for match in SYMBOL_PATTERN.finditer(question):
        if symbol_count >= MAX_SYMBOL_SIGNALS:
            break
        token = match.group(0)
        # CamelCase 分支已保证 ≥6，snake_case 分支仍需此处兜底长度过滤。
        if len(token) < MIN_SYMBOL_LENGTH:
            continue
        add(token)
        symbol_count += 1

    # 3) 报错类型：说明用户在描述一个故障现场，需要语义检索辅助定位根因。
    for match in ERROR_TYPE_PATTERN.finditer(question):
        add(match.group(0))

    # 4) 引号内代码片段：用户贴出的具体代码，去掉引号保留内容。
    for match in QUOTED_PATTERN.finditer(question):
        add(match.group(0)[1:-1])

    return signals[:MAX_SIGNALS]


def decide_rag(
    repo_path: str | Path,
    question: str,
    settings: Settings | None = None,
) -> RagDecision:
    """! @brief 按仓库规模与查询歧义决定是否启用 RAG。

    决策规则（三层，按顺序短路）：
    - 源码文件数 ≤ rag_auto_small_repo_files：小仓库，search_code 全文检索足够，
      索引构建与检索延迟不划算 → 不启用（small_repo）；
    - 源码文件数 ≥ rag_auto_large_repo_files：大仓库，语义检索能显著缩小定位范围
      → 启用（large_repo）；
    - 中间地带看查询歧义：问题短于 rag_auto_query_signal_chars 且无任何定位信号
      → 歧义查询，需要语义检索辅助 → 启用（ambiguous_query）；
      否则定位明确，直接检索足够 → 不启用（specific_query）。

    @param repo_path 代码仓库根目录。
    @param question 用户原始任务描述。
    @param settings 配置对象；默认取进程内缓存单例，便于测试注入。
    @return 包含 enabled / mode / reasons / metrics 的决策结果。
    """

    if settings is None:
        settings = get_settings()

    files = count_source_files(repo_path)
    signals = extract_query_signals(question)
    query_chars = len(question)

    metrics: dict[str, object] = {
        "source_files": files,
        "query_chars": query_chars,
        "signals": signals,
    }

    if files <= settings.rag_auto_small_repo_files:
        return RagDecision(
            enabled=False,
            mode="small_repo",
            reasons=[
                f"源码文件数 {files} 不超过小仓库阈值 "
                f"{settings.rag_auto_small_repo_files}，"
                "search_code 全文检索已足够，索引构建与检索延迟不划算",
            ],
            metrics=metrics,
        )

    if files >= settings.rag_auto_large_repo_files:
        return RagDecision(
            enabled=True,
            mode="large_repo",
            reasons=[
                f"源码文件数 {files} 达到大仓库阈值 "
                f"{settings.rag_auto_large_repo_files}，"
                "语义检索可显著缩小定位范围",
            ],
            metrics=metrics,
        )

    # 中间地带：规模既不极小也不极大，交由查询歧义度决定。
    if query_chars < settings.rag_auto_query_signal_chars and not signals:
        return RagDecision(
            enabled=True,
            mode="ambiguous_query",
            reasons=[
                f"问题仅 {query_chars} 字符且无定位信号（阈值 "
                f"{settings.rag_auto_query_signal_chars}），歧义度高，"
                "需要语义检索辅助定位",
            ],
            metrics=metrics,
        )

    return RagDecision(
        enabled=False,
        mode="specific_query",
        reasons=[
            f"问题长度 {query_chars} 或定位信号 {signals} 已明确，"
            "直接检索足够，无需构建索引",
        ],
        metrics=metrics,
    )
