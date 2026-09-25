"""! @brief 将 Evaluation Dashboard 指标导出为论文级 PNG 图表。"""

from collections.abc import Callable
from pathlib import Path

import matplotlib

# 服务器和 CI 通常没有图形桌面，必须在导入 pyplot 前切换无界面后端。
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: I001

from ..database.evaluation_repository import evaluation_repository
from .metrics import MetricValues, summarize_results


FIGURE_DIR = Path(__file__).resolve().parents[2] / "data" / "eval_figures"
VARIANT_ORDER = ("single_no_rag", "single_rag", "multi_no_rag", "multi_rag")
BAR_COLORS = ("#287271", "#E9C46A", "#3D5A80", "#E76F51")


def _get_summary(run_id: str | None = None) -> dict[str, MetricValues]:
    """! @brief 从 SQLite 读取结果并计算图表所需聚合指标。"""

    return summarize_results(evaluation_repository.get_results(run_id=run_id))


def _plot_metric(
    *,
    metric: str,
    filename: str,
    ylabel: str,
    title: str,
    value_format: Callable[[float], str],
    run_id: str | None = None,
) -> Path:
    """! @brief 绘制单项指标柱状图并保存为 300 DPI PNG。"""

    summary = _get_summary(run_id=run_id)
    variants = [variant for variant in VARIANT_ORDER if variant in summary]
    if not variants:
        raise ValueError("没有可用于绘图的评测数据")

    values = [float(summary[variant][metric]) for variant in variants]
    FIGURE_DIR.mkdir(parents=True, exist_ok=True)
    output = FIGURE_DIR / filename

    figure, axis = plt.subplots(figsize=(9, 5))
    bars = axis.bar(variants, values, color=BAR_COLORS[: len(variants)])
    axis.set_xlabel("Agent Variant")
    axis.set_ylabel(ylabel)
    axis.set_title(title)
    axis.tick_params(axis="x", rotation=20)
    axis.grid(axis="y", color="#D7DCE2", linewidth=0.8, alpha=0.7)
    axis.set_axisbelow(True)
    axis.spines["top"].set_visible(False)
    axis.spines["right"].set_visible(False)
    axis.bar_label(bars, labels=[value_format(value) for value in values], padding=3)
    figure.tight_layout()
    figure.savefig(output, dpi=300, bbox_inches="tight")
    plt.close(figure)
    return output


def plot_success_rate(run_id: str | None = None) -> Path:
    """! @brief 并列展示端到端成功率与独立测试通过率。

    两者的差值能识别「代码已修好，但 Agent 编排流程异常结束」的情况，
    因此质量图不能只展示其中一项。
    """

    summary = _get_summary(run_id=run_id)
    variants = [variant for variant in VARIANT_ORDER if variant in summary]
    if not variants:
        raise ValueError("没有可用于绘图的评测数据")

    success_rates = [float(summary[variant]["success_rate"]) for variant in variants]
    test_pass_rates = [
        float(summary[variant]["test_pass_rate"]) for variant in variants
    ]
    positions = list(range(len(variants)))
    bar_width = 0.36

    FIGURE_DIR.mkdir(parents=True, exist_ok=True)
    output = FIGURE_DIR / "success_rate.png"
    figure, axis = plt.subplots(figsize=(9, 5))
    success_bars = axis.bar(
        [position - bar_width / 2 for position in positions],
        success_rates,
        width=bar_width,
        color="#287271",
        label="End-to-end success",
    )
    test_bars = axis.bar(
        [position + bar_width / 2 for position in positions],
        test_pass_rates,
        width=bar_width,
        color="#E9C46A",
        label="Verifier tests passed",
    )
    axis.set_xlabel("Agent Variant")
    axis.set_ylabel("Rate")
    axis.set_title("Agent Outcome Quality")
    axis.set_xticks(positions, variants, rotation=20)
    axis.set_ylim(0, 1.12)
    axis.grid(axis="y", color="#D7DCE2", linewidth=0.8, alpha=0.7)
    axis.set_axisbelow(True)
    axis.spines["top"].set_visible(False)
    axis.spines["right"].set_visible(False)
    axis.legend(
        frameon=False,
        loc="center left",
        bbox_to_anchor=(1.01, 0.5),
    )
    axis.bar_label(
        success_bars,
        labels=[f"{value:.0%}" for value in success_rates],
        padding=3,
    )
    axis.bar_label(
        test_bars,
        labels=[f"{value:.0%}" for value in test_pass_rates],
        padding=3,
    )
    figure.tight_layout()
    figure.savefig(output, dpi=300, bbox_inches="tight")
    plt.close(figure)
    return output


def plot_tool_calls(run_id: str | None = None) -> Path:
    """! @brief 生成平均工具调用次数图。"""

    return _plot_metric(
        metric="avg_tool_calls",
        filename="avg_tool_calls.png",
        ylabel="Average Tool Calls",
        title="Tool Usage Comparison",
        value_format=lambda value: f"{value:.1f}",
        run_id=run_id,
    )


def plot_tokens(run_id: str | None = None) -> Path:
    """! @brief 生成平均 Token 消耗图。"""

    return _plot_metric(
        metric="avg_total_tokens",
        filename="avg_tokens.png",
        ylabel="Average Total Tokens",
        title="Token Cost Comparison",
        value_format=lambda value: f"{value:,.0f}",
        run_id=run_id,
    )


def plot_elapsed_time(run_id: str | None = None) -> Path:
    """! @brief 生成平均执行耗时图。"""

    return _plot_metric(
        metric="avg_elapsed_seconds",
        filename="avg_elapsed_seconds.png",
        ylabel="Average Time (s)",
        title="Execution Time Comparison",
        value_format=lambda value: f"{value:.1f}s",
        run_id=run_id,
    )


def generate_all_figures(run_id: str | None = None) -> list[Path]:
    """! @brief 一键生成成功率、工具、Token 和耗时四张论文图。"""

    return [
        plot_success_rate(run_id=run_id),
        plot_tool_calls(run_id=run_id),
        plot_tokens(run_id=run_id),
        plot_elapsed_time(run_id=run_id),
    ]
