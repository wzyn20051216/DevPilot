"""代码仓库分块器（chunker）。

本模块负责把仓库里的源代码按「自然语法单元」切成多个 CodeChunk，
供后续嵌入（embedding）和向量检索使用。

策略：
- Python 文件：用 `ast` 解析 AST，按顶层「函数 / 异步函数 / 类」切块；
- 其它语言（或 Python 语法错误时）：交给 `chunk_text_file` 做定长滑动窗口切块。
"""

import ast
import hashlib
from pathlib import Path

from ..rag.models import CodeChunk


# 支持做语法切块的文件扩展名集合。
# Python 由 ast 处理；其它语言目前走通用文本切块（chunk_text_file）。
SUPPORTED_EXTENSIONS = {
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".java",
    ".go",
    ".rs",
    ".cpp",
    ".c",
    ".h",
    ".md",
    ".toml",
    ".yaml",
    ".yml",
}


def make_chunk_id(
    file_path: str,
    start_line: int,
    end_line: int,
) -> str:
    """为一个代码块生成稳定的 SHA-1 id。

    用「文件路径 + 起止行号」拼接后取 SHA-1，保证：
    - 同一位置的内容多次索引得到同一个 id（用于去重 / 增量更新）；
    - id 是定长 hex 字符串，便于存入向量库。

    Args:
        file_path: 相对于仓库根的文件路径（POSIX 风格）。
        start_line: 块起始行号（1-based）。
        end_line: 块结束行号（1-based，包含）。

    Returns:
        str: 40 字符的 SHA-1 十六进制字符串。
    """
    raw = (
        f"{file_path}:"
        f"{start_line}:"
        f"{end_line}"
    )

    return hashlib.sha1(
        raw.encode("utf-8")
    ).hexdigest()


def chunk_python_file(
    repo: Path,
    path: Path,
) -> list[CodeChunk]:
    """把一个 Python 文件按顶层「函数 / 异步函数 / 类」切成多个 CodeChunk。

    流程：
    1. 读源码并按行切分（便于用行号区间取原文）；
    2. 用 `ast.parse` 解析 AST；解析失败（如语法错误）则降级为 `chunk_text_file`；
    3. 遍历 AST 顶层节点，仅保留 `FunctionDef` / `AsyncFunctionDef` / `ClassDef`；
    4. 对每个目标节点，按 `lineno` ~ `end_lineno` 取原文片段，
       构造 `CodeChunk` 并加入结果列表。

    Args:
        repo: 仓库根目录（用于计算相对路径）。
        path: 待分块的 Python 文件绝对路径。

    Returns:
        list[CodeChunk]: 切出的代码块列表（可能为空，调用方需自行兜底）。
    """
    text = path.read_text(encoding="utf-8", errors="ignore")
    # 按行切分，后面用行号区间取片段时按 1-based 索引需减 1。
    lines = text.splitlines()
    # 计算文件相对仓库根的 POSIX 风格路径，作为 CodeChunk.file_path。
    relative = (path.relative_to(repo).as_posix())
    try:
        tree = ast.parse(text)
    except SyntaxError:
        # 语法解析失败时降级到通用文本切块（保证索引仍能产生一些 chunk）。
        return chunk_text_file(repo, path)
    chunks: list[CodeChunk] = []

    for node in tree.body:
        # 只关心顶层「函数 / 异步函数 / 类」三类定义节点，其它（import / 赋值等）跳过。
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        start = node.lineno
        # Python 3.8+ AST 节点自带 end_lineno；旧版本没有则退化为 start（单行）。
        end = getattr(node, "end_lineno", start)

        # 按行号区间取原文；splitlines() 后索引从 0 开始，所以 start-1。
        content = "\n".join(lines[start - 1:end])
        symbol = node.name
        chunks.append(CodeChunk(
            id=make_chunk_id(relative, start, end),
            file_path=relative,
            language="python",
            symbol=symbol,
            start_line=start,
            end_line=end,
            content=content,
            # embedding_text 是给 embedding 模型吃的「带上下文的文本」：
            # 前面拼上文件名 + symbol 名，让语义检索时知道这段代码来自哪个文件、哪个符号。
            embedding_text=(
                f"File:{relative}\n"
                f"Symbol:{symbol}\n\n"
                f"{content}"
            ),
        ))

    # for 跑完了还没找到任何「函数 / 类」节点时（如纯脚本只有 import 和语句），
    # 降级到通用文本切块，保证索引至少能产出一些 chunk。
    if not chunks:
        return chunk_text_file(repo, path)
    return chunks


