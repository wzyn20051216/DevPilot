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
from ..config import settings
from ..sandbox.docker_runner import SandboxProfile, run_in_sandbox, use_sandbox_profile

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
    command: list[str] | None = None,
) -> dict[str, Any]:
    """在 Docker Sandbox 中运行测试。

    `command` 为 None 时沿用现有 pytest 路径（一个字符都不变）；
    提供 `command` 时直接透传该完整 argv，并切换到 polyglot 镜像以支持
    Node/Java 等非 Python 运行时（白名单仍会拦截非法 argv[0]）。
    """

    if command is not None:
        # polyglot 分支：非 Python 语言需要 Node/JDK，镜像换成 polyglot。
        # 这里以“command 非 None 即视为非 python 验证”为准，避免让调用方
        # 额外传 language，同时保证旧调用方（command=None）行为零变化。
        with use_sandbox_profile(SandboxProfile(image=settings.sandbox_image_polyglot)):
            result = run_in_sandbox(
                repo_path=repo_path,
                argv=command,
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
