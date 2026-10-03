"""! @brief 基于 SWE-bench Lite 开源 Issue 的真实仓库评测。"""

import argparse
import json
import shutil
import subprocess
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import uuid4

from ..agents.orchestrator import DevPilotOrchestrator
from ..agents.single_developer_agent import SingleDeveloperAgent
from ..config import settings
from ..sandbox.docker_runner import SandboxProfile, use_sandbox_profile
from ..tools.test_tool import run_tests
from .dataset import PROJECT_ROOT
from .runner import VARIANTS, collect_event_metrics, collect_timing, collect_usage

DATASET_API = "https://datasets-server.huggingface.co/first-rows"
DEFAULT_INSTANCES = (
    "marshmallow-code__marshmallow-1359",
    "pydicom__pydicom-1139",
    "pylint-dev__astroid-1268",
)
REAL_EVAL_ROOT = PROJECT_ROOT / "data" / "real_world_evals"
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
        command_prefix=(
            "source /opt/miniconda3/bin/activate",
            "conda activate testbed",
            "export PYTHONDONTWRITEBYTECODE=1",
        ),
        mount_target="/testbed",
    )


def load_swebench_lite_dev() -> dict[str, SweBenchInstance]:
    """! @brief 从官方 Hugging Face 数据服务读取 Lite dev split。"""

    query = urllib.parse.urlencode(
        {
            "dataset": "SWE-bench/SWE-bench_Lite",
            "config": "default",
            "split": "dev",
        }
    )
    with urllib.request.urlopen(f"{DATASET_API}?{query}", timeout=30) as response:
        payload = json.load(response)

    instances: dict[str, SweBenchInstance] = {}
    for wrapped in payload["rows"]:
        row = wrapped["row"]
        instance = SweBenchInstance(
            instance_id=str(row["instance_id"]),
            repo=str(row["repo"]),
            base_commit=str(row["base_commit"]),
            problem_statement=str(row["problem_statement"]),
            gold_patch=str(row["patch"]),
            test_patch=str(row["test_patch"]),
            fail_to_pass=tuple(row.get("FAIL_TO_PASS") or ()),
            pass_to_pass=tuple(row.get("PASS_TO_PASS") or ()),
        )
        instances[instance.instance_id] = instance
    return instances


def _run_git(arguments: list[str], cwd: Path | None = None, input_text: str | None = None) -> str:
    """! @brief 执行固定参数的 Git 命令并返回 stdout。"""

    result = subprocess.run(
        ["git", *arguments],
        cwd=cwd,
        input=input_text,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )
    return result.stdout


def _cached_repository(instance: SweBenchInstance) -> Path:
    """! @brief 创建或刷新只读 bare mirror，避免重复下载同一真实仓库。"""

    cache = REPOSITORY_CACHE_ROOT / f"{instance.repo.replace('/', '__')}.git"
    cache.parent.mkdir(parents=True, exist_ok=True)
    if not cache.exists():
        _run_git(["clone", "--mirror", f"https://github.com/{instance.repo}.git", str(cache)])
    else:
        _run_git(["fetch", "--prune", "origin"], cwd=cache)
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
    _run_git(["clone", "--no-hardlinks", str(cache), str(destination)])
    _run_git(["checkout", "--detach", instance.base_commit], cwd=destination)
    return destination


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


def _failed_pytest_nodes(result: dict[str, Any]) -> list[str]:
    """! @brief 从 pytest 终端摘要提取失败或错误节点。"""

    nodes: list[str] = []
    for line in str(result.get("stdout", "")).splitlines():
        for prefix in ("FAILED ", "ERROR "):
            if line.startswith(prefix):
                nodes.append(line.removeprefix(prefix).split(" - ", 1)[0].strip())
                break
    return nodes


