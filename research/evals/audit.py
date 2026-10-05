"""! @brief Benchmark 数据集结构和初始失败状态审计。"""

import argparse
import json
import os
import subprocess
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

from backend.src.tools.test_tool import run_tests
from .dataset import (
    BENCHMARK_ROOT,
    calculate_dataset_fingerprint,
    get_fixture_path,
    load_benchmark_cases,
)
from .models import BenchmarkCase

DEFAULT_REPORT_PATH = BENCHMARK_ROOT / "audit_report.json"


def audit_case(case: BenchmarkCase, execute: bool = True) -> dict[str, Any]:
    """! @brief 检查一条 case 的文件完整性，并可执行初始失败测试。

    Benchmark fixture 代表 Agent 修改前的错误版本，所以 verifier 初始通过反而
    表示样本无效。这里使用当前 Python 解释器运行项目自带测试，不调用 LLM。

    @param case 待检查的 Benchmark case。
    @param execute 是否运行 fixture 的 pytest。
    @return 包含结构检查和 baseline 退出码的字典。
    """

    fixture = get_fixture_path(case)
    target = fixture / case.verification_target if case.verification_target else fixture
    row: dict[str, Any] = {
        "case_id": case.id,
        "difficulty": case.difficulty,
        "category": case.category,
        "fixture_exists": fixture.is_dir(),
        "verification_target_exists": target.exists(),
        "baseline_failed": None,
        "baseline_exit_code": None,
        "elapsed_seconds": None,
    }
    if not execute or not fixture.is_dir() or not target.exists():
        return row

    started_at = perf_counter()
    if case.verification_command is not None:
        # polyglot 分支：非 Python 用例在沙箱中直接执行 verification_command。
        # 结果解析沿用 run_tests 的 returncode/timed_out/stdout/stderr 结构；
        # 初始失败 = returncode != 0 且未超时（超时属于环境问题而非样本红）。
        result = run_tests(
            repo_path=str(fixture),
            command=case.verification_command,
            timeout=case.timeout_seconds,
        )
        row["baseline_exit_code"] = result["returncode"]
        row["baseline_failed"] = bool(
            result["returncode"] != 0 and not result["timed_out"]
        )
        row["timed_out"] = result["timed_out"]
        # 失败摘要供审计报告定位初始红的具体用例，截断避免撑爆报告。
        row["baseline_stderr"] = str(result["stderr"])[-2000:]
        row["elapsed_seconds"] = round(perf_counter() - started_at, 6)
        return row

    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "-p",
                "no:cacheprovider",
                case.verification_target or ".",
            ],
            cwd=fixture,
            env=environment,
            capture_output=True,
            text=True,
            timeout=case.timeout_seconds,
            check=False,
        )
        row["baseline_exit_code"] = result.returncode
        row["baseline_failed"] = result.returncode != 0
    except subprocess.TimeoutExpired:
        row["baseline_exit_code"] = None
        row["baseline_failed"] = True
        row["timed_out"] = True
    row["elapsed_seconds"] = round(perf_counter() - started_at, 6)
    return row


def build_audit_report(execute: bool = True) -> dict[str, Any]:
    """! @brief 审计完整数据集并生成可序列化报告。"""

    cases = load_benchmark_cases()
    rows = [audit_case(case, execute=execute) for case in cases]
    valid = all(
        row["fixture_exists"]
        and row["verification_target_exists"]
        and (not execute or row["baseline_failed"] is True)
        for row in rows
    )
    return {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "dataset_sha256": calculate_dataset_fingerprint(),
        "case_count": len(cases),
        "difficulty_counts": dict(sorted(Counter(case.difficulty for case in cases).items())),
        "category_counts": dict(sorted(Counter(case.category for case in cases).items())),
        "baseline_executed": execute,
        "valid": valid,
        "cases": rows,
    }


def write_audit_report(
    output: Path = DEFAULT_REPORT_PATH,
    execute: bool = True,
) -> Path:
    """! @brief 生成审计报告并写入 JSON 文件。"""

    report = build_audit_report(execute=execute)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return output


def main() -> None:
    """! @brief 命令行入口。"""

    parser = argparse.ArgumentParser(description="审计 DevPilot Benchmark 数据集")
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument(
        "--structure-only",
        action="store_true",
        help="只检查文件结构，不执行 fixture 测试",
    )
    args = parser.parse_args()
    output = write_audit_report(args.output, execute=not args.structure_only)
    report = json.loads(output.read_text(encoding="utf-8"))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["valid"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
