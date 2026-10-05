"""! @brief 基于 SWE-bench Lite 开源 Issue 的真实仓库评测。"""

import argparse
import hashlib
import io
import json
import random
import shutil
import subprocess
import tarfile
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter, sleep
from typing import Any
from uuid import uuid4

from ..agents.orchestrator import DevPilotOrchestrator
from ..agents.single_developer_agent import SingleDeveloperAgent
from ..agents.strategy import AgentStrategy, decide_strategy
from ..config import settings
from ..sandbox.docker_runner import SandboxProfile, use_sandbox_profile
from ..tools.test_tool import run_tests
from ..tools.write_tool import use_syntax_guard, use_write_guard
from .dataset import PROJECT_ROOT
from .pytest_output import strip_ansi_codes
from .runner import VARIANTS, collect_event_metrics, collect_timing, collect_usage

# 使用 /rows 分页端点而非 first-rows：后者只返回首屏行，无法覆盖 dev split
# 全部 23 个实例，会导致 --pool 无法选满 20 个分层样本。
DATASET_API = "https://datasets-server.huggingface.co/rows"
DEFAULT_INSTANCES = (
    "marshmallow-code__marshmallow-1359",
    "pydicom__pydicom-1139",
    "pylint-dev__astroid-1268",
)
# 从官方 datasets-server 拉取的 SWE-bench_Lite dev split 全部 23 个真实实例。
# 保留 DEFAULT_INSTANCES 不变以兼容旧行为；本常量供 --pool 分层采样使用。
REAL_INSTANCE_POOL: tuple[str, ...] = (
    # marshmallow-code/marshmallow
    "marshmallow-code__marshmallow-1343",
    "marshmallow-code__marshmallow-1359",
    # pvlib/pvlib-python
    "pvlib__pvlib-python-1072",
    "pvlib__pvlib-python-1154",
    "pvlib__pvlib-python-1606",
    "pvlib__pvlib-python-1707",
    "pvlib__pvlib-python-1854",
    # pydicom/pydicom
    "pydicom__pydicom-1139",
    "pydicom__pydicom-1256",
    "pydicom__pydicom-1413",
    "pydicom__pydicom-1694",
    "pydicom__pydicom-901",
    # pylint-dev/astroid
    "pylint-dev__astroid-1196",
    "pylint-dev__astroid-1268",
    "pylint-dev__astroid-1333",
    "pylint-dev__astroid-1866",
    "pylint-dev__astroid-1978",
    # pyvista/pyvista
    "pyvista__pyvista-4315",
    # sqlfluff/sqlfluff
    "sqlfluff__sqlfluff-1517",
    "sqlfluff__sqlfluff-1625",
    "sqlfluff__sqlfluff-1733",
    "sqlfluff__sqlfluff-1763",
    "sqlfluff__sqlfluff-2419",
)
# 真实评测可用的数据集。Lite dev 是已经用于开发调参的 23 题；Verified test
# 是经人工筛选的 500 题，作为未参与调参的新题来源。
DATASETS: dict[str, tuple[str, str]] = {
    "lite": ("SWE-bench/SWE-bench_Lite", "dev"),
    "verified": ("SWE-bench/SWE-bench_Verified", "test"),
}
# 这些仓库的官方测试不是 pytest 节点（Django 用 runtests.py，SymPy 用 bin/test），
# 当前 verifier 与 Agent 的 run_test 都只支持 pytest，抽样时可用 --pytest-only 排除。
NON_PYTEST_REPOS = frozenset({"django/django", "sympy/sympy"})
# 真实评测额外支持增强策略变体；合成 runner 的 VARIANTS 保持不变。
REAL_VARIANTS: tuple[str, ...] = (*VARIANTS, "single_enhanced", "single_adaptive")
REAL_EVAL_ROOT = PROJECT_ROOT / "data" / "real_world_evals"
DATASET_CACHE_ROOT = PROJECT_ROOT / "data" / "swebench_datasets"
REPOSITORY_CACHE_ROOT = PROJECT_ROOT / "data" / "repository_cache"
PROTECTED_PATH_PREFIXES = ("tests/", "test/")
PROTECTED_CONFIG_NAMES = {
    "pytest.ini",
    "tox.ini",
    "setup.cfg",
    "pyproject.toml",
    "conftest.py",
}


@dataclass(frozen=True)
class SweBenchInstance:
    """SWE-bench 运行所需且不会向 Agent 暴露测试补丁的数据。"""

    instance_id: str
    repo: str
    base_commit: str
    problem_statement: str
    gold_patch: str
    test_patch: str
    fail_to_pass: tuple[str, ...]
    pass_to_pass: tuple[str, ...]

    @property
    def image(self) -> str:
        """! @brief 返回 SWE-bench 官方发布的实例镜像名。"""

        normalized = self.instance_id.lower().replace("__", "_1776_")
        return f"swebench/sweb.eval.x86_64.{normalized}:latest"


