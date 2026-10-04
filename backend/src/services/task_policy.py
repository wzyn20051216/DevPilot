"""! @brief API 与独立 Worker 共享的任务执行策略。"""

from loguru import logger

from ..config import settings
from ..rag.policy import decide_rag


def task_rag_enabled(task) -> bool:
    """! @brief 根据持久化模式及可选启发式策略决定是否启用 RAG。"""
    if settings.rag_mode == "auto" and task.execution_mode.startswith("single_"):
        decision = decide_rag(task.repo_path, task.question)
        logger.info("任务 {} RAG={}，依据={}", task.id, decision.enabled, decision.reasons)
        return decision.enabled
    return task.execution_mode in {"single_rag", "multi_rag"}
