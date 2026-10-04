"""! @brief 冻结配置、交替执行基线与动态策略的真实仓库配对实验。

在独立源代码快照中运行；不访问业务数据库。逐条落盘，可使用相同参数
续跑。隐藏测试和金补丁只供独立裁判校准，Agent 只接收原始 Issue。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any


def _save(path: Path, payload: dict[str, Any]) -> None:
    """! @brief 原子替换报告，避免进程中断留下半份 JSON。"""

    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _code_hash(root: Path) -> str:
    """! @brief 按相对路径与字节散列实际运行的源代码。"""

    digest = hashlib.sha256()
    paths = sorted((root / "backend" / "src").rglob("*.py"))
    paths.append(Path(__file__).resolve())
    for path in paths:
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _schedule(instances: list[str], repeats: int) -> list[dict[str, Any]]:
    """! @brief 在重复轮次内逐题配对，交替 AB/BA，减少固定顺序偏差。"""

    scheduled = []
    for repeat in range(1, repeats + 1):
        for index, instance in enumerate(instances):
            variants = ["single_no_rag", "single_adaptive"]
            if (repeat - 1 + index) % 2:
                variants.reverse()
            for variant in variants:
                scheduled.append({
                    "instance_id": instance, "variant": variant, "repeat_index": repeat,
                })
    return scheduled


def _is_provider_blocked(row: dict[str, Any]) -> bool:
    """! @brief 识别供应商额度/限流中断，避免计入修复率或跳过续跑。"""

    error = str(row.get("error") or "").casefold()
    return any(term in error for term in (
        "error code: 402", "error code: 429", "insufficient balance",
    ))


def main() -> None:
    """! @brief 校准环境后执行预先登记的配对计划，并保留失败记录。"""

    parser = argparse.ArgumentParser(description="真实模型配对实验（建议从冻结代码快照运行）")
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--instance", action="append", required=True, dest="instances")
    parser.add_argument("--repeats", type=int, default=3)
    # 历史基线提示词与构造器均使用 14 轮，不允许只改动态组造成预算混杂。
    parser.add_argument("--iterations", type=int, default=14, choices=[14])
    parser.add_argument("--token-budget", type=int, default=250_000)
    parser.add_argument("--audit-only", action="store_true")
    args = parser.parse_args()
    if args.repeats < 1 or args.iterations < 10 or args.token_budget < 0:
        parser.error("重复次数须为正数，轮次至少 10，Token 上限不能为负")
    if Path(args.run_id).name != args.run_id or args.run_id in {".", ".."}:
        parser.error("run-id 必须是单个目录名")
    if len(set(args.instances)) != len(args.instances):
        parser.error("instance 不可重复")

    # 在加载依赖模块前注入隔离配置，防止其模块级数据库路径指向业务数据。
    from backend.src.config import Settings, settings

    configured = Settings(_env_file=args.env_file.resolve())
    for name in Settings.model_fields:
        setattr(settings, name, getattr(configured, name))
    data_root = args.data_root.resolve()
    run_root = data_root / "paired_real_world" / args.run_id
    run_root.mkdir(parents=True, exist_ok=True)
    settings.database_backend = "sqlite"
    settings.database_path = run_root / "isolated.db"
    settings.agent_checkpoint_enabled = False
    settings.host_workspace_root = None
    settings.agent_token_budget = args.token_budget
    settings.strategy_guarded_max_iterations = args.iterations
    settings.validate_llm()

    from backend.src.evals import real_world as evaluator

    evaluator.REAL_EVAL_ROOT = data_root / "paired_real_world"
    evaluator.DATASET_CACHE_ROOT = data_root / "swebench_datasets"
    evaluator.REPOSITORY_CACHE_ROOT = data_root / "repository_cache"
    dataset_path = evaluator.DATASET_CACHE_ROOT / "verified.json"
    if not dataset_path.is_file():
        raise RuntimeError("须先缓存 Verified 数据集；配对实验不自动更新数据集")
    dataset = evaluator.load_swebench("verified")
    source_root = Path(__file__).resolve().parents[2]
    identity = {
        "source_sha256": _code_hash(source_root),
        "dataset_sha256": hashlib.sha256(dataset_path.read_bytes()).hexdigest(),
        "instances": args.instances,
        "model": settings.llm_model,
        "temperature": 0.2,
        "reasoning_effort": settings.llm_reasoning_effort,
        "repeats": args.repeats,
        "max_iterations_both": args.iterations,
        "agent_token_budget": args.token_budget,
        "tool_observation_max_chars": settings.tool_observation_max_chars,
        "recent_messages": settings.agent_recent_messages,
        "history_summary_max_chars": settings.agent_history_summary_max_chars,
        "dedupe_observations": settings.agent_dedupe_observations,
        "llm_timeout_seconds": settings.llm_timeout_seconds,
        "llm_max_retries": settings.llm_max_retries,
        "strategy_thresholds": {
            name: getattr(settings, name) for name in (
                "strategy_router_enabled", "strategy_outline_min_lines",
                "strategy_outline_min_repo_files", "strategy_python_min_ratio",
            )
        },
        "image_ids": {
            key: subprocess.check_output(
                ["docker", "image", "inspect", "--format", "{{.Id}}", dataset[key].image],
                text=True,
            ).strip() for key in args.instances
        },
    }
    plan = _schedule(args.instances, args.repeats)
    report_path = run_root / "report.json"
    if report_path.exists():
        report = json.loads(report_path.read_text(encoding="utf-8"))
        if report["identity"] != identity or report["schedule"] != plan:
            raise RuntimeError("代码、数据或配置与登记内容不同；请使用新 run-id")
    else:
        report = {
            "run_id": args.run_id, "created_at": datetime.now(UTC).isoformat(),
            "identity": identity, "schedule": plan, "audits": [], "rows": [],
            "scope": "Verified 小样本文件级独立 verifier；不是官方 harness 成绩",
        }

    def persist(status: str) -> None:
        """! @brief 保存当前状态、已完成结果与更新时刻。"""

        report["status"] = status
        report["updated_at"] = datetime.now(UTC).isoformat()
        _save(report_path, report)

    persist("auditing")
    audited = {audit["instance_id"] for audit in report["audits"]}
    for key in args.instances:
        if key in audited:
            continue
        print(f"AUDIT START {key}", flush=True)
        try:
            audit = evaluator.audit_real_instance(dataset[key], args.run_id)
        except Exception as exc:
            audit = {"instance_id": key, "baseline_failed": False,
                     "calibration_error": f"{type(exc).__name__}: {exc}"}
        report["audits"].append(audit)
        persist("auditing")
        print(f"AUDIT END {key} valid={audit['baseline_failed']}", flush=True)
    if args.audit_only:
        persist("audited")
        return
    completed = {
        (row["instance_id"], row["variant"], row["repeat_index"])
        for row in report["rows"] if not _is_provider_blocked(row)
    }
    audits = {audit["instance_id"]: audit for audit in report["audits"]}
    for job in plan:
        key = job["instance_id"]
        job_key = (key, job["variant"], job["repeat_index"])
        if job_key in completed or not audits[key]["baseline_failed"]:
            continue
        persist("running")
        print(f"RUN START {key} {job['variant']} r{job['repeat_index']}", flush=True)
        started = perf_counter()
        row = evaluator.evaluate_real_instance(
            dataset[key], job["variant"], args.run_id,
            excluded_targets=audits[key]["unstable_pass_to_pass"],
            repeat_index=job["repeat_index"],
        )
        row["wall_seconds_including_workspace_and_verifier"] = round(perf_counter() - started, 6)
        row["schedule_index"] = plan.index(job)
        row["completed_at"] = datetime.now(UTC).isoformat()
        if _is_provider_blocked(row):
            # 下次重跑会覆盖同作业的标准产物，先保留本次中断的原始证据。
            attempt = len(report.get("provider_failures", [])) + 1
            for field in ("trace_path", "patch_path"):
                original = Path(row[field])
                archived = original.with_name(
                    f"{original.stem}__provider_attempt{attempt}{original.suffix}"
                )
                shutil.copy2(original, archived)
                row[field] = str(archived)
            report.setdefault("provider_failures", []).append(row)
            persist("provider_blocked")
            print(f"RUN BLOCKED {key} {job['variant']} r{job['repeat_index']} "
                  f"tokens={row['total_tokens']}", flush=True)
            raise RuntimeError("供应商额度/限流错误：已保留结果并停止，避免把它当作算法失败")
        report["rows"].append(row)
        persist("running")
        print(f"RUN END {key} {job['variant']} r{job['repeat_index']} "
              f"success={row['success']} tokens={row['total_tokens']} "
              f"agent_seconds={row['elapsed_seconds']:.1f}", flush=True)
    persist("completed" if all(a["baseline_failed"] for a in report["audits"])
            else "completed_with_invalid_environments")
    print(f"REPORT {report_path}", flush=True)


if __name__ == "__main__":
    main()
