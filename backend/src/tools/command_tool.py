##命令工具 只开放代码质量检查
import shutil#更高级的文件处理
import subprocess
from pathlib import Path
from typing import Any
from ..sandbox.docker_runner import run_in_sandbox
ALLOWED_COMMANDS=[
    "pytest",# 单元测试
    "ruff",# 代码格式化、lint检查
    "mypy"# python静态类型检查
]

def run_command(
    repo_path: str,
    argv: list[str],
    timeout: int = 60,
) -> dict[str, Any]:
    """在 Sandbox 中执行允许的开发命令。"""

    if not argv:
        raise ValueError(
            "argv 不能为空"
        )

    command = argv[0].lower()

    if command not in ALLOWED_COMMANDS:
        raise ValueError(
            f"不允许执行命令: {command}"
        )

    return run_in_sandbox(
        repo_path=repo_path,
        argv=argv,
        timeout=timeout,
    )