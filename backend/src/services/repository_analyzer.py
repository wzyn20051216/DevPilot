"""代码仓库分析服务：把仓库内容提炼成上下文，交给 LLM 生成分析结论。

整体流程（对应下面注释的 5 步）：
1. 列出仓库所有文件；
2. 判断哪些文件「重要」（入口/配置/README 等）；
3. 只读取重要文件的内容；
4. 拼装成一段结构化的 Context；
5. 把 Context + 用户问题交给 LLM 生成回答。
"""

from pathlib import Path

from ..config import settings
from ..llm_client import create_client
from ..tools.file_tool import list_files, read_files


# 高优先级文件名：命中即视为「重要文件」，优先读进上下文。
IMPORTANT_FILES = {
    "README.md",
    "README_CN.md",
    "pyproject.toml",
    "requirements.txt",
    "package.json",
    "main.py",
    "app.py",
    "config.py",
    "settings.py",
    "docker-compose.yml",
    "Dockerfile",
}


def select_important_files(files: list[str], max_files: int = 8) -> list[str]:
    """从文件列表中筛选出「重要文件」，最多返回 max_files 个。

    分两级优先级：
    1. 文件名命中 IMPORTANT_FILES（README/配置/入口等）；
    2. 根目录下文件名 stem 命中关键字的 .py 文件（main/app/config/settings/agent）。

    Args:
        files: 仓库内的文件路径列表（相对路径）。
        max_files: 最多返回的文件数量。

    Returns:
        筛选出的重要文件路径列表。
    """
    selected: list[str] = []

    # 第一优先级：文件名命中 IMPORTANT_FILES
    for file_path in files:
        path = Path(file_path)
        # path.name 是「文件名+后缀」（不含目录），直接和 IMPORTANT_FILES 比对
        if path.name in IMPORTANT_FILES and len(selected) < max_files:
            selected.append(file_path)
        if len(selected) >= max_files:
            return selected

    # 第二优先级：根目录下的 .py 入口/配置文件
    # path.stem 是「去掉后缀的文件名」，如 main.py -> main
    key_words = {"main", "app", "config", "settings", "agent"}
    for file_path in files:
        if file_path in selected:  # 已选过的不重复
            continue
        path = Path(file_path)
        if (
            path.suffix == ".py"
            and len(selected) < max_files
            and path.stem.lower() in key_words
        ):
            selected.append(file_path)
        if len(selected) >= max_files:
            break

    return selected


def build_repository_context(repo_path: str, max_files: int = 500, max_chars: int = 12000) -> str:
    """构建仓库的上下文信息（文件树 + 重要文件内容）。

    Args:
        repo_path: 仓库路径。
        max_files: 最多列出多少个文件（用于文件树）。
        max_chars: 每个重要文件最多读取多少字符。

    Returns:
        拼装好的上下文字符串，可直接喂给 LLM。
    """
    files = list_files(repo_path, max_files=max_files)
    important_files = select_important_files(files, max_files=8)

    # context_parts 逐段累积，最后用双换行拼起来
    context_parts: list[str] = []

    # 1. 先给模型看「项目有哪些文件」，截前 200 个避免过长
    #    注意：原作者这里写的是 "/n"（字面反斜杠），实际应为换行 "\n"，
    #    否则文件树会挤在一行，影响模型理解。
    file_tree = "/n".join(files[:200])
    context_parts.append(f"Project file tree:\n{file_tree}\n".strip())

    # 2. 再逐个读取重要文件内容
    for file_path in important_files:
        try:
            content: str = read_files(repo_path=repo_path, file_path=file_path, max_chars=max_chars)
        except Exception as exc:
            # 读失败不中断整个流程，用一个占位说明替换
            content = f"[读取失败: {exc}]"

        context_parts.append(
            f"""
        # 文件：{file_path}

        ```text
        {content}""".strip()
        )

    return "\n\n".join(context_parts)


def analyze_repository(repo_path: str, question: str) -> str:
    """分析仓库并生成回答：先建上下文，再调用 LLM。

    Args:
        repo_path: 仓库路径。
        question: 用户问题。

    Returns:
        LLM 生成的分析回答。
    """
    context = build_repository_context(repo_path)
    client = create_client()
    response = client.chat.completions.create(
        model=settings.llm_model,
        messages=[
            {
                "role": "system",
                "content": (
                    "你是 DevPilot 的代码仓库分析专家。"
                    "你的任务是根据真实代码仓库内容，"
                    "分析项目结构、技术栈、入口、核心模块"
                    "以及模块之间的关系。"
                    "不要编造没有出现在上下文中的内容。"
                ),
            },
            {
                "role": "user",
                "content": f"仓库上下文:\n{context}\n\n用户问题:\n{question}",
            },
        ],
        temperature=0.2,
    )

    content = response.choices[0].message.content

    return content or ""