def chunk_text_file(
    repo: Path,
    path: Path,
    chunk_lines: int = 80,
    overlap: int = 15,
) -> list[CodeChunk]:
    """通用文本分块：用「定长窗口 + 行间重叠」的滑动窗口把文件切成多个 chunk。

    适用于两类场景：
    1. 非 Python 文件（.js/.ts/.go/.md 等没有对应 AST 解析器的语言）；
    2. Python 文件但 `ast.parse` 抛 `SyntaxError`（语法错误）时的降级兜底。

    切块策略（滑动窗口）：
    - 每个窗口最多取 `chunk_lines` 行；
    - 相邻窗口之间重叠 `overlap` 行，避免语义在块边界处被硬生生截断；
    - 直到覆盖整个文件为止。

    Args:
        repo: 仓库根目录（用于计算相对路径）。
        path: 待分块的文件绝对路径。
        chunk_lines: 每个 chunk 的最大行数，默认 80。
        overlap: 相邻 chunk 重叠的行数，默认 15（须小于 chunk_lines，否则会死循环/倒退）。

    Returns:
        list[CodeChunk]: 切出的代码块列表。文本块没有「符号」概念，
            故 `symbol` 保持默认 None，`embedding_text` 改用「文件名 + 行号区间」定位。
    """
    text = path.read_text(encoding="utf-8", errors="ignore")
    relative = path.relative_to(repo).as_posix()
    # 取后缀作为 language 标记（去掉点号，如 ".py"→"py"）；无后缀则回退 "text"。
    language = path.suffix.lstrip(".").lower() or "text"
    lines = text.splitlines()

    start = 0  # 当前窗口起始行索引（0-based）。
    chunks: list[CodeChunk] = []
    while start < len(lines):
        # 窗口结束索引 = 起始 + chunk_lines，但不超过文件总行数。
        end = min(start + chunk_lines, len(lines))
        content = "\n".join(lines[start:end])
        # 对外记录的行号是 1-based（start_line 含、end_line 不含右边界之外），
        # 与 chunk_python_file 里 lineno/end_lineno 的 1-based 约定保持一致。
        start_line = start + 1
        end_line = end
        chunks.append(
            CodeChunk(
                id=make_chunk_id(relative, start_line, end_line),
                file_path=relative,
                language=language,
                start_line=start_line,
                end_line=end_line,
                content=content,
                # embedding_text 带上「文件名 + 行号区间」作为定位上下文，
                # 让 embedding 模型知道这段文本来自哪个文件的哪几行。
                embedding_text=(
                    f"File: {relative}\n"
                    f"Lines: {start_line}-{end_line}"
                    f"\n\n{content}"
                ),
            )
        )

        # 已切到文件末尾，退出循环。
        if end >= len(lines):
            break

        # 下一窗口起点 = 当前窗口末尾向前回退 overlap 行，形成重叠。
        start = end - overlap

    return chunks

IGNORED_DIRS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
    ".idea",
    ".vscode",
    ".devpilot",
}


IGNORED_FILES = {
    ".env",
    ".env.local",
    ".env.production",
}

def chunk_repository(repo_path:str)->list[CodeChunk]:
    """! @brief 遍历仓库并把可支持的源码文件汇总为代码块。

    @param repo_path 仓库根目录。
    @return 所有成功读取并切分的 CodeChunk；单个文件失败不会中断全仓索引。
    """
    repo=Path(repo_path).resolve()
    chunks:list[CodeChunk]=[]
    for path in repo.rglob("*"):
        if not path.is_file():
            continue
        # path.parts 包含完整目录层级；任一级命中忽略目录都跳过，避免把
        # .git、虚拟环境、构建产物及旧索引再次送进 embedding 模型。
        if any(part in IGNORED_DIRS for part in path.parts):
            continue
        if path.name in IGNORED_FILES:
            continue
        if (path.suffix.lower() not in SUPPORTED_EXTENSIONS):
            continue
        try:
            if (path.suffix.lower()==".py"):
                file_chunks=(chunk_python_file(repo,path))
            else:
                file_chunks=(chunk_text_file(repo,path))
        except Exception:
            # 索引面向整个仓库：单个文件编码异常或临时不可读时选择跳过，
            # 保留其它文件的检索能力。需要诊断时应在上层增加日志记录。
            continue
        chunks.extend(file_chunks)
    return chunks