def _run_verification_targets(
    instance: SweBenchInstance,
    workspace: Path,
    targets: tuple[str, ...] | list[str],
) -> dict[str, Any]:
    """! @brief 在官方实例环境中一次运行指定 pytest 节点。"""

    with use_sandbox_profile(_sandbox_profile(instance)):
        return run_tests(str(workspace), targets=list(targets), timeout=300)


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
    failed_nodes = _failed_pytest_nodes(result)
    unexpected_failures = set(failed_nodes).difference(excluded_targets)
    calibrated_pass = bool(result["passed"]) or (
        bool(failed_nodes)
        and not unexpected_failures
        and not bool(result.get("timed_out"))
    )
    return {
        "passed": bool(targets) and calibrated_pass,
        "targets": targets,
        "ignored_environment_failures": [
            node for node in failed_nodes if node in set(excluded_targets)
        ],
        "unexpected_failures": sorted(unexpected_failures),
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
    gold_unexpected = set(gold_failures).intersection(instance.fail_to_pass)
    gold_passed = bool(gold["passed"]) or (
        bool(gold_failures)
        and not gold_unexpected
        and not bool(gold.get("timed_out"))
    )
    fail_to_pass_failed = set(instance.fail_to_pass).issubset(baseline_failures)
    pass_to_pass_failures = [
        node for node in baseline_failures if node in set(instance.pass_to_pass)
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


def _run_agent(instance: SweBenchInstance, variant: str, workspace: Path):
    enable_rag = variant in {"single_rag", "multi_rag"}
    with use_sandbox_profile(_sandbox_profile(instance)):
        if variant.startswith("single_"):
            return list(
                SingleDeveloperAgent(str(workspace), enable_rag=enable_rag).run_stream(
                    instance.problem_statement
                )
            )
        return list(
            DevPilotOrchestrator(str(workspace), enable_rag=enable_rag).run_stream(
                instance.problem_statement
            )
        )


def evaluate_real_instance(
    instance: SweBenchInstance,
    variant: str,
    run_id: str,
    excluded_targets: tuple[str, ...] | list[str] = (),
) -> dict[str, Any]:
    """! @brief 运行一组真实 Issue，并用隐藏测试补丁独立验收。"""

    workspace = create_real_workspace(instance, run_id, variant)
    started = perf_counter()
    events = []
    execution_exception: str | None = None
    try:
        events = _run_agent(instance, variant, workspace)
    except Exception as exc:  # noqa: BLE001
        execution_exception = f"{type(exc).__name__}: {exc}"
    elapsed = perf_counter() - started
    trace_path = REAL_EVAL_ROOT / run_id / "traces" / f"{instance.instance_id}__{variant}.jsonl"
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
            variant,
            excluded_targets=excluded_targets,
        )
    except Exception as exc:  # noqa: BLE001
        verification_exception = f"Verifier {type(exc).__name__}: {exc}"
    patch_path = REAL_EVAL_ROOT / run_id / "patches" / f"{instance.instance_id}__{variant}.patch"
    patch_path.parent.mkdir(parents=True, exist_ok=True)
    patch_path.write_text(candidate_patch, encoding="utf-8")
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
        "excluded_candidate_paths": protected_paths,
        "excluded_unstable_tests": list(excluded_targets),
        "verification_targets": verification["targets"],
        "verification": verification,
        "error": " | ".join(event_errors) or None,
    }


def run_real_world_evaluation(
    instance_ids: tuple[str, ...] = DEFAULT_INSTANCES,
    variants: tuple[str, ...] = ("single_no_rag", "single_rag"),
) -> Path:
    """! @brief 审计并运行真实仓库实验矩阵，逐条持久化防止中断丢失。"""

    unknown = set(variants).difference(VARIANTS)
    if unknown:
        raise ValueError("未知 variant: " + ", ".join(sorted(unknown)))
    dataset = load_swebench_lite_dev()
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
                    "dataset": "SWE-bench/SWE-bench_Lite:dev",
                    "status": status,
                    "model": settings.llm_model,
                    "agent_config": {
                        "token_budget": settings.agent_token_budget,
                        "tool_observation_max_chars": (
                            settings.tool_observation_max_chars
                        ),
                        "recent_messages": settings.agent_recent_messages,
                        "history_summary_max_chars": (
                            settings.agent_history_summary_max_chars
                        ),
                    },
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
        write_report("invalid_environment")
        raise RuntimeError(
            "真实用例基线或金补丁校准失败: "
            + ", ".join(invalid)
            + f"；诊断报告: {report_path}"
        )

    write_report("running")
    for instance in instances:
        audit = next(item for item in audits if item["instance_id"] == instance.instance_id)
        for variant in variants:
            row = evaluate_real_instance(
                instance,
                variant,
                run_id,
                excluded_targets=audit["unstable_pass_to_pass"],
            )
            rows.append(row)
            write_report("running")
            print(
                f"{instance.instance_id} {variant}: "
                f"success={row['success']} tests={row['tests_passed']}"
            )
    write_report("completed")
    return report_path


def main() -> None:
    """! @brief 命令行入口。"""

    parser = argparse.ArgumentParser(description="运行 SWE-bench Lite dev 小规模真实评测")
    parser.add_argument("--instance", action="append", dest="instances")
    parser.add_argument("--variant", action="append", choices=VARIANTS)
    args = parser.parse_args()
    output = run_real_world_evaluation(
        instance_ids=tuple(args.instances or DEFAULT_INSTANCES),
        variants=tuple(args.variant or ("single_no_rag", "single_rag")),
    )
    print(output)


if __name__ == "__main__":
    main()
