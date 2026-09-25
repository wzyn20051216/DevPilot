"""! @brief 论文实验原始数据导出器。"""

import csv
from pathlib import Path

from ..database.evaluation_repository import evaluation_repository

EXPORT_DIR = Path(__file__).resolve().parents[2] / "data" / "eval_exports"


def export_results_csv(run_id: str | None = None) -> Path:
    """! @brief 将评测结果导出为 Excel 友好的 UTF-8 BOM CSV。

    @param run_id 可选实验批次；None 导出全部数据。
    @return 已生成 CSV 的绝对路径。
    """

    results = evaluation_repository.get_results(run_id=run_id)
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    output = EXPORT_DIR / (f"{run_id}.csv" if run_id else "all_results.csv")

    with output.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.writer(file)
        writer.writerow(
            [
                "run_id",
                "case_id",
                "variant",
                "repeat_index",
                "success",
                "tests_passed",
                "elapsed_seconds",
                "tool_calls",
                "iterations",
                "repair_rounds",
                "prompt_tokens",
                "completion_tokens",
                "total_tokens",
                "workspace_path",
                "error",
                "created_at",
            ]
        )
        for item in results:
            writer.writerow(
                [
                    item.run_id,
                    item.case_id,
                    item.variant,
                    item.repeat_index,
                    item.success,
                    item.tests_passed,
                    item.elapsed_seconds,
                    item.tool_calls,
                    item.iterations,
                    item.repair_rounds,
                    item.prompt_tokens,
                    item.completion_tokens,
                    item.total_tokens,
                    item.workspace_path,
                    item.error or "",
                    item.created_at.isoformat(),
                ]
            )
    return output
