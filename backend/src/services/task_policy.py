"""! @brief API 与独立 Worker 共享的任务执行策略。"""

from loguru import logger

from ..agents.strategy import AgentStrategy, DEFAULT_MAX_ITERATIONS, decide_strategy
from ..config import settings
from ..rag.policy import decide_rag


def task_rag_enabled(task) -> bool:
    """! @brief 根据持久化模式及可选启发式策略决定是否启用 RAG。"""
    if settings.rag_mode == "auto" and task.execution_mode.startswith("single_"):
        decision = decide_rag(task.repo_path, task.question)
        logger.info("任务 {} RAG={}，依据={}", task.id, decision.enabled, decision.reasons)
        return decision.enabled
    return task.execution_mode in {"single_rag", "multi_rag"}


def decide_task_strategy(task) -> AgentStrategy:
    """! @brief 按全局开关决定单 Agent 任务工作流。"""

    if not settings.strategy_router_enabled:
        return AgentStrategy(
            use_outline=False,
            enforce_probe=False,
            max_iterations=DEFAULT_MAX_ITERATIONS,
            mode="static_fallback",
            reasons=["动态策略路由已关闭，回退到 execution_mode 的既有单 Agent 行为"],
            metrics={"execution_mode": task.execution_mode},
        )

    decision = decide_strategy(task.repo_path, task.question)
    logger.info("任务 {} 策略={}，依据={}", task.id, decision.mode, decision.reasons)
    return decision