def _sandbox_profile(instance: SweBenchInstance) -> SandboxProfile:
    """! @brief 使用实例自带的 testbed Conda 环境执行测试。"""

    return SandboxProfile(
        image=instance.image,
        # 不用 `conda activate testbed`：部分官方镜像（sqlfluff/pyvista）的
        # conda CLI 与 base python 版本不匹配，`conda` 命令本身抛
        # `TypeError: 'module' object is not callable`，导致激活失败、测试跑不起来。
        # 直接改 PATH 指向 testbed 环境，绕过 conda CLI，对全部镜像通用
        # （实测 pvlib/sqlfluff/pyvista 均能正确拿到 testbed 的 python+pytest）。
        command_prefix=(
            "export PATH=/opt/miniconda3/envs/testbed/bin:$PATH",
            "export PYTHONDONTWRITEBYTECODE=1",
            # 沙箱只读挂载 /testbed，根文件系统也只读；把测试框架常写的目录
            # 重定向到 tmpfs。否则 hypothesis 写 /testbed/.hypothesis 失败发出
            # 警告，被 astropy 的 filterwarnings=error 升级为收集错误
            # （astropy-13579 实测）；pydicom 写 /root/.pydicom 同理。
            "export HOME=/tmp",
            "export XDG_CACHE_HOME=/tmp/.cache",
            "export MPLCONFIGDIR=/tmp/.matplotlib",
            "export HYPOTHESIS_STORAGE_DIRECTORY=/tmp/.hypothesis",
        ),
        mount_target="/testbed",
    )


def load_swebench_lite_dev() -> dict[str, SweBenchInstance]:
    """! @brief 从官方 Hugging Face 数据服务分页读取 Lite dev split 全部实例。"""

    return load_swebench("lite")


def _test_nodes(value: Any) -> tuple[str, ...]:
    """! @brief 解析 FAIL_TO_PASS/PASS_TO_PASS；部分数据集版本存为 JSON 字符串。"""

    if not value:
        return ()
    if isinstance(value, str):
        value = json.loads(value)
    return tuple(str(item) for item in value)


def _fetch_rows(query: str) -> dict[str, Any]:
    """! @brief 带重试地读取一页数据集；网络被镜像下载占满时握手会偶发超时。"""

    for attempt in range(4):
        try:
            with urllib.request.urlopen(f"{DATASET_API}?{query}", timeout=60) as response:
                return json.load(response)
        except OSError:
            if attempt == 3:
                raise
            sleep(5 * (attempt + 1))
    raise AssertionError("unreachable")


def load_swebench(dataset_key: str = "lite") -> dict[str, SweBenchInstance]:
    """! @brief 分页读取 DATASETS 中指定数据集与 split 的全部实例。

    首次读取后把原始行缓存到 DATASET_CACHE_ROOT，之后评测复用同一份字节，
    保证可复现，也避免每次运行都依赖外网。
    """

    dataset_name, split = DATASETS[dataset_key]
    cache_path = DATASET_CACHE_ROOT / f"{dataset_key}.json"
    if cache_path.exists():
        raw_rows = json.loads(cache_path.read_text(encoding="utf-8"))
        return {
            instance.instance_id: instance
            for instance in (_instance_from_row(row) for row in raw_rows)
        }
    instances: dict[str, SweBenchInstance] = {}
    raw_rows: list[dict[str, Any]] = []
    offset = 0
    length = 100
    while True:
        query = urllib.parse.urlencode(
            {
                "dataset": dataset_name,
                "config": "default",
                "split": split,
                "offset": offset,
                "length": length,
            }
        )
        payload = _fetch_rows(query)

        rows = payload.get("rows") or []
        for wrapped in rows:
            row = wrapped["row"]
            raw_rows.append(row)
            instance = _instance_from_row(row)
            instances[instance.instance_id] = instance
        total = payload.get("num_rows_total")
        # 没有更多行，或已拉满官方声明的总行数时停止；total 缺失时退回仅按空行判断。
        if not rows or (total is not None and len(instances) >= int(total)):
            break
        offset += length
    DATASET_CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(raw_rows, ensure_ascii=False), encoding="utf-8")
    return instances


def _instance_from_row(row: dict[str, Any]) -> SweBenchInstance:
    """! @brief 把数据集原始行转换为评测所需字段。"""

    return SweBenchInstance(
        instance_id=str(row["instance_id"]),
        repo=str(row["repo"]),
        base_commit=str(row["base_commit"]),
        problem_statement=str(row["problem_statement"]),
        gold_patch=str(row["patch"]),
        test_patch=str(row["test_patch"]),
        fail_to_pass=_test_nodes(row.get("FAIL_TO_PASS")),
        pass_to_pass=_test_nodes(row.get("PASS_TO_PASS")),
    )


def select_stratified_instances(
    dataset: dict[str, SweBenchInstance],
    count: int,
    seed: int = 0,
) -> list[str]:
    """! @brief 按 repo 分层、组内确定性轮转，选取尽量多仓库的实例子集。

    组间用 round-robin 轮流各取一个实例，确保小样本下每个 repo 都有机会被
    覆盖；组内先按 instance_id 排序，再用 ``random.Random(seed)`` 偏移每组
    的轮转起点，从而在可复现的前提下用不同 seed 得到不同组合。

    @param dataset 以 instance_id 为键的完整实例字典。
    @param count 期望选取的实例数，超过数据集规模时截断。
    @param seed 组内起点偏移的随机种子，相同 seed 必得相同结果。
    @return 恰好 ``min(count, len(dataset))`` 个 instance_id。
    """

    if count <= 0:
        return []
    # 先按 repo 分组并在组内排序，消除字典遍历顺序带来的非确定性。
    grouped: dict[str, list[str]] = {}
    for instance_id, instance in dataset.items():
        grouped.setdefault(instance.repo, []).append(instance_id)
    for ids in grouped.values():
        ids.sort()

    rng = random.Random(seed)
    # 每组按 seed 决定的偏移量轮转队列，起点不同即得到不同采样组合。
    queues: list[list[str]] = []
    for repo in sorted(grouped):
        ids = grouped[repo]
        offset = rng.randrange(len(ids))
        queues.append(ids[offset:] + ids[:offset])

    limit = min(count, len(dataset))
    selected: list[str] = []
    while len(selected) < limit:
        progressed = False
        for queue in queues:
            if len(selected) >= limit:
                break
            if not queue:
                continue
            selected.append(queue.pop(0))
            progressed = True
        # 所有组都被取空时退出，防止空转死循环。
        if not progressed:
            break
    return selected


def _run_git(
    arguments: list[str],
    cwd: Path | None = None,
    input_text: str | None = None,
    timeout: int = 300,
) -> str:
    """! @brief 执行固定参数的 Git 命令并返回 stdout。"""

    # stdin 必须走字节流：Windows 上 text=True 的 TextIOWrapper 会把 \n 翻译
    # 成 \r\n，导致 git apply 收到 CRLF 补丁，在 LF 工作树上整体失配
    # （marshmallow-1359/sqlfluff-1763 校准实测失败）。字节流保证补丁与
    # 官方 Linux 评测环境逐字节一致。
    input_bytes = input_text.encode("utf-8") if input_text is not None else None
    result = subprocess.run(
        ["git", *arguments],
        cwd=cwd,
        input=input_bytes,
        check=True,
        capture_output=True,
        timeout=timeout,
    )
    return result.stdout.decode("utf-8", errors="replace")


def _cached_repository(instance: SweBenchInstance) -> Path:
    """! @brief 创建或刷新只读 bare mirror，避免重复下载同一真实仓库。"""

    cache = REPOSITORY_CACHE_ROOT / f"{instance.repo.replace('/', '__')}.git"
    cache.parent.mkdir(parents=True, exist_ok=True)
    if not cache.exists():
        # 大仓库（pyvista/sqlfluff）mirror 克隆在普通宽带下远超 5 分钟，
        # 首次克隆放宽容忍 45 分钟；失败残留目录必须清理后重试。
        try:
            _run_git(
                ["clone", "--mirror", f"https://github.com/{instance.repo}.git", str(cache)],
                timeout=2700,
            )
        except subprocess.TimeoutExpired:
            shutil.rmtree(cache, ignore_errors=True)
            raise
    else:
        _run_git(["fetch", "--prune", "origin"], cwd=cache, timeout=900)
    return cache


