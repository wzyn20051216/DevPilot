"""! @brief 上下文断点检查点的持久化存取模块。

本模块是技术手册 10.1「上下文断点恢复」的存储层：把 Agent 每轮迭代后的
对话消息快照 UPSERT 进 SQLite 的 ``agent_contexts`` 表，服务重启后由
``TaskExecutionService._run`` 在开始执行前读回并注入到 Agent。

设计原则：
- 所有异常向上抛，由调用方（执行服务）负责容错与降级，本模块不吞异常；
- 每次保存覆盖同 (task_id, agent_name) 的旧检查点，只保留最新一份快照；
- ``max_messages`` 用于限制单份快照体积，超出时保留系统/任务锚点和最近完整工具回合。
"""

import json
from datetime import UTC, datetime
from typing import Any, cast

from ..database.connection import get_connection, upsert_sql


def _now_iso() -> str:
    """! @brief 返回精确到秒的 UTC ISO 时间字符串。"""

    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _message_groups(messages: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """! @brief 验证工具调用配对并分组，防止截断后留下孤立工具结果。"""
    groups: list[list[dict[str, Any]]] = []
    pending: set[str] = set()
    for message in messages:
        if not isinstance(message, dict) or message.get("role") not in {
            "system", "user", "assistant", "tool", "developer",
        }:
            raise ValueError("检查点包含非法消息")
        if message["role"] == "tool":
            call_id = message.get("tool_call_id")
            if call_id not in pending:
                raise ValueError("检查点存在孤立或重复的工具结果")
            pending.remove(call_id)
            groups[-1].append(message)
        else:
            if pending:
                raise ValueError("检查点工具调用缺少结果")
            groups.append([message])
            calls = message.get("tool_calls") or []
            ids = [call.get("id") for call in calls]
            if calls and (message["role"] != "assistant" or
                          any(not isinstance(i, str) or not i for i in ids) or
                          len(set(ids)) != len(ids)):
                raise ValueError("检查点工具调用格式错误")
            pending = set(ids)
    if pending:
        raise ValueError("检查点工具调用尚未完成")
    return groups


def _trim_messages(messages: list[dict[str, Any]], limit: int | None) -> list[dict[str, Any]]:
    """! @brief 保留系统和原始请求，按完整工具回合裁剪；limit 是消息数上限。"""
    groups = _message_groups(messages)
    if limit is None or len(messages) <= limit:
        return messages
    if limit < 2:
        raise ValueError("检查点消息上限至少为 2")
    prefix = []
    # 标准 Agent 上下文的 system + user 是不可丢弃的任务锚点。
    if len(groups) >= 2 and groups[0][0]["role"] == "system" and groups[1][0]["role"] == "user":
        prefix = groups[:2]
        groups = groups[2:]
    budget = limit - sum(map(len, prefix))
    tail = []
    for group in reversed(groups):
        if len(group) > budget:
            break
        tail.insert(0, group)
        budget -= len(group)
    return [message for group in prefix + tail for message in group]


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
    @param max_messages 可选上限；消息数超出时保留任务锚点并按完整工具回合裁剪，
        并置 ``truncated=1`` 表示快照被截断过。
    """

    stored = _trim_messages(messages, max_messages)
    truncated = int(len(stored) < len(messages))

    payload = json.dumps(stored, ensure_ascii=False, default=str)
    now = _now_iso()

    with get_connection() as conn:
        conn.execute(
            upsert_sql(
                "agent_contexts",
                ("task_id", "agent_name"),
                (
                    "task_id",
                    "agent_name",
                    "messages_json",
                    "message_count",
                    "total_tokens",
                    "truncated",
                    "updated_at",
                ),
            ),
            (
                task_id,
                agent_name,
                payload,
                len(stored),
                0,
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
    messages = json.loads(str(row["messages_json"]))
    if not isinstance(messages, list):
        raise ValueError("检查点必须是消息列表")
    _message_groups(messages)
    return cast(list[dict[str, Any]], messages)


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
