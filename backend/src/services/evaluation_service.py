"""! @brief 向工作台提供已发布的最终评测结果，不依赖实验执行工具。"""

import json
from pathlib import Path

RESULT_PATH = Path(__file__).resolve().parents[1] / "assets/evaluation/latest.json"


class EvaluationService:
    """! @brief 只读结果快照，与任务数据库和 research 工具解耦。"""

    @staticmethod
    def _load(run_id: str | None = None) -> dict:
        """! @brief 仅接受发布快照中的批次，防止混入历史实验统计。"""
        result = json.loads(RESULT_PATH.read_text(encoding="utf-8"))
        if run_id is not None and run_id not in {result["run_id"], *result["run_ids"]}:
            raise ValueError("当前发布结果不包含该实验批次")
        return result

    def summary(self, run_id: str | None = None) -> dict:
        """! @brief 返回最终批次按执行策略聚合的质量、耗时与 Token 指标。"""
        return self._load(run_id)["summary"]

    def difficulty_summary(self, run_id: str | None = None) -> list:
        """! @brief 返回快照中已有的难度分组，不为真实 Issue 虚构难度。"""
        return self._load(run_id)["difficulty_summary"]

    def ablation_report(self, run_id: str | None = None) -> list:
        """! @brief 返回同批已发布的配对统计，没有配对时返回空列表。"""
        return self._load(run_id)["ablation_report"]


evaluation_service = EvaluationService()
