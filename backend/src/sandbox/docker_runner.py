import shutil
import subprocess
from pathlib import Path
from subprocess import CompletedProcess
from typing import Any

from ..config import settings

# DevPilot 的工具沙箱：把 agent 的 "run_test" / "run_command" 类工具
# 通过 `docker run` 隔离执行，不让 agent 直接动宿主机。
# 这个文件是给 test_tool.py / command_tool.py 调用的底层能力，
# 本身**不**被 LLM 直接看到，也不会作为工具注册到 LLM。

# 沙箱镜像的 tag，必须和 docker build -t 的名字保持一致。
# Dockerfile 在 backend/docker/sandbox.Dockerfile。
SANDBOX_IMAGE = settings.sandbox_image

# 白名单：sandbox 里只允许跑这几个可执行程序。
# 任意 `argv[0]` 命中白名单才能进容器，否则直接拒绝（防止 agent 用 `rm`/`curl`/`bash` 越权）。
# 想要加新工具？同时改这里和 Dockerfile 的 `pip install` 列表。
ALLOWED_PROGRAMS = {
    "python",
    "pytest",
    "ruff",
    "mypy",
}


def resolve_host_mount_path(repo: Path) -> str:
    """! @brief 把 Backend 容器路径映射为 Docker daemon 可见的宿主机路径。

    本机直接运行 Backend 时 ``HOST_WORKSPACE_ROOT`` 为空，原路径即可挂载。
    Compose 模式下，Backend 看到 ``/workspace/project``，但宿主机 Docker
    daemon 需要 ``E:/desktop/project`` 这样的路径，因此按相对目录重新拼接。

    @param repo Backend 进程可见的绝对仓库路径。
    @return Docker ``--mount source`` 使用的路径字符串。
    @raise ValueError Compose 模式下仓库不属于允许的 workspace root。
    """

    host_root = settings.host_workspace_root
    if host_root is None:
        return str(repo)

    workspace_root = settings.workspace_root.resolve()
    try:
        relative = repo.relative_to(workspace_root)
    except ValueError as exc:
        raise ValueError("Repository 不在允许的 Workspace Root 内") from exc

    # 不对 host_root 调用 resolve()：Linux 容器必须原样保留 E:/... 形式的
    # Windows Docker Desktop 路径，不能把它错误转换成 /app/E:/...。
    return f"{str(host_root).rstrip('/\\\\')}/{relative.as_posix()}"


def run_in_sandbox(
    repo_path: str,
    argv: list[str],
    timeout: int = 120,
) -> dict[str, Any]:
    """在受限 Docker 容器中执行开发命令。

    本函数把 `argv`（例如 ["python", "-m", "pytest", "-q"]）作为入口命令，
    通过 `docker run` 在一个**只读挂载仓库 + 禁网 + 限资源**的容器里跑。
    返回结构化结果：argv / returncode / timed_out / stdout / stderr。
    调用方（test_tool / command_tool）拿到结果后自己决定怎么解读。
    """
    # argv 不能为空：没有可执行程序的话 docker 容器起来也是空转。
    if not argv:
        raise ValueError(f"参数 {argv} 不能为空")

    # 宿主机没装 docker 就直接报错，不要让 agent 拿到模糊的失败信息。
    if shutil.which("docker") is None:
        raise RuntimeError("未找到 docker，请检查是否安装")

    # 把 repo_path 标准化成绝对路径（避免相对路径在不同 cwd 下行为不一致）。
    repo = Path(repo_path).resolve()
    if not repo.exists():
        raise FileNotFoundError(f"{repo} 不存在")

    # -------------------------
    # 白名单校验：argv[0] 必须是 ALLOWED_PROGRAMS 之一
    # 防止 agent 写出 `["rm", "-rf", "/"]` 或 `["bash", "evil.sh"]` 之类
    # -------------------------
    program = argv[0].lower()
    if program not in ALLOWED_PROGRAMS:
        raise ValueError(f"sandbox 不允许执行这个程序：{program}")

    # 把宿主机仓库只读挂载到容器的 /workspace。
    # `readonly` 是关键：agent 通过工具只能**读**代码、**写**文件必须走 write_file 工具
    # （write_file 工具自己有 review 逻辑），不允许它在容器里偷偷改宿主机文件。
    mount_source = resolve_host_mount_path(repo)
    mount_value = (
        f"type=bind,"
        f"source={mount_source},"
        f"target=/workspace,"
        f"readonly"
    )

    # 拼装 docker run 命令：这一长串 flags 是 sandbox 的"安全策略"，
    # 任何一项被去掉都会让 agent 的破坏能力指数级上升，不要轻易删。
    command = [
        "docker",
        "run",

        # 容器退出后立刻删除（不留垃圾镜像/容器）。
        "--rm",

        # 禁网：容器内程序不能访问外网，
        # 防止 agent 把代码上传或下载恶意 payload。
        "--network",
        "none",

        # 内存上限 512m：单次测试任务够用，
        # 防止 agent 写出吃内存的脚本把宿主机拖垮。
        "--memory",
        "512m",

        # CPU 限制 1 核：不至于让 agent 把整个机器卡死。
        "--cpus",
        "1.0",

        # 进程数限制 128：拦 fork bomb 之类的攻击。
        "--pids-limit",
        "128",

        # 容器根文件系统只读：所有写操作只能进 /tmp（下面的 tmpfs）。
        "--read-only",

        # 给程序一个临时的、可写的、不能执行新程序的 /tmp。
        # `noexec` 关键：禁止在 /tmp 里运行下载的可执行文件。
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,size=64m",

        # 把仓库挂进容器（只读，见上）。
        "--mount",
        mount_value,

        # 容器里的工作目录就是 /workspace（也就是上面挂载的位置）。
        "--workdir",
        "/workspace",

        # 镜像名。
        SANDBOX_IMAGE,

        # 真正要执行的命令（剩下的 argv 全部作为参数透传）。
        *argv,
    ]

    # 实际跑 docker run：超时由参数 timeout 控制。
    try:
        result: CompletedProcess[str] = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            shell=False,   # shell=False：不要走 shell 解释，避免命令注入
        )
    except subprocess.TimeoutExpired:
        # 超时也算一种"结果"返回给调用方，不要 raise 出去炸掉 agent 的循环。
        return {
            "argv": argv,
            "returncode": None,
            "timed_out": True,
            "stdout": "",
            "stderr": f"Sandbox 执行超过 {timeout} 秒，已终止",
        }

    # 正常返回。stdout/stderr 截断到 20k/10k 字符，避免一个超长输出
    # 把整个 AgentState 撑爆（state 会被序列化、传到前端等等）。
    return {
        "argv": argv,
        "returncode": result.returncode,
        "timed_out": False,
        "stdout": result.stdout[-20_000:],
        "stderr": result.stderr[-10_000:],
    }
