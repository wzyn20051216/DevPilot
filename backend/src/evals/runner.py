"""! @brief 第十四关 Benchmark 执行器。

一条用例会在四份相互独立的 Workspace 中运行：单/多 Agent 分别开启或
关闭 RAG。Agent 完成后再由独立 verifier 运行测试，避免采用 Agent 自己的
“测试通过”陈述作为成功依据。
"""

import argparse
import json
import platform
import shutil
import subprocess
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import uuid4

from ..agents.orchestrator import DevPilotOrchestrator
from ..agents.single_developer_agent import SingleDeveloperAgent
from ..config import settings
from ..database.connection import init_database
from ..database.evaluation_repository import evaluation_repository
from ..models.agent_state import AgentEvent
from ..rag.embedder import MODEL_NAME
from ..tools.test_tool import run_tests
from .dataset import (
    PROJECT_ROOT,
    calculate_dataset_fingerprint,
    get_fixture_path,
    load_benchmark_cases,
    load_case,
)
from .metrics import summarize_results
from .models import BenchmarkCase, EvaluationResult, EvaluationVariant

EVAL_WORKSPACE_ROOT = PROJECT_ROOT / "data" / "eval_workspaces"
EXPERIMENT_ROOT = PROJECT_ROOT / "data" / "experiments"
VARIANTS: tuple[EvaluationVariant, ...] = (
    "single_no_rag",
    "single_rag",
    "multi_no_rag",
    "multi_rag",
)


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
) -> Path:
    """! @brief 从只读 fixture 创建本次实验的独立 Git Workspace。"""

    source = get_fixture_path(case)
    destination = EVAL_WORKSPACE_ROOT / run_id / case.id / variant
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
        if event.type not in {"final", "error"}:
            continue
        usage = event.data.get("usage") if isinstance(event.data, dict) else None
        if not isinstance(usage, dict):
            continue
        prompt_tokens += int(usage.get("prompt_tokens", 0) or 0)
        completion_tokens += int(usage.get("completion_tokens", 0) or 0)
        total_tokens += int(usage.get("total_tokens", 0) or 0)
    return prompt_tokens, completion_tokens, total_tokens


def verify_case(case: BenchmarkCase, workspace: Path) -> dict[str, Any]:
    """! @brief 在 Agent 流程之外运行固定测试，得到客观验收结果。"""

    return run_tests(
        repo_path=str(workspace),
        target=case.verification_target,
        timeout=case.timeout_seconds,
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
    workspace = create_workspace(case, variant, actual_run_id)
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
) -> Path:
    """! @brief 保存可复现实验所需的模型、参数和数据集快照。"""

    experiment_dir = EXPERIMENT_ROOT / run_id
    experiment_dir.mkdir(parents=True, exist_ok=False)
    config_path = experiment_dir / "config.json"
    payload = {
        "run_id": run_id,
        "created_at": datetime.now(UTC).isoformat(),
        "model": settings.llm_model,
        "temperature": 0.2,
        "random_seed": 42,
        "repeats": repeats,
        "max_single_iterations": 12,
        "max_coder_iterations": 8,
        "max_repair_rounds": 2,
        "benchmark_cases": len(cases),
        "case_ids": [case.id for case in cases],
        "dataset_sha256": calculate_dataset_fingerprint(),
        "variants": list(VARIANTS),
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


def run_full_experiment(repeats: int = 3) -> str:
    """! @brief 运行全部难度、四种 variant 和指定重复次数的正式实验。

    全部结果共享一个 run_id。`evaluate_case` 已负责逐条持久化，本函数只负责编排，
    不重复写库。即使进程中途停止，已完成结果和实验配置仍会保留。

    @param repeats 每个 case/variant 的独立重复次数。
    @return 可用于 Dashboard 和统计分析的实验 run_id。
    """

    if repeats <= 0:
        raise ValueError("repeats 必须为正整数")
    cases = load_benchmark_cases()
    if not cases:
        raise ValueError("没有可运行的 Benchmark case")

    run_id = uuid4().hex
    config_path = save_experiment_config(run_id, repeats, cases)
    total = len(cases) * len(VARIANTS) * repeats
    completed = 0
    print(f"实验 {run_id} 已创建，配置：{config_path}")

    for repeat_index in range(1, repeats + 1):
        for case in cases:
            for variant in VARIANTS:
                completed += 1
                print(
                    f"[{completed}/{total}] repeat={repeat_index} "
                    f"case={case.id} variant={variant}"
                )
                result = evaluate_case(
                    case,
                    variant,
                    run_id=run_id,
                    repeat_index=repeat_index,
                    persist=True,
                )
                print(
                    f"  success={result.success} tests={result.tests_passed} "
                    f"elapsed={result.elapsed_seconds:.2f}s"
                )
    return run_id


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
        default=3,
        help="--full 模式下每组重复次数，默认 3",
    )
    parser.add_argument(
        "--variant",
        choices=VARIANTS,
        action="append",
        dest="variants",
        help="只运行指定 variant；可重复传入，默认运行全部四组",
    )
    args = parser.parse_args()
    if args.full:
        run_id = run_full_experiment(repeats=args.repeats)
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
