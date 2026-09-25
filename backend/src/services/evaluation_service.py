"""! @brief Evaluation Dashboard 查询服务。"""

from ..database.evaluation_repository import evaluation_repository
from ..evals.analysis import (
    AnalysisRow,
    aggregate_case_results,
    build_ablation_report,
    summarize_by_difficulty,
)
from ..evals.dataset import load_benchmark_cases
from ..evals.metrics import MetricValues, summarize_results


class EvaluationService:
    """! @brief 连接评测仓储与 Dashboard API 的只读服务。"""

    def summary(self, run_id: str | None = None) -> dict[str, MetricValues]:
        """! @brief 汇总全部实验或指定批次的指标。

        @param run_id 可选实验批次 ID。
        @return 按 variant 分组的成功率、成本和耗时指标。
        """

        results = evaluation_repository.get_results(run_id=run_id)
        return summarize_results(results)

    def difficulty_summary(self, run_id: str | None = None) -> list[AnalysisRow]:
        """! @brief 按难度和 variant 返回 case 级汇总指标。"""

        results = evaluation_repository.get_results(run_id=run_id)
        return summarize_by_difficulty(results, load_benchmark_cases())

    def ablation_report(self, run_id: str | None = None) -> list[AnalysisRow]:
        """! @brief 返回 RAG 与多 Agent 的完整配对消融报告。"""

        results = evaluation_repository.get_results(run_id=run_id)
        return build_ablation_report(aggregate_case_results(results))


evaluation_service = EvaluationService()
