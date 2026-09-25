"""! @brief 第十六关正式实验的统计分析工具。

重复实验先按 case + variant 聚合，再进行配对比较。这样同一个用例重复运行
多次不会被错误地当成多个独立样本，置信区间和显著性检验的统计单位始终是
Benchmark case。
"""

from collections import defaultdict
from statistics import mean
from typing import Protocol, cast

import numpy as np
from scipy.stats import wilcoxon

from .models import BenchmarkCase, EvaluationResult, EvaluationVariant


Numeric = int | float
CaseAggregate = dict[str, str | Numeric]
AnalysisRow = dict[str, str | Numeric | None]
AggregateKey = tuple[str, EvaluationVariant]


class _WilcoxonResult(Protocol):
    """! @brief 补充 SciPy 未提供类型桩的 pvalue 最小接口。"""

    pvalue: float

ABLATION_PAIRS: tuple[tuple[EvaluationVariant, EvaluationVariant], ...] = (
    ("single_no_rag", "single_rag"),
    ("multi_no_rag", "multi_rag"),
    ("single_no_rag", "multi_no_rag"),
    ("single_rag", "multi_rag"),
)
ABLATION_METRICS: tuple[str, ...] = (
    "success_rate",
    "tool_calls",
    "iterations",
    "elapsed_seconds",
    "total_tokens",
)


def aggregate_case_results(
    results: list[EvaluationResult],
) -> dict[AggregateKey, CaseAggregate]:
    """! @brief 将重复运行结果聚合为每个 case/variant 一个统计样本。

    @param results 数据库中的逐次实验结果。
    @return 以 ``(case_id, variant)`` 为键的平均指标。
    """

    grouped: dict[AggregateKey, list[EvaluationResult]] = defaultdict(list)
    for result in results:
        grouped[(result.case_id, result.variant)].append(result)

    aggregated: dict[AggregateKey, CaseAggregate] = {}
    for (case_id, variant), items in grouped.items():
        aggregated[(case_id, variant)] = {
            "case_id": case_id,
            "variant": variant,
            "runs": len(items),
            "success_rate": mean(float(item.success) for item in items),
            "test_pass_rate": mean(float(item.tests_passed) for item in items),
            "tool_calls": mean(item.tool_calls for item in items),
            "iterations": mean(item.iterations for item in items),
            "repair_rounds": mean(item.repair_rounds for item in items),
            "elapsed_seconds": mean(item.elapsed_seconds for item in items),
            "total_tokens": mean(item.total_tokens for item in items),
        }
    return aggregated


def summarize_by_difficulty(
    results: list[EvaluationResult],
    cases: list[BenchmarkCase],
) -> list[AnalysisRow]:
    """! @brief 按难度和 variant 汇总 case 级实验指标。

    先对重复运行取 case 内平均，再对同难度的 case 求平均，避免重复次数不同的
    用例获得不合理的更高权重。``runs`` 仍报告底层真实运行次数。
    """

    case_by_id = {case.id: case for case in cases}
    aggregated = aggregate_case_results(results)
    grouped: dict[tuple[str, EvaluationVariant], list[CaseAggregate]] = defaultdict(list)
    for (case_id, variant), item in aggregated.items():
        case = case_by_id.get(case_id)
        if case is not None:
            grouped[(case.difficulty, variant)].append(item)

    difficulty_order = {"easy": 0, "medium": 1, "hard": 2}
    rows: list[AnalysisRow] = []
    for (difficulty, variant), items in sorted(
        grouped.items(),
        key=lambda pair: (difficulty_order[pair[0][0]], pair[0][1]),
    ):
        rows.append(
            {
                "difficulty": difficulty,
                "variant": variant,
                "cases": len(items),
                "runs": sum(int(item["runs"]) for item in items),
                "success_rate": mean(float(item["success_rate"]) for item in items),
                "avg_tool_calls": mean(float(item["tool_calls"]) for item in items),
                "avg_elapsed_seconds": mean(
                    float(item["elapsed_seconds"]) for item in items
                ),
                "avg_tokens": mean(float(item["total_tokens"]) for item in items),
            }
        )
    return rows


def get_paired_deltas(
    aggregated: dict[AggregateKey, CaseAggregate],
    variant_a: EvaluationVariant,
    variant_b: EvaluationVariant,
    metric: str,
) -> list[float]:
    """! @brief 计算同一批 case 上 B-A 的配对指标差值。

    正值始终表示 variant B 的数值更大。对成功率通常是提升；对耗时、工具调用
    和 Token 则通常表示成本增加，解读报告时必须结合指标方向。
    """

    case_ids_a = {case_id for case_id, variant in aggregated if variant == variant_a}
    case_ids_b = {case_id for case_id, variant in aggregated if variant == variant_b}
    deltas: list[float] = []
    for case_id in sorted(case_ids_a & case_ids_b):
        value_a = aggregated[(case_id, variant_a)].get(metric)
        value_b = aggregated[(case_id, variant_b)].get(metric)
        if not isinstance(value_a, (int, float)) or not isinstance(value_b, (int, float)):
            raise KeyError(f"聚合结果中缺少数值指标：{metric}")
        deltas.append(float(value_b) - float(value_a))
    return deltas


def bootstrap_mean_ci(
    values: list[float],
    confidence: float = 0.95,
    samples: int = 10_000,
    seed: int = 42,
) -> tuple[float, float]:
    """! @brief 使用有放回重采样估计均值的 Bootstrap 置信区间。"""

    if not values:
        raise ValueError("Bootstrap 至少需要一个配对样本")
    if not 0 < confidence < 1:
        raise ValueError("confidence 必须在 0 和 1 之间")
    if samples <= 0:
        raise ValueError("samples 必须为正整数")

    data = np.asarray(values, dtype=np.float64)
    generator = np.random.default_rng(seed)
    sample_indexes = generator.integers(0, len(data), size=(samples, len(data)))
    sample_means = data[sample_indexes].mean(axis=1)
    alpha = (1.0 - confidence) / 2.0
    lower, upper = np.quantile(sample_means, [alpha, 1.0 - alpha])
    return float(lower), float(upper)


def wilcoxon_paired_test(values: list[float]) -> float | None:
    """! @brief 对配对差值执行 Wilcoxon signed-rank 检验。

    少于 5 个 case 时统计功效过低，返回 None；全部差值为零时直接返回 1，
    避免 SciPy 对零差值样本发出无意义警告。
    """

    if len(values) < 5:
        return None
    if all(np.isclose(value, 0.0) for value in values):
        return 1.0
    try:
        # SciPy 当前缺少完整类型桩；先收窄为 object，再声明本函数实际只依赖
        # pvalue 属性，避免把第三方库的未知返回类型扩散到业务代码。
        result = cast(_WilcoxonResult, cast(object, wilcoxon(values)))
    except ValueError:
        return None
    return float(result.pvalue)


def compare_variants(
    aggregated: dict[AggregateKey, CaseAggregate],
    variant_a: EvaluationVariant,
    variant_b: EvaluationVariant,
    metric: str,
) -> AnalysisRow:
    """! @brief 对两个 variant 生成配对均值、置信区间和显著性结果。"""

    deltas = get_paired_deltas(aggregated, variant_a, variant_b, metric)
    if not deltas:
        raise ValueError(f"{variant_a} 与 {variant_b} 没有共同完成的 case")
    ci_lower, ci_upper = bootstrap_mean_ci(deltas)
    return {
        "variant_a": variant_a,
        "variant_b": variant_b,
        "metric": metric,
        "cases": len(deltas),
        "mean_delta": mean(deltas),
        "ci_lower": ci_lower,
        "ci_upper": ci_upper,
        "wilcoxon_p": wilcoxon_paired_test(deltas),
    }


def build_ablation_report(
    aggregated: dict[AggregateKey, CaseAggregate],
) -> list[AnalysisRow]:
    """! @brief 生成四组架构对照、五项指标组成的 20 行消融报告。"""

    report: list[AnalysisRow] = []
    for variant_a, variant_b in ABLATION_PAIRS:
        for metric in ABLATION_METRICS:
            report.append(
                compare_variants(
                    aggregated,
                    variant_a=variant_a,
                    variant_b=variant_b,
                    metric=metric,
                )
            )
    return report
