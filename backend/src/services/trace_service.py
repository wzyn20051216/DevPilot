"""! @brief Agent 运行轨迹和成本聚合服务。"""

from collections import defaultdict

from ..config import settings
from ..database.task_repository import TaskRepository, task_repository


class TraceService:
    """! @brief 从持久化事件聚合任务级性能、成本和失败指标。"""

    def __init__(self, repository: TaskRepository) -> None:
        self.repository = repository

    def task_metrics(self, task_id: str) -> dict[str, object]:
        """! @brief 返回单个任务的 Token、耗时和工具调用分布。"""

        task = self.repository.get_task(task_id)
        events = self.repository.get_events(task_id)
        tools = self.repository.get_tool_calls(task_id)
        prompt_tokens = 0
        completion_tokens = 0
        total_tokens = 0
        llm_seconds = 0.0
        tool_seconds = 0.0
        per_agent: dict[str, dict[str, float | int]] = defaultdict(
            lambda: {"events": 0, "tool_calls": 0, "tool_seconds": 0.0}
        )

        for event in events:
            agent = str(event["agent"])
            per_agent[agent]["events"] = int(per_agent[agent]["events"]) + 1
            if event["type"] not in {"final", "error", "cancelled"}:
                continue
            data = event.get("data", {})
            if not isinstance(data, dict):
                continue
            usage = data.get("usage")
            if isinstance(usage, dict):
                prompt_tokens += int(usage.get("prompt_tokens", 0) or 0)
                completion_tokens += int(usage.get("completion_tokens", 0) or 0)
                total_tokens += int(usage.get("total_tokens", 0) or 0)
            timing = data.get("timing")
            if isinstance(timing, dict):
                llm_seconds += float(timing.get("llm_seconds", 0.0) or 0.0)

        for tool in tools:
            agent = str(tool["agent"])
            duration = float(tool.get("duration_seconds", 0.0) or 0.0)
            tool_seconds += duration
            per_agent[agent]["tool_calls"] = int(per_agent[agent]["tool_calls"]) + 1
            per_agent[agent]["tool_seconds"] = round(
                float(per_agent[agent]["tool_seconds"]) + duration,
                6,
            )

        estimated_cost = (
            prompt_tokens * settings.llm_prompt_cost_per_million
            + completion_tokens * settings.llm_completion_cost_per_million
        ) / 1_000_000
        retrieval_calls = [tool for tool in tools if tool["tool"] == "retrieve_code"]
        return {
            "task_id": task_id,
            "status": task.status,
            "events": len(events),
            "tool_calls": len(tools),
            "failed_tool_calls": sum(not bool(tool["succeeded"]) for tool in tools),
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "estimated_cost": round(estimated_cost, 8),
            "llm_seconds": round(llm_seconds, 6),
            "tool_seconds": round(tool_seconds, 6),
            "retrieval_calls": len(retrieval_calls),
            "retrieval_seconds": round(
                sum(float(tool["duration_seconds"]) for tool in retrieval_calls),
                6,
            ),
            "per_agent": dict(per_agent),
        }


trace_service = TraceService(task_repository)
