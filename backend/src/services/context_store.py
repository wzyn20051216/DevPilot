"""! @brief 上下文断点检查点的持久化存取模块。

本模块是技术手册 10.1「上下文断点恢复」的存储层：把 Agent 每轮迭代后的
对话消息快照 UPSERT 进 SQLite 的 ``agent_contexts`` 表，服务重启后由
``TaskExecutionService._run`` 在开始执行前读回并注入到 Agent。

设计原则：
- 所有异常向上抛，由调用方（执行服务）负责容错与降级，本模块不吞异常；
- 每次保存覆盖同 (task_id, agent_name) 的旧检查点，只保留最新一份快照；
- ``max_messages`` 用于限制单份快照体积，超出时只保留最近的消息。
"""

import json
from datetime import UTC, datetime
from typing import Any, cast

from ..database.connection import get_connection


def _now_iso() -> str:
    """! @brief 返回精确到秒的 UTC ISO 时间字符串。"""

    return datetime.now(UTC).replace(microsecond=0).isoformat()


def save_context(
    task_id: str,
    agent_name: str,
    messages: list[dict[str, Any]],
    *,
    max_messages: int | None = None,
) -> None:
    """! @brief UPSERT 保存 Agent 消息检查点。

    @param task_id 任务唯一标识。
    @param agent_name Agent 名称（与 ``agent_contexts`` 复合主键对应）。
    @param messages 要保存的对话消息列表。
    @param max_messages 可选上限；消息数超出时只保留最近 max_messages 条，
        并置 ``truncated=1`` 表示快照被截断过。
    """

    stored = messages
    truncated = 0
    # 只保留最近 max_messages 条：断点恢复更关心 Agent 临近中断时的上下文，
    # 越早的对话对继续执行的价值越低，截断能避免快照无限膨胀。
    if max_messages is not None and len(stored) > max_messages:
        stored = stored[-max_messages:]
        truncated = 1

    payload = json.dumps(stored, ensure_ascii=False, default=str)
    now = _now_iso()

    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO agent_contexts (
                task_id,
                agent_name,
                messages_json,
                message_count,
                total_tokens,
                truncated,
                updated_at
            )
            VALUES (?, ?, ?, ?, 0, ?, ?)
            ON CONFLICT(task_id, agent_name) DO UPDATE SET
                messages_json = excluded.messages_json,
                message_count = excluded.message_count,
                total_tokens = excluded.total_tokens,
                truncated = excluded.truncated,
                updated_at = excluded.updated_at
            """,
            (
                task_id,
                agent_name,
                payload,
                len(stored),
                truncated,
                now,
            ),
        )


def load_context(
    task_id: str,
    agent_name: str,
) -> list[dict[str, Any]] | None:
    """! @brief 读取指定 Agent 的最新消息检查点。

    @param task_id 任务唯一标识。
    @param agent_name Agent 名称。
    @return 反序列化后的消息列表；没有记录时返回 None。
    """

    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT messages_json
            FROM agent_contexts
            WHERE task_id = ? AND agent_name = ?
            """,
            (task_id, agent_name),
        ).fetchone()

    if row is None:
        return None
    return cast(list[dict[str, Any]], json.loads(str(row["messages_json"])))


def clear_context(
    task_id: str,
    agent_name: str | None = None,
) -> int:
    """! @brief 删除任务（或任务下某个 Agent）的检查点。

    @param task_id 任务唯一标识。
    @param agent_name 可选 Agent 名称；为 None 时清空该任务的全部检查点。
    @return 实际删除的行数。
    """

    with get_connection() as conn:
        if agent_name is None:
            cursor = conn.execute(
                "DELETE FROM agent_contexts WHERE task_id = ?",
                (task_id,),
            )
        else:
            cursor = conn.execute(
                """
                DELETE FROM agent_contexts
                WHERE task_id = ? AND agent_name = ?
                """,
                (task_id, agent_name),
            )
        return cursor.rowcount
