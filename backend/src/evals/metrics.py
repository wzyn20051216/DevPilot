"""! @brief 评测结果聚合指标。"""

from collections import defaultdict
from statistics import mean
from .models import EvaluationResult


MetricValues = dict[str, int | float]


def summarize_results(results: list[EvaluationResult]) -> dict[str, MetricValues]:
    """! @brief 按 variant 汇总成功率、效率和成本指标。

    @param results 待汇总的原始评测结果。
    @return 以 variant 为键的指标字典；空输入返回空字典。
    """

    grouped: dict[str, list[EvaluationResult]] = defaultdict(list)
    for result in results:
        grouped[result.variant].append(result)

    summary: dict[str, MetricValues] = {}
    for variant, items in grouped.items():
        summary[variant] = {
            "runs": len(items),
            "success_rate": sum(item.success for item in items) / len(items),
            "test_pass_rate": sum(item.tests_passed for item in items) / len(items),
            "avg_tool_calls": mean(item.tool_calls for item in items),
            "avg_iterations": mean(item.iterations for item in items),
            "avg_repair_rounds": mean(item.repair_rounds for item in items),
            "avg_elapsed_seconds": mean(item.elapsed_seconds for item in items),
            "avg_total_tokens": mean(item.total_tokens for item in items),
        }
    return summary
