"""! @brief 第十四关 Benchmark 执行器。

一条用例会在四份相互独立的 Workspace 中运行：单/多 Agent 分别开启或
关闭 RAG。Agent 完成后再由独立 verifier 运行测试，避免采用 Agent 自己的
“测试通过”陈述作为成功依据。
"""

import argparse
import json
import os
import platform
import shutil
import subprocess
from collections.abc import Iterable
from datetime import UTC, datetime
from fnmatch import fnmatch
from hashlib import sha256
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import uuid4

from backend.src.agents.orchestrator import DevPilotOrchestrator
from backend.src.agents.single_developer_agent import SingleDeveloperAgent
from backend.src.config import settings
from backend.src.database.connection import init_database
from backend.src.database.evaluation_repository import evaluation_repository
from backend.src.models.agent_state import AgentEvent
from backend.src.rag.embedder import MODEL_NAME
from backend.src.tools.test_tool import run_tests

from .dataset import (
    PROJECT_ROOT,
    calculate_dataset_fingerprint,
    get_fixture_path,
    load_benchmark_cases,
    load_case,
)
from .metrics import summarize_results
from .models import BenchmarkCase, EvaluationResult, EvaluationVariant

EVAL_WORKSPACE_ROOT = PROJECT_ROOT / "artifacts" / "eval_workspaces"
EXPERIMENT_ROOT = PROJECT_ROOT / "artifacts" / "experiments"
REPOSITORY_ROOT = PROJECT_ROOT.parent
VARIANTS: tuple[EvaluationVariant, ...] = (
    "single_no_rag",
    "single_rag",
    "multi_no_rag",
    "multi_rag",
)

# 正式实验使用固定消融设计，不受生产环境动态路由开关影响。将设计随配置
# 一起保存，避免报告只写 variant 名称却没有说明实际 Agent/RAG 策略。
VARIANT_DESIGN: dict[EvaluationVariant, dict[str, object]] = {
    "single_no_rag": {
        "architecture": "single_agent",
        "rag_enabled": False,
        "strategy": "static_baseline",
    },
    "single_rag": {
        "architecture": "single_agent",
        "rag_enabled": True,
        "strategy": "static_baseline",
    },
    "multi_no_rag": {
        "architecture": "multi_agent",
        "rag_enabled": False,
        "strategy": "planner_coder_tester_reviewer",
    },
    "multi_rag": {
        "architecture": "multi_agent",
        "rag_enabled": True,
        "strategy": "planner_coder_tester_reviewer",
    },
}

IMPLEMENTATION_PATHS = (
    Path("backend/src"),
    Path("research/evals"),
    Path("pyproject.toml"),
    Path("uv.lock"),
    Path("backend/Dockerfile"),
    Path("backend/docker/sandbox.Dockerfile"),
)