def create_real_workspace(
    instance: SweBenchInstance,
    run_id: str,
    variant: str,
) -> Path:
    """! @brief 在固定 base commit 创建与其它 variant 隔离的真实仓库。"""

    destination = REAL_EVAL_ROOT / run_id / "workspaces" / instance.instance_id / variant
    if destination.exists():
        shutil.rmtree(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    cache = _cached_repository(instance)
    # --no-checkout：clone 不写工作树（默认分支的 checkout 是浪费的，紧接着
    # 就要切到 base_commit）。对大仓库（pydicom 541 文件）省掉一次完整工作树
    # 重写，把「clone + checkout」从 300 秒超时降到约 20 秒。
    _run_git(["clone", "--no-hardlinks", "--no-checkout", str(cache), str(destination)])
    # 深层实验目录在 Windows 可能超过 MAX_PATH；仅对独立工作区启用，
    # 且必须先于首次 checkout/reset，避免裁判因文件缺失误判候选补丁。
    _run_git(["config", "core.longpaths", "true"], cwd=destination)
    # 工作区不需要 remote：删除继承自 bare mirror 的 origin（指向 GitHub），
    # 避免测试框架或 Git 钩子隐式触发 fetch，在沙箱禁网下退出 128
    # （pallets-flask-5014 r2 实测）。工作区所需的全部提交已经在本地。
    _run_git(["remote", "remove", "origin"], cwd=destination)
    # Windows 全局 core.autocrlf=true 会把工作树写成 CRLF，官方 test_patch
    # （LF）在校准阶段的 git apply 直接失败（sqlfluff-1763 实测；其余仓库因
    # .gitattributes 屏蔽换行转换而幸免）。评测工作树必须与 blob 字节一致：
    # clone 后、checkout 前本地关闭换行转换。
    _run_git(["config", "core.autocrlf", "false"], cwd=destination)
    _run_git(["config", "core.eol", "lf"], cwd=destination)
    _run_git(["checkout", "--detach", instance.base_commit], cwd=destination)
    # 曾在 marshmallow-1359 观察到 checkout 后工作树与 index 不一致，
    # 根因尚未独立证实。reset --hard 强制对齐，再校验工作区完全干净，
    # 避免把残缺工作树中的伪删除当成 Agent 修改。
    _run_git(["reset", "--hard"], cwd=destination)
    residual = _run_git(["status", "--porcelain"], cwd=destination).strip()
    if residual:
        raise RuntimeError(
            "真实评测工作区在创建后不干净，已终止本次评测: "
            + residual[:500]
        )
    _overlay_build_artifacts(instance, destination)
    return destination


# 只复制被 git 忽略的构建产物（编译出的 .so、生成的 _version.py、egg-info 等），
# 排除字节码缓存与顶层 build/ 中间文件。
_ARTIFACT_EXPORT_SCRIPT = (
    "cd /testbed && git ls-files -z --others --ignored --exclude-standard"
    r" | grep -zv -e '__pycache__' -e '^build/' -e '\.pyc$'"
    " | tar --null --no-recursion -T - -cf -"
)


def _overlay_build_artifacts(instance: SweBenchInstance, destination: Path) -> int:
    """! @brief 把官方镜像 /testbed 中被 git 忽略的构建产物覆盖到工作区。

    工作区挂载到 /testbed 时会盖住镜像里已编译好的源码树。astropy、
    matplotlib、scikit-learn 等含 C 扩展或生成文件的仓库因此在导入阶段就失败
    （astropy-13579 实测：缺少 _version.py 与 17 个 .so）。这些文件被 git
    忽略，不会进入候选补丁 diff。

    @return 实际写入的文件数；镜像不可用时返回 0，交由后续校准判定。
    """

    completed = subprocess.run(
        [
            "docker", "run", "--rm", "--network", "none",
            "--entrypoint", "bash", instance.image, "-c", _ARTIFACT_EXPORT_SCRIPT,
        ],
        capture_output=True,
        timeout=600,
        check=False,
    )
    if completed.returncode != 0 or not completed.stdout:
        return 0
    root = destination.resolve()
    written = 0
    with tarfile.open(fileobj=io.BytesIO(completed.stdout)) as archive:
        for member in archive.getmembers():
            # 只落地普通文件，拒绝链接与越界路径，保持与 resolve_safe_path 同样的边界。
            if not member.isfile():
                continue
            target = (root / member.name).resolve()
            if not target.is_relative_to(root):
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is None:
                continue
            target.write_bytes(source.read())
            written += 1
    return written


def _apply_patch(workspace: Path, patch: str) -> None:
    """! @brief 通过 stdin 应用补丁，避免 shell 转义和临时文件泄漏。"""

    _run_git(["apply", "--whitespace=nowarn", "-"], cwd=workspace, input_text=patch)


def _candidate_paths(workspace: Path) -> list[str]:
    """! @brief 返回候选相对 HEAD 修改或新建的文件路径。

    新文件先标记为 intent-to-add，使后续 ``git diff --binary HEAD`` 能生成
    可应用的补丁；评测 Workspace 是一次性的，因此这项 index 变更不会污染
    用户仓库。
    """

    modified = _run_git(["diff", "--name-only", "HEAD"], cwd=workspace).splitlines()
    untracked = _run_git(
        ["ls-files", "--others", "--exclude-standard"], cwd=workspace
    ).splitlines()
    if untracked:
        _run_git(["add", "--intent-to-add", "--", *untracked], cwd=workspace)
    return sorted(
        {
            line.strip().replace("\\", "/")
            for line in (*modified, *untracked)
            if line.strip()
        }
    )


def _protected_candidate_paths(paths: list[str]) -> list[str]:
    """! @brief 找出会影响裁判测试或测试配置的候选路径。"""

    protected: list[str] = []
    for path in paths:
        normalized = path.replace("\\", "/")
        candidate = Path(normalized)
        name = candidate.name.lower()
        parts = {part.lower() for part in candidate.parts}
        if (
            normalized.startswith(PROTECTED_PATH_PREFIXES)
            or {"test", "tests"}.intersection(parts)
            or name.startswith("test_")
            or name.endswith("_test.py")
            or name in PROTECTED_CONFIG_NAMES
        ):
            protected.append(path)
    return protected


def _verification_targets(
    instance: SweBenchInstance,
    excluded: tuple[str, ...] | list[str] = (),
) -> list[str]:
    """! @brief 返回 SWE-bench 指定的失败转通过与回归测试节点。"""

    excluded_set = set(excluded)
    return [
        target
        for target in dict.fromkeys((*instance.fail_to_pass, *instance.pass_to_pass))
        if target not in excluded_set
    ]


def _verification_files(instance: SweBenchInstance) -> list[str]:
    """! @brief 返回官方目标节点所在测试文件，兼容不完整参数化节点名。"""

    return list(
        dict.fromkeys(target.split("::", 1)[0] for target in _verification_targets(instance))
    )


def _normalize_pytest_node(node: str) -> str:
    """! @brief 归一化 pytest 节点名到匹配粒度，用于与 FAIL_TO_PASS 比对。

    参数化测试（如 ``test_foo[param]``）在失败摘要里会带上 ``[...]`` 后缀，而
    SWE-bench 的 FAIL_TO_PASS 存的是不带后缀的 ``file::Class::test_foo``。
    此函数去掉后缀以匹配基线节点名；同时处理换行截断导致的不完整后缀
    ``test_foo[``（astroid-1866 实测）。
    """

    return node.split("[", 1)[0]


def _failed_pytest_nodes(result: dict[str, Any], normalize: bool = True) -> list[str]:
    """! @brief 从 pytest 终端摘要提取失败或错误节点。

    @param normalize 是否归一化节点名（去掉参数化后缀）；用于与 FAIL_TO_PASS
                     比对时必须归一化，用于报告失败列表时保留原始名称。
    """

    nodes: list[str] = []
    for line in strip_ansi_codes(str(result.get("stdout", ""))).splitlines():
        for prefix in ("FAILED ", "ERROR "):
            if line.startswith(prefix):
                node = line.removeprefix(prefix).split(" - ", 1)[0].strip()
                nodes.append(_normalize_pytest_node(node) if normalize else node)
                break
    return nodes


def _run_verification_targets(
    instance: SweBenchInstance,
    workspace: Path,
    targets: tuple[str, ...] | list[str],
) -> dict[str, Any]:
    """! @brief 在官方实例环境中一次运行指定 pytest 节点。"""

    with use_sandbox_profile(_sandbox_profile(instance)):
        # 部分仓库使用 -rN 关闭短摘要；裁判需要明确节点名才能校准。
        # 显式覆盖摘要和颜色选项，两组使用完全相同的测试命令。
        return run_tests(
            str(workspace), targets=["--color=no", "-r", "fE", *targets], timeout=300,
        )


def verify_real_patch(
    instance: SweBenchInstance,
    candidate_patch: str,
    run_id: str,
    label: str,
    excluded_targets: tuple[str, ...] | list[str] = (),
) -> dict[str, Any]:
    """! @brief 在干净 base commit 上应用候选补丁和隐藏测试补丁后验收。"""

    verifier = create_real_workspace(instance, run_id, f"{label}-verifier")
    if candidate_patch.strip():
        _apply_patch(verifier, candidate_patch)
    _apply_patch(verifier, instance.test_patch)
    # 即使某些节点经校准确认为环境失败，也保持它们在原顺序中执行，避免
    # 老项目测试通过全局状态互相影响；只在最终判分时忽略这些已知失败。
    targets = _verification_files(instance)
    result = _run_verification_targets(instance, verifier, targets)
    failed_nodes_normalized = _failed_pytest_nodes(result, normalize=True)
    failed_nodes_raw = _failed_pytest_nodes(result, normalize=False)
    unexpected_failures = set(failed_nodes_normalized).difference(excluded_targets)
    calibrated_pass = bool(result["passed"]) or (
        # 只容忍已知测试失败（pytest exit=1）；收集/内部错误不是有效验收。
        result.get("returncode") == 1
        and bool(failed_nodes_normalized)
        and not unexpected_failures
        and not bool(result.get("timed_out"))
    )
    return {
        "passed": bool(targets) and calibrated_pass,
        "targets": targets,
        "ignored_environment_failures": [
            node for node in failed_nodes_normalized if node in set(excluded_targets)
        ],
        "unexpected_failures": sorted(
            raw for raw, norm in zip(failed_nodes_raw, failed_nodes_normalized)
            if norm in unexpected_failures
        ),
        "results": [result],
    }


def audit_real_instance(instance: SweBenchInstance, run_id: str) -> dict[str, Any]:
    """! @brief 确认隐藏测试在错误 base commit 上确实失败。"""

    verifier = create_real_workspace(instance, run_id, "baseline-verifier")
    _apply_patch(verifier, instance.test_patch)
    baseline = _run_verification_targets(instance, verifier, _verification_files(instance))
    baseline_failures = _failed_pytest_nodes(baseline)
    gold_workspace = create_real_workspace(instance, run_id, "gold-verifier")
    _apply_patch(gold_workspace, instance.gold_patch)
    _apply_patch(gold_workspace, instance.test_patch)
    gold = _run_verification_targets(
        instance,
        gold_workspace,
        _verification_files(instance),
    )
    gold_failures = _failed_pytest_nodes(gold)
    # SWE-bench 的 FAIL_TO_PASS/PASS_TO_PASS 节点名可能自带参数化后缀（如
    # ``test_safe_create_replace_file[utf8_create]``），甚至被换行截断成
    # ``...[\n``（astroid-1866 实测）。比较前统一归一化掉 ``[`` 后缀，否则
    # issubset 匹配不上，参数化实例被误判为「基线未失败」。
    fail_to_pass_normalized = {_normalize_pytest_node(n) for n in instance.fail_to_pass}
    pass_to_pass_normalized = {_normalize_pytest_node(n) for n in instance.pass_to_pass}
    baseline_failures_set = set(baseline_failures)
    gold_unexpected = set(gold_failures).intersection(fail_to_pass_normalized)
    gold_passed = bool(gold["passed"]) or (
        # 收集失败不能因「未出现 FAIL_TO_PASS 节点」被误当作参考修复通过。
        gold.get("returncode") == 1
        and bool(gold_failures)
        and not gold_unexpected
        and not bool(gold.get("timed_out"))
    )
    fail_to_pass_failed = fail_to_pass_normalized.issubset(baseline_failures_set)
    pass_to_pass_failures = [
        node for node in baseline_failures if node in pass_to_pass_normalized
    ]
    return {
        "instance_id": instance.instance_id,
        "baseline_failed": fail_to_pass_failed and gold_passed,
        "fail_to_pass_failed": fail_to_pass_failed,
        "pass_to_pass_passed": not pass_to_pass_failures,
        "unstable_pass_to_pass": gold_failures,
        "gold_passed": gold_passed,
        "gold_unexpected_failures": sorted(gold_unexpected),
        "targets": _verification_files(instance),
        "baseline_result": {
            "returncode": baseline.get("returncode"),
            "timed_out": baseline.get("timed_out", False),
            "stdout": str(baseline.get("stdout", ""))[-4_000:],
            "stderr": str(baseline.get("stderr", ""))[-4_000:],
        },
        "gold_result": {
            "returncode": gold.get("returncode"),
            "timed_out": gold.get("timed_out", False),
            "stdout": str(gold.get("stdout", ""))[-4_000:],
            "stderr": str(gold.get("stderr", ""))[-4_000:],
        },
    }


def _run_agent(
    instance: SweBenchInstance,
    variant: str,
    workspace: Path,
    strategy: AgentStrategy | None = None,
):
    enable_rag = variant in {"single_rag", "multi_rag"}
    enhanced = variant == "single_enhanced"
    with use_sandbox_profile(_sandbox_profile(instance)), use_write_guard(
        lambda path: bool(_protected_candidate_paths([path]))
    ), use_syntax_guard(enhanced or strategy is not None):
        if variant.startswith("single_"):
            return list(
                SingleDeveloperAgent(
                    str(workspace),
                    enable_rag=enable_rag,
                    enhanced=enhanced,
                    strategy=strategy,
                ).run_stream(instance.problem_statement)
            )
        return list(
            DevPilotOrchestrator(str(workspace), enable_rag=enable_rag).run_stream(
                instance.problem_statement
            )
        )


def _repeat_suffix(variant: str, repeat_index: int) -> str:
    """! @brief 计算 variant 在指定重复轮次下的目录/文件名后缀。

    第 1 轮沿用历史命名保持向后兼容，后续轮次追加 ``__r{n}`` 以避免
    workspace、trace、patch 产物互相覆盖。
    """

    return variant if repeat_index == 1 else f"{variant}__r{repeat_index}"


def evaluate_real_instance(
    instance: SweBenchInstance,
    variant: str,
    run_id: str,
    excluded_targets: tuple[str, ...] | list[str] = (),
    repeat_index: int = 1,
) -> dict[str, Any]:
    """! @brief 运行一组真实 Issue，并用隐藏测试补丁独立验收。

    @param repeat_index 同一 instance/variant 的重复实验序号；第 1 轮保持
        原有命名，后续轮次在 workspace/trace/patch 名称上追加 ``__r{n}``
        后缀，避免不同轮次互相覆盖工作区与证据文件。
    """

    suffix = _repeat_suffix(variant, repeat_index)
    workspace = create_real_workspace(instance, run_id, suffix)
    strategy = (
        decide_strategy(workspace, instance.problem_statement)
        if variant == "single_adaptive"
        else None
    )
    started = perf_counter()
    events = []
    execution_exception: str | None = None
    try:
        events = _run_agent(instance, variant, workspace, strategy=strategy)
    except Exception as exc:  # noqa: BLE001
        execution_exception = f"{type(exc).__name__}: {exc}"
    elapsed = perf_counter() - started
    trace_path = REAL_EVAL_ROOT / run_id / "traces" / f"{instance.instance_id}__{suffix}.jsonl"
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    trace_path.write_text(
        "".join(
            json.dumps(event.model_dump(mode="json"), ensure_ascii=False) + "\n"
            for event in events
        ),
        encoding="utf-8",
    )
    candidate_patch = ""
    protected_paths: list[str] = []
    verification: dict[str, Any] = {
        "passed": False,
        "targets": _verification_files(instance),
        "ignored_environment_failures": [],
        "unexpected_failures": [],
        "results": [],
    }
    verification_exception: str | None = None
    try:
        changed_paths = _candidate_paths(workspace)
        protected_paths = _protected_candidate_paths(changed_paths)
        source_paths = [path for path in changed_paths if path not in protected_paths]
        candidate_patch = (
            _run_git(["diff", "--binary", "HEAD", "--", *source_paths], cwd=workspace)
            if source_paths
            else ""
        )
        verification = verify_real_patch(
            instance,
            candidate_patch,
            run_id,
            suffix,
            excluded_targets=excluded_targets,
        )
    except Exception as exc:  # noqa: BLE001
        verification_exception = f"Verifier {type(exc).__name__}: {exc}"
    patch_path = REAL_EVAL_ROOT / run_id / "patches" / f"{instance.instance_id}__{suffix}.patch"
    patch_path.parent.mkdir(parents=True, exist_ok=True)
    patch_path.write_text(candidate_patch, encoding="utf-8", newline="\n")
    tool_calls, iterations, repair_rounds = collect_event_metrics(events)
    prompt_tokens, completion_tokens, total_tokens = collect_usage(events)
    llm_seconds, tool_seconds = collect_timing(events)
    event_errors = list(
        dict.fromkeys(event.message for event in events if event.type == "error")
    )
    if execution_exception:
        event_errors.append(execution_exception)
    if verification_exception:
        event_errors.append(verification_exception)
    if not candidate_patch.strip():
        event_errors.append("Agent 未生成候选补丁")
    workflow_compliance = next(
        (
            event.data.get("workflow")
            for event in reversed(events)
            if isinstance(event.data, dict) and event.data.get("workflow") is not None
        ),
        None,
    )
    return {
        "instance_id": instance.instance_id,
        "repo": instance.repo,
        "base_commit": instance.base_commit,
        "source_pr_url": (
            "https://github.com/"
            + instance.repo
            + "/pull/"
            + instance.instance_id.rsplit("-", 1)[-1]
        ),
        "variant": variant,
        "strategy": asdict(strategy) if strategy is not None else None,
        "workflow_compliance": workflow_compliance,
        "repeat_index": repeat_index,
        "success": bool(verification["passed"]) and not event_errors,
        "tests_passed": bool(verification["passed"]),
        "elapsed_seconds": round(elapsed, 6),
        "llm_seconds": round(llm_seconds, 6),
        "tool_seconds": round(tool_seconds, 6),
        "tool_calls": tool_calls,
        "iterations": iterations,
        "repair_rounds": repair_rounds,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "patch_path": str(patch_path),
        "trace_path": str(trace_path),
        "patch_bytes": len(candidate_patch.encode("utf-8")),
        "patch_sha256": hashlib.sha256(candidate_patch.encode("utf-8")).hexdigest(),
        "excluded_candidate_paths": protected_paths,
        "excluded_unstable_tests": list(excluded_targets),
        "verification_targets": verification["targets"],
        "verification": verification,
        "error": " | ".join(event_errors) or None,
    }


def _build_repeat_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """! @brief 按 (instance_id, variant) 聚合重复运行的 mean/std。

    对成功率、成本和 Adaptive 工作流合规性做描述统计，作为 report.json
    顶层的纯加法键，不改动 rows 的逐条结构。
    """

    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault((row["instance_id"], row["variant"]), []).append(row)

    def mean(values: list[float]) -> float:
        return sum(values) / len(values) if values else 0.0

    def std(values: list[float]) -> float:
        if len(values) < 2:
            return 0.0
        center = mean(values)
        return (sum((value - center) ** 2 for value in values) / len(values)) ** 0.5

    summary: dict[str, Any] = {}
    for (instance_id, variant), group in grouped.items():
        success_rate = [1.0 if row["success"] else 0.0 for row in group]
        total_tokens = [float(row["total_tokens"]) for row in group]
        elapsed_seconds = [float(row["elapsed_seconds"]) for row in group]
        workflow_rows = [
            row["workflow_compliance"]
            for row in group
            if isinstance(row.get("workflow_compliance"), dict)
        ]
        workflow_compliant = [
            1.0 if item.get("compliant") else 0.0 for item in workflow_rows
        ]
        denied_writes = sum(
            int(item.get("progress", {}).get("denied_writes", 0) or 0)
            for item in workflow_rows
        )
        rejected_finals = sum(
            int(item.get("progress", {}).get("rejected_finals", 0) or 0)
            for item in workflow_rows
        )
        summary[f"{instance_id}__{variant}"] = {
            "instance_id": instance_id,
            "variant": variant,
            "runs": len(group),
            "success_rate": {"mean": mean(success_rate), "std": std(success_rate)},
            "total_tokens": {"mean": mean(total_tokens), "std": std(total_tokens)},
            "elapsed_seconds": {
                "mean": mean(elapsed_seconds),
                "std": std(elapsed_seconds),
            },
            "workflow_compliance": {
                "reported_runs": len(workflow_rows),
                "rate": mean(workflow_compliant),
                "denied_writes": denied_writes,
                "rejected_finals": rejected_finals,
            },
        }
    return summary


def run_real_world_evaluation(
    instance_ids: tuple[str, ...] = DEFAULT_INSTANCES,
    variants: tuple[str, ...] = ("single_no_rag", "single_rag"),
    repeats: int = 1,
    dataset_key: str = "lite",
) -> Path:
    """! @brief 审计并运行真实仓库实验矩阵，逐条持久化防止中断丢失。

    @param repeats 每个 instance/variant 的独立重复次数，必须 >= 1。
    @param dataset_key DATASETS 中的数据集键，如 lite 或 verified。
    """

    if repeats < 1:
        raise ValueError("repeats 必须 >= 1")
    unknown = set(variants).difference(REAL_VARIANTS)
    if unknown:
        raise ValueError("未知 variant: " + ", ".join(sorted(unknown)))
    dataset = (
        load_swebench_lite_dev() if dataset_key == "lite" else load_swebench(dataset_key)
    )
    instances = [dataset[instance_id] for instance_id in instance_ids]
    run_id = uuid4().hex
    run_dir = REAL_EVAL_ROOT / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    audits = [audit_real_instance(instance, run_id) for instance in instances]
    rows: list[dict[str, Any]] = []
    report_path = run_dir / "report.json"

    def write_report(status: str) -> None:
        """将当前审计和已完成行持久化为可诊断报告。"""

        report_path.write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "created_at": datetime.now(UTC).isoformat(),
                    "dataset": ":".join(DATASETS[dataset_key]),
                    "status": status,
                    "model": settings.llm_model,
                    "agent_config": {
                        "reasoning_effort": settings.llm_reasoning_effort,
                        "token_budget": settings.agent_token_budget,
                        "tool_observation_max_chars": (
                            settings.tool_observation_max_chars
                        ),
                        "recent_messages": settings.agent_recent_messages,
                        "history_summary_max_chars": (
                            settings.agent_history_summary_max_chars
                        ),
                        "strategy_guarded_max_iterations": (
                            settings.strategy_guarded_max_iterations
                        ),
                    },
                    "repeats": repeats,
                    "summary": _build_repeat_summary(rows),
                    "audits": audits,
                    "rows": rows,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    invalid = [audit["instance_id"] for audit in audits if not audit["baseline_failed"]]
    if invalid:
        # 环境无效的实例（如 pvlib 官方镜像用 NumPy 2.x，历史代码访问已删除
        # 的 np.Inf，conftest 导入即崩）不能拿去考 Agent，但也不应让它们
        # 终止整批评测。如实记为 skipped_invalid_environment 并跳过，
        # 其余有效实例继续跑。这与 docs/current-evaluation.md 对 pvlib-1707
        # 的 invalid_environment 标记一致。
        skipped = {audit["instance_id"]: audit for audit in audits if not audit["baseline_failed"]}
        write_report("partial_invalid_environment")
        print(
            "跳过环境无效实例（不进入成功率分母）: "
            + ", ".join(sorted(skipped))
        )

    write_report("running")
    for instance in instances:
        audit = next(item for item in audits if item["instance_id"] == instance.instance_id)
        if not audit["baseline_failed"]:
            continue
        for variant in variants:
            for repeat_index in range(1, repeats + 1):
                row = evaluate_real_instance(
                    instance,
                    variant,
                    run_id,
                    excluded_targets=audit["unstable_pass_to_pass"],
                    repeat_index=repeat_index,
                )
                rows.append(row)
                write_report("running")
                print(
                    f"{instance.instance_id} {variant} r{repeat_index}: "
                    f"success={row['success']} tests={row['tests_passed']}"
                )
    write_report("completed")
    return report_path


def main() -> None:
    """! @brief 命令行入口。"""

    parser = argparse.ArgumentParser(description="运行 SWE-bench 小规模真实评测")
    parser.add_argument("--instance", action="append", dest="instances")
    parser.add_argument("--variant", action="append", choices=REAL_VARIANTS)
    parser.add_argument(
        "--dataset",
        choices=sorted(DATASETS),
        default="lite",
        help="lite=Lite dev（已用于调参）；verified=Verified test（新题）",
    )
    parser.add_argument("--seed", type=int, default=0, help="--pool 分层抽样种子")
    parser.add_argument(
        "--pytest-only",
        action="store_true",
        help="--pool 抽样前排除测试不是 pytest 节点的仓库（见 NON_PYTEST_REPOS）",
    )
    parser.add_argument(
        "--repeats",
        type=int,
        default=1,
        help="每个 instance/variant 的重复次数，默认 1",
    )
    parser.add_argument(
        "--pool",
        type=int,
        default=None,
        help="从 dev split 分层选取 N 个实例；默认不传使用 DEFAULT_INSTANCES",
    )
    args = parser.parse_args()
    if args.pool is not None:
        # --pool 显式传入时，从真实 dev split 分层采样，覆盖尽量多的仓库。
        pool_dataset = load_swebench(args.dataset)
        if args.pytest_only:
            pool_dataset = {
                key: value
                for key, value in pool_dataset.items()
                if value.repo not in NON_PYTEST_REPOS
            }
        instance_ids = tuple(
            select_stratified_instances(pool_dataset, args.pool, seed=args.seed)
        )
    else:
        instance_ids = tuple(args.instances or DEFAULT_INSTANCES)
    output = run_real_world_evaluation(
        instance_ids=instance_ids,
        variants=tuple(args.variant or ("single_no_rag", "single_rag")),
        repeats=args.repeats,
        dataset_key=args.dataset,
    )
    print(output)


if __name__ == "__main__":
    main()
