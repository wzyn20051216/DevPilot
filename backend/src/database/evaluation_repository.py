"""! @brief Evals 结果仓储。"""

from ..evals.models import EvaluationResult
from .connection import get_connection


class EvaluationRepository:
    """! @brief 把每次评测结果写入 SQLite，并支持按批次读取。"""

    def save_result(self, result: EvaluationResult) -> None:
        """! @brief 持久化一条评测结果。"""

        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO evaluation_results (
                    run_id, case_id, variant, repeat_index, success,
                    tests_passed, tool_calls,
                    iterations, repair_rounds, elapsed_seconds,
                    prompt_tokens, completion_tokens, total_tokens,
                    workspace_path, error, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result.run_id,
                    result.case_id,
                    result.variant,
                    result.repeat_index,
                    int(result.success),
                    int(result.tests_passed),
                    result.tool_calls,
                    result.iterations,
                    result.repair_rounds,
                    result.elapsed_seconds,
                    result.prompt_tokens,
                    result.completion_tokens,
                    result.total_tokens,
                    result.workspace_path,
                    result.error,
                    result.created_at.isoformat(),
                ),
            )

    def get_results(self, run_id: str | None = None) -> list[EvaluationResult]:
        """! @brief 查询全部结果，或只查询指定实验批次。

        @param run_id 可选批次 ID；None 表示读取全部历史结果。
        @return 按数据库写入顺序排列的 EvaluationResult 列表。
        """

        with get_connection() as conn:
            if run_id is None:
                rows = conn.execute(
                    """
                    SELECT * FROM evaluation_results
                    ORDER BY id ASC
                    """
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM evaluation_results
                    WHERE run_id = ?
                    ORDER BY id ASC
                    """,
                    (run_id,),
                ).fetchall()

        return [
            EvaluationResult.model_validate(
                {
                    **dict(row),
                    "success": bool(row["success"]),
                    "tests_passed": bool(row["tests_passed"]),
                }
            )
            for row in rows
        ]

    def list_by_run(self, run_id: str) -> list[EvaluationResult]:
        """! @brief 兼容第十四关调用方式，读取指定批次结果。"""

        return self.get_results(run_id=run_id)


evaluation_repository = EvaluationRepository()