def calculate_implementation_fingerprint() -> str:
    """! @brief 计算本轮被测实现与运行环境定义的稳定源码指纹。"""

    digest = sha256()
    files: list[Path] = []
    for relative in IMPLEMENTATION_PATHS:
        path = REPOSITORY_ROOT / relative
        if path.is_file():
            files.append(path)
        elif path.is_dir():
            files.extend(item for item in path.rglob("*") if item.is_file())

    for path in sorted(files):
        relative = path.relative_to(REPOSITORY_ROOT)
        if any(part in {"__pycache__", ".pytest_cache"} for part in relative.parts):
            continue
        if path.suffix in {".pyc", ".pyo"}:
            continue
        digest.update(relative.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def collect_git_provenance() -> dict[str, object]:
    """! @brief 记录提交和相关源码脏状态，保证实验结果可以追溯。"""

    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPOSITORY_ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        status = subprocess.run(
            [
                "git",
                "status",
                "--porcelain",
                "--untracked-files=all",
                "--",
                *(path.as_posix() for path in IMPLEMENTATION_PATHS),
            ],
            cwd=REPOSITORY_ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
    except (OSError, subprocess.SubprocessError):
        return {"git_commit": None, "git_dirty": None, "dirty_paths": []}

    return {
        "git_commit": commit,
        "git_dirty": bool(status),
        "dirty_paths": status,
    }


def _initialize_git_repository(workspace: Path) -> None:
    """! @brief 为复制出的 Benchmark Workspace 创建可比较的 Git 基线。

    Fixture 在主仓库中以普通源码保存，不能携带嵌套 `.git` 目录。每次实验
    都在独立 Workspace 中初始化仓库并提交原始错误版本，Agent 修改后的
    `git diff` 因而始终相对于相同基线计算。

    @param workspace 已复制完成的实验工作区。
    """

    commands = (
        ("init", "--quiet"),
        ("add", "."),
        (
            "-c",
            "user.name=DevPilot Benchmark",
            "-c",
            "user.email=benchmark@devpilot.local",
            "commit",
            "--quiet",
            "-m",
            "Initialize benchmark fixture",
        ),
    )
    for arguments in commands:
        _ = subprocess.run(
            ["git", *arguments],
            cwd=workspace,
            check=True,
            capture_output=True,
            text=True,
        )


def create_workspace(
    case: BenchmarkCase,
    variant: EvaluationVariant,
    run_id: str,
    repeat_index: int = 1,
) -> Path:
    """! @brief 从只读 fixture 创建本次实验的独立 Git Workspace。"""

    source = get_fixture_path(case)
    # 重复实验也必须使用独立目录。复用 case/variant 目录不仅会覆盖上一轮
    # 证据，Windows 上还可能因 Git 对象仍被进程占用而无法删除。
    destination = (
        EVAL_WORKSPACE_ROOT
        / run_id
        / f"repeat-{repeat_index}"
        / case.id
        / variant
    )
    if destination.exists():
        shutil.rmtree(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        source,
        destination,
        ignore=shutil.ignore_patterns(
            ".git*",
            "__pycache__",
            ".pytest_cache",
            ".devpilot",
        ),
    )
    _initialize_git_repository(destination)
    return destination


def collect_event_metrics(events: Iterable[AgentEvent]) -> tuple[int, int, int]:
    """! @brief 从统一事件流提取工具调用、LLM 迭代和返工次数。

    多 Agent 的 iteration 会在每个角色内从 1 重新开始，所以不能简单取最大值。
    每个 thinking 事件对应一次真实 LLM 请求，计数后才是整条链路的总迭代数。
    """

    event_list = list(events)
    tool_calls = sum(event.type == "tool_call" for event in event_list)
    iterations = sum(event.type == "thinking" for event in event_list)
    repair_rounds = max(
        (
            int(event.data.get("repair_rounds", 0) or 0)
            for event in event_list
            if isinstance(event.data, dict)
        ),
        default=0,
    )
    return tool_calls, iterations, repair_rounds


def collect_usage(events: Iterable[AgentEvent]) -> tuple[int, int, int]:
    """! @brief 汇总各 Agent 调用链末尾上报的 Token 用量。

    BaseToolAgent 每次执行只在 final/error 终止事件中放一份累计 usage；只读取
    这些事件可避免把同一个 Agent 的多轮累计值重复相加。
    """

    prompt_tokens = 0
    completion_tokens = 0
    total_tokens = 0
    for event in events:
        if event.type not in {"final", "error", "cancelled"}:
            continue
        usage = event.data.get("usage") if isinstance(event.data, dict) else None
        if not isinstance(usage, dict):
            continue
        prompt_tokens += int(usage.get("prompt_tokens", 0) or 0)
        completion_tokens += int(usage.get("completion_tokens", 0) or 0)
        total_tokens += int(usage.get("total_tokens", 0) or 0)
    return prompt_tokens, completion_tokens, total_tokens


def collect_cache_usage(events: Iterable[AgentEvent]) -> tuple[int, int]:
    """! @brief 汇总各 Agent 终止事件中的 Prompt Cache 命中/未命中 Token。

    与 collect_usage 相同，只读取 final/error/cancelled 终止事件里带出的累计值，
    避免把同一个 Agent 的多轮累计重复相加。供应商不支持该字段时保持 0。
    """

    hit_tokens = 0
    miss_tokens = 0
    for event in events:
        if event.type not in {"final", "error", "cancelled"}:
            continue
        usage = event.data.get("usage") if isinstance(event.data, dict) else None
        if not isinstance(usage, dict):
            continue
        hit_tokens += int(usage.get("prompt_cache_hit_tokens", 0) or 0)
        miss_tokens += int(usage.get("prompt_cache_miss_tokens", 0) or 0)
    return hit_tokens, miss_tokens


def collect_timing(events: Iterable[AgentEvent]) -> tuple[float, float]:
    """! @brief 汇总各 Agent 终止事件中的模型与工具耗时。"""

    llm_seconds = 0.0
    tool_seconds = 0.0
    for event in events:
        if event.type not in {"final", "error", "cancelled"}:
            continue
        timing = event.data.get("timing") if isinstance(event.data, dict) else None
        if not isinstance(timing, dict):
            continue
        llm_seconds += float(timing.get("llm_seconds", 0.0) or 0.0)
        tool_seconds += float(timing.get("tool_seconds", 0.0) or 0.0)
    return llm_seconds, tool_seconds


VERIFIER_PROTECTED_PATTERNS = (
    "tests/**",
    "test_*.py",
    "**/test_*.py",
    "**/tests/**",
    "conftest.py",
    "**/conftest.py",
    "pytest.ini",
    "pyproject.toml",
    "setup.cfg",
    "tox.ini",
)


def _changed_paths(workspace: Path) -> tuple[list[str], list[str]]:
    """! @brief 返回相对 HEAD 的新增/修改路径和删除路径。"""

    modified = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=AM", "HEAD"],
        cwd=workspace,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        cwd=workspace,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    deleted = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=D", "HEAD"],
        cwd=workspace,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    return sorted(set(modified + untracked)), sorted(set(deleted))


def _is_verifier_protected(path: str, case: BenchmarkCase) -> bool:
    """! @brief 判断路径是否会影响独立裁判本身。"""

    normalized = path.replace("\\", "/")
    target = (case.verification_target or "").replace("\\", "/")
    if target and (normalized == target or normalized.startswith(target.rstrip("/") + "/")):
        return True
    return any(fnmatch(normalized, pattern) for pattern in VERIFIER_PROTECTED_PATTERNS)


def prepare_verification_workspace(case: BenchmarkCase, workspace: Path) -> Path:
    """! @brief 把候选源码应用到一份带原始测试的干净裁判工作区。

    Agent 可以查看公开测试，但其测试和测试配置改动不会进入最终验收。
    """

    changed, deleted = _changed_paths(workspace)
    forbidden = [path for path in changed if _is_verifier_protected(path, case)]
    forbidden.extend(deleted)
    if forbidden:
        raise ValueError("候选修改触及裁判保护路径: " + ", ".join(sorted(set(forbidden))))

    destination = workspace.parent / f"{workspace.name}-verifier"
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(
        get_fixture_path(case),
        destination,
        ignore=shutil.ignore_patterns(".git*", "__pycache__", ".pytest_cache", ".devpilot"),
    )
    for relative in changed:
        source_path = workspace / relative
        target_path = destination / relative
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, target_path)
    return destination


def verify_case(case: BenchmarkCase, workspace: Path) -> dict[str, Any]:
    """! @brief 在隔离的干净副本中运行固定测试，得到客观验收结果。"""

    try:
        verification_workspace = prepare_verification_workspace(case, workspace)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        return {
            "passed": False,
            "returncode": None,
            "timed_out": False,
            "stdout": "",
            "stderr": str(exc),
            "sandboxed": True,
        }

    return run_tests(
        repo_path=str(verification_workspace),
        target=case.verification_target,
        timeout=case.timeout_seconds,
        command=case.verification_command,
    )


def _run_variant(
    case: BenchmarkCase,
    variant: EvaluationVariant,
    workspace: Path,
) -> list[AgentEvent]:
    """! @brief 根据 variant 构造对应 Agent 并完整消费事件流。"""

    # 不能使用 endswith("_rag")：single_no_rag / multi_no_rag 也满足该条件。
    enable_rag = variant in {"single_rag", "multi_rag"}
    if variant.startswith("single_"):
        agent = SingleDeveloperAgent(
            repo_path=str(workspace),
            enable_rag=enable_rag,
        )
        return list(agent.run_stream(case.task))

    orchestrator = DevPilotOrchestrator(
        repo_path=str(workspace),
        enable_rag=enable_rag,
    )
    # Benchmark 不需要人工审批计划，直接跑完整 Planner -> Reviewer 链路；
    # 这样 Planner 的迭代和 Token 也会被纳入同一次实验统计。
    return list(orchestrator.run_stream(case.task))


def _event_error(events: Iterable[AgentEvent]) -> str | None:
    """! @brief 合并事件流中的错误消息，同时去掉重复文本。"""

    messages = list(
        dict.fromkeys(event.message for event in events if event.type == "error" and event.message)
    )
    return " | ".join(messages) or None


def evaluate_case(
    case: BenchmarkCase,
    variant: EvaluationVariant,
    run_id: str | None = None,
    repeat_index: int = 1,
    persist: bool = True,
) -> EvaluationResult:
    """! @brief 在独立 Workspace 中执行并验证一个 case/variant。

    @param case Benchmark 输入。
    @param variant 单/多 Agent 与 RAG 开关组合。
    @param run_id 批次 ID；省略时自动生成。
    @param repeat_index 同一 case/variant 的重复实验序号。
    @param persist 是否写入 evaluation_results，测试时可关闭。
    @return 包含正确性、效率、耗时和 Token 的 EvaluationResult。
    """

    actual_run_id = run_id or uuid4().hex
    workspace = create_workspace(
        case,
        variant,
        actual_run_id,
        repeat_index=repeat_index,
    )
    events: list[AgentEvent] = []
    execution_error: str | None = None
    verification: dict[str, Any] = {"passed": False}
    started_at = perf_counter()

    try:
        events = _run_variant(case, variant, workspace)
        execution_error = _event_error(events)
        # 无论 Agent 是否正确结束，都让独立测试裁判检查最终文件状态。
        # 这能区分“协议输出失败但代码已修好”和“代码确实仍然错误”。
        verification = verify_case(case, workspace)
    # Benchmark 必须把任意 Agent、Sandbox 或 verifier 故障记录为一条失败样本，
    # 否则批次会中断并产生幸存者偏差，因此这里有意捕获执行边界的所有异常。
    except Exception as exc:  # noqa: BLE001
        execution_error = f"{type(exc).__name__}: {exc}"

    elapsed_seconds = perf_counter() - started_at
    tool_calls, iterations, repair_rounds = collect_event_metrics(events)
    prompt_tokens, completion_tokens, total_tokens = collect_usage(events)
    llm_seconds, tool_seconds = collect_timing(events)
    estimated_cost = (
        prompt_tokens * settings.llm_prompt_cost_per_million
        + completion_tokens * settings.llm_completion_cost_per_million
    ) / 1_000_000

    if not verification.get("passed") and not execution_error:
        stderr = str(verification.get("stderr", "")).strip()
        stdout = str(verification.get("stdout", "")).strip()
        execution_error = stderr or stdout or "独立验证未通过"

    result = EvaluationResult(
        run_id=actual_run_id,
        case_id=case.id,
        variant=variant,
        repeat_index=repeat_index,
        # success 比 tests_passed 更严格：代码测试通过且 Agent/编排协议没有
        # error 事件才算本次架构完整成功，二者分开后论文分析更准确。
        success=bool(verification.get("passed")) and execution_error is None,
        tests_passed=bool(verification.get("passed")),
        tool_calls=tool_calls,
        iterations=iterations,
        repair_rounds=repair_rounds,
        elapsed_seconds=round(elapsed_seconds, 6),
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        llm_seconds=round(llm_seconds, 6),
        tool_seconds=round(tool_seconds, 6),
        estimated_cost=round(estimated_cost, 8),
        workspace_path=str(workspace),
        error=execution_error,
    )

    if persist:
        init_database()
        evaluation_repository.save_result(result)
    return result


def run_benchmark_case(
    case_id: str,
    variants: Iterable[EvaluationVariant] = VARIANTS,
) -> list[EvaluationResult]:
    """! @brief 对同一用例运行给定 variants，并共享同一个 run_id。"""

    case = load_case(case_id)
    run_id = uuid4().hex
    return [evaluate_case(case, variant, run_id=run_id) for variant in variants]


def save_experiment_config(
    run_id: str,
    repeats: int,
    cases: list[BenchmarkCase],
    variants: tuple[EvaluationVariant, ...] = VARIANTS,
) -> Path:
    """! @brief 保存可复现实验所需的模型、参数和数据集快照。"""

    experiment_dir = EXPERIMENT_ROOT / run_id
    experiment_dir.mkdir(parents=True, exist_ok=False)
    config_path = experiment_dir / "config.json"
    provenance = collect_git_provenance()
    payload = {
        "run_id": run_id,
        "created_at": datetime.now(UTC).isoformat(),
        "model": settings.llm_model,
        "temperature": 0.2,
        "random_seed": 42,
        "repeats": repeats,
        "max_single_iterations": 14,
        "max_coder_iterations": 8,
        "max_repair_rounds": 2,
        "agent_token_budget": settings.agent_token_budget,
        "tool_observation_max_chars": settings.tool_observation_max_chars,
        "agent_recent_messages": settings.agent_recent_messages,
        "agent_history_summary_max_chars": settings.agent_history_summary_max_chars,
        "benchmark_cases": len(cases),
        "case_ids": [case.id for case in cases],
        "dataset_sha256": calculate_dataset_fingerprint(),
        "implementation_sha256": calculate_implementation_fingerprint(),
        **provenance,
        "variants": list(variants),
        "variant_design": {
            variant: VARIANT_DESIGN[variant]
            for variant in variants
        },
        "experiment_strategy": "fixed_ablation",
        "production_defaults": {
            "execution_mode": "multi_rag",
            "rag_mode": settings.rag_mode,
            "strategy_router_enabled": settings.strategy_router_enabled,
        },
        "compute_environment": {
            name: os.environ.get(name)
            for name in (
                "OPENBLAS_NUM_THREADS",
                "OMP_NUM_THREADS",
                "MKL_NUM_THREADS",
                "TOKENIZERS_PARALLELISM",
            )
        },
        "verification": {
            "truth_source": "independent_sandbox_verifier",
            "workspace_policy": "fresh_fixture_plus_candidate_source_overlay",
            "protected_patterns": list(VERIFIER_PROTECTED_PATTERNS),
            "success_rule": "tests_passed_and_no_agent_error",
        },
        "rag": "BM25 + Embedding + RRF",
        "embedding_model": MODEL_NAME,
        "python_version": platform.python_version(),
        "platform": platform.platform(),
    }
    config_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return config_path


def run_full_experiment(
    repeats: int | None = 3,
    run_id: str | None = None,
    case_ids: Iterable[str] | None = None,
    variants: Iterable[EvaluationVariant] | None = None,
) -> str:
    """! @brief 运行选定用例、variant 和重复次数的正式实验。

    全部结果共享一个 run_id。`evaluate_case` 已负责逐条持久化，本函数只负责编排，
    不重复写库。即使进程中途停止，已完成结果和实验配置仍会保留。

    @param repeats 每个 case/variant 的独立重复次数。
    @param run_id 可选已有批次 ID；传入后跳过已持久化组合并断点续跑。
    @param case_ids 可选用例 ID；省略时运行全部用例。
    @param variants 可选实验变体；省略时运行全部四组。
    @return 可用于 Dashboard 和统计分析的实验 run_id。
    """

    actual_run_id = run_id or uuid4().hex
    config_path = EXPERIMENT_ROOT / actual_run_id / "config.json"
    if run_id is not None:
        if not config_path.is_file():
            raise FileNotFoundError(f"续跑实验配置不存在：{config_path}")
        config = json.loads(config_path.read_text(encoding="utf-8"))
        configured_case_ids = list(config["case_ids"])
        configured_variants = tuple(config["variants"])
        configured_repeats = int(config["repeats"])

        # 续跑默认完全沿用首次保存的实验设计。显式传入不同筛选条件时立即
        # 报错，防止同一个 run_id 混入不可比较的样本。
        if case_ids is not None and list(dict.fromkeys(case_ids)) != configured_case_ids:
            raise ValueError("续跑的 case_ids 与原实验配置不一致")
        if variants is not None and tuple(dict.fromkeys(variants)) != configured_variants:
            raise ValueError("续跑的 variants 与原实验配置不一致")
        if repeats is not None and repeats != configured_repeats:
            raise ValueError("续跑的 repeats 与原实验配置不一致")
        selected_case_ids = configured_case_ids
        selected_variants = configured_variants
        actual_repeats = configured_repeats
    else:
        selected_case_ids = list(dict.fromkeys(case_ids)) if case_ids is not None else None
        selected_variants = (
            tuple(dict.fromkeys(variants)) if variants is not None else VARIANTS
        )
        actual_repeats = 3 if repeats is None else repeats

    if actual_repeats <= 0:
        raise ValueError("repeats 必须为正整数")
    if not selected_variants:
        raise ValueError("至少需要一个实验 variant")

    cases = (
        [load_case(case_id) for case_id in selected_case_ids]
        if selected_case_ids is not None
        else load_benchmark_cases()
    )
    if not cases:
        raise ValueError("没有可运行的 Benchmark case")
    if run_id is None:
        config_path = save_experiment_config(
            actual_run_id,
            actual_repeats,
            cases,
            selected_variants,
        )

    # 结果按 (repeat, case, variant) 去重。批次异常退出后再次启动时，已完成
    # 组合不会重复消耗 Token，也不会向 SQLite 写入重复样本。
    init_database()
    completed_keys = {
        (result.repeat_index, result.case_id, result.variant)
        for result in evaluation_repository.get_results(run_id=actual_run_id)
    }
    total = len(cases) * len(selected_variants) * actual_repeats
    completed = len(completed_keys)
    action = "继续" if run_id is not None else "创建"
    print(
        f"实验 {actual_run_id} 已{action}，配置：{config_path}，"
        f"已完成 {completed}/{total}"
    )

    for repeat_index in range(1, actual_repeats + 1):
        for case in cases:
            for variant in selected_variants:
                key = (repeat_index, case.id, variant)
                if key in completed_keys:
                    continue
                completed += 1
                print(
                    f"[{completed}/{total}] repeat={repeat_index} "
                    f"case={case.id} variant={variant}"
                )
                result = evaluate_case(
                    case,
                    variant,
                    run_id=actual_run_id,
                    repeat_index=repeat_index,
                    persist=True,
                )
                print(
                    f"  success={result.success} tests={result.tests_passed} "
                    f"elapsed={result.elapsed_seconds:.2f}s"
                )
                completed_keys.add(key)
    return actual_run_id


def main() -> None:
    """! @brief 命令行入口，支持单用例调试和完整正式实验。"""

    parser = argparse.ArgumentParser(description="运行 DevPilot 正式 Benchmark 实验")
    parser.add_argument("case_id", nargs="?", default="add_bug")
    parser.add_argument(
        "--full",
        action="store_true",
        help="运行全部 Benchmark 的正式重复实验",
    )
    parser.add_argument(
        "--repeats",
        type=int,
        default=None,
        help="--full 模式下每组重复次数，默认 3",
    )
    parser.add_argument(
        "--resume",
        metavar="RUN_ID",
        help="从已有正式实验批次断点续跑，并跳过已完成组合",
    )
    parser.add_argument(
        "--case",
        action="append",
        dest="case_ids",
        help="正式实验只运行指定 case；可重复传入，默认运行全部用例",
    )
    parser.add_argument(
        "--variant",
        choices=VARIANTS,
        action="append",
        dest="variants",
        help="只运行指定 variant；可重复传入，默认运行全部四组",
    )
    args = parser.parse_args()
    if args.full or args.resume:
        run_id = run_full_experiment(
            repeats=args.repeats,
            run_id=args.resume,
            case_ids=args.case_ids,
            variants=args.variants,
        )
        results = evaluation_repository.get_results(run_id=run_id)
    else:
        selected = tuple(args.variants) if args.variants else VARIANTS
        results = run_benchmark_case(args.case_id, selected)
    payload = {
        "results": [result.model_dump(mode="json") for result in results],
        "summary": summarize_results(results),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
