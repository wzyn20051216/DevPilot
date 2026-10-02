#运行测试
# 整个 test_tool.py 现在只做一件事：把"跑 pytest"这件事委托给
# src/sandbox/docker_runner.py，在 Docker 容器里跑，避免 agent 接触宿主机。
# 历史说明：之前这文件里有个 _find_project_python() 是为了在宿主机直接
# 调本机 venv 里的 python；现在改成 sandbox 之后这个函数已经不再被调用
# （保留只是因为你的代码要求"不要动我的代码"，删它属于改动逻辑）。
import shutil
import shlex
import subprocess
from pathlib import Path
from typing import Any
from ..sandbox.docker_runner import run_in_sandbox

def _find_project_python(
    repo: Path,
) -> Path | None:
    """寻找目标项目自己的 Python。"""

    candidates = [
        repo / ".venv" / "Scripts" / "python.exe",
        repo / ".venv" / "bin" / "python",
    ]

    for candidate in candidates:
        if candidate.exists():
            return candidate

    return None

def run_tests(
    repo_path: str,
    target: str | None = None,
    targets: list[str] | None = None,
    timeout: int = 120,
) -> dict[str, Any]:
    """在 Docker Sandbox 中运行 pytest。"""

    if target and targets:
        raise ValueError("target 与 targets 不能同时传入")

    argv = [
        "python",
        "-m",
        "pytest",
        "-q",

        # 防止 pytest 往仓库写 .pytest_cache
        "-p",
        "no:cacheprovider",
    ]

    if target:
        # 模型常按 pytest CLI 习惯传入 ``file.py -k "expr"``。使用 shlex
        # 拆为 argv 后仍由 Docker 的 shell=False 执行，不引入命令注入。
        argv.extend(shlex.split(target))
    elif targets:
        argv.extend(targets)

    result = run_in_sandbox(
        repo_path=repo_path,
        argv=argv,
        timeout=timeout,
    )

    return {
        **result,
        "passed": (
            result["returncode"] == 0
            and not result["timed_out"]
        ),
        "sandboxed": True,
    }
