# 支持「整个仓库 diff」或「指定单文件 diff」，
# 带安全路径校验、输出截断、超时保护，返回结构化字典给上层 Agent 调用。
import subprocess  # 在 Python 里调用操作系统外部命令（git、ls 等）

from pathlib import Path

from .file_tool import resolve_safe_path


def git_diff(repo_path: str, file_path: str | None = None) -> dict[str, object]:
    """查看代码仓库的 Git Diff（改动内容）。

    Args:
        repo_path: 仓库根目录路径。
        file_path: 可选，只查看某个文件的 diff；不传则看整个仓库的 diff。

    Returns:
        dict: 结构化结果，包含：
            - is_git_repo: 是否 Git 仓库（非仓库时 diff 为空、message 说明原因）；
            - returncode: git 命令退出码（0 正常，非 0 异常）；
            - diff: diff 输出文本（截断到 3 万字）；
            - stderr: 错误信息（截断）。
    """
    repo = Path(repo_path).resolve()  # 转成绝对路径

    # 判断是否真实 Git 仓库：目录下存在 .git 文件夹
    if not (repo / ".git").exists():
        return {
            "is_git_repo": False,
            "diff": "",
            "message": "当前目录不是 Git 仓库",
        }

    # 构造基础 git diff 命令
    command: list[str] = [
        "git",
        "diff",
        "--no-ext-diff",   # 不调用外部 diff 工具，只用 git 内置输出
        "--unified=3",     # 上下文各显示 3 行
    ]

    # 传入 file_path 时，只看单个文件的 diff
    if file_path:
        safe_path = resolve_safe_path(repo, file_path)
        relative = safe_path.relative_to(repo)  # git diff 需要仓库根目录下的相对路径
        # "--" 是 git 参数分隔符，防止文件名以 "-" 开头被误解析成命令行参数
        command.extend(["--", str(relative)])

    # 执行 git diff；cwd=repo 表示在仓库目录里执行；shell=False 防注入
    result = subprocess.run(
        command,
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
        shell=False,
    )

    # 返回结构化结果并做截断，防止 diff 文本过大撑爆 LLM 上下文
    return {
        "is_git_repo": True,
        "returncode": result.returncode,
        "diff": result.stdout[:30_000],   # diff 输出最多保留 3 万字符
        "stderr": result.stderr[:5000],   # 错误信息截断到 5000 字符
    }