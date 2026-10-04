"""! @brief 汇总 Verified 新题配对 A/B 的分题结果。

每题一次运行会产生一个 run 目录；本脚本把它们合并成配对表，
并输出 CSV 供文档引用。用法：

    python -m backend.scripts.summarize_verified_ab
"""

import csv
import json
from pathlib import Path

REPORT_ROOT = Path(__file__).resolve().parents[1] / "data" / "real_world_evals"
OUTPUT_CSV = Path(__file__).resolve().parents[1] / "data" / "verified_ab_summary.csv"


def main() -> None:
    """! @brief 扫描全部真实评测报告，聚合 Verified 分题的配对结果。"""

    rows: list[dict[str, object]] = []
    for report_path in sorted(REPORT_ROOT.glob("*/report.json")):
        report = json.loads(report_path.read_text(encoding="utf-8"))
        if report.get("dataset") != "SWE-bench/SWE-bench_Verified:test":
            continue
        audits = {item["instance_id"]: item for item in report.get("audits", [])}
        for row in report.get("rows", []):
            audit = audits.get(row["instance_id"], {})
            rows.append(
                {
                    "run_id": report["run_id"],
                    "instance_id": row["instance_id"],
                    "repo": row["repo"],
                    "variant": row["variant"],
                    "repeat_index": row["repeat_index"],
                    "success": row["success"],
                    "tests_passed": row["tests_passed"],
                    "iterations": row["iterations"],
                    "tool_calls": row["tool_calls"],
                    "total_tokens": row["total_tokens"],
                    "elapsed_seconds": round(row["elapsed_seconds"], 1),
                    "patch_bytes": row["patch_bytes"],
                    "calibrated": audit.get("baseline_failed"),
                    "unexpected_failures": ";".join(
                        row.get("verification", {}).get("unexpected_failures", [])
                    ),
                    "error": row.get("error") or "",
                }
            )

    if not rows:
        print("没有找到 Verified 评测结果")
        return

    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_CSV.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    # 按题聚合：同一变体的多次重复先算题内成功率，再跨题统计。
    by_variant: dict[str, dict[str, list[dict[str, object]]]] = {}
    for row in rows:
        by_variant.setdefault(str(row["variant"]), {}).setdefault(
            str(row["instance_id"]), []
        ).append(row)

    print(f"共 {len(rows)} 条运行记录，写入 {OUTPUT_CSV}")
    for variant, per_instance in sorted(by_variant.items()):
        solved = sum(
            1
            for records in per_instance.values()
            if any(item["success"] for item in records)
        )
        runs = sum(len(records) for records in per_instance.values())
        successes = sum(1 for records in per_instance.values() for item in records if item["success"])
        tokens = [int(item["total_tokens"]) for records in per_instance.values() for item in records]
        print(
            f"{variant}: 按题 {solved}/{len(per_instance)}，按次 {successes}/{runs}，"
            f"平均 Token {sum(tokens) // max(len(tokens), 1)}"
        )

    shared = set.intersection(
        *(set(per_instance) for per_instance in by_variant.values())
    ) if len(by_variant) > 1 else set()
    if shared:
        print("\n配对（同一批题、两个变体都有结果）:")
        for variant, per_instance in sorted(by_variant.items()):
            solved = sum(
                1
                for instance_id in shared
                if any(item["success"] for item in per_instance[instance_id])
            )
            print(f"  {variant}: {solved}/{len(shared)}")


if __name__ == "__main__":
    main()
