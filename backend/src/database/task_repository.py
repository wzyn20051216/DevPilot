"""! @brief 任务数据仓储模块。

本模块封装任务记录的增删改查逻辑。服务层通过 TaskRepository 访问
SQLite 中的 tasks / plan_steps 表，而不是直接操作数据库连接。
"""

import json
from datetime import UTC, datetime
from uuid import uuid4

from ..exceptions import TaskNotFoundError
from ..models.agent_state import AgentEvent, PlanStep
from ..models.task import DevelopmentTask, TaskStatus
from .connection import get_connection


def _now_iso() -> str:
    """! @brief 获取 UTC ISO 时间字符串。

    @return 当前 UTC 时间，精确到秒。
    """
    return datetime.now(UTC).replace(microsecond=0).isoformat()


class TaskRepository:
    """! @brief 基于 SQLite 的任务仓储。

    TaskRepository 替代旧的进程内 TaskStore，把任务和计划步骤持久化到
    SQLite。服务重启后任务不会丢失，也便于后续查询和追踪回放。
    """

    def create_task(
        self,
        repo_path: str,
        question: str,
        plan: list[PlanStep],
    ) -> DevelopmentTask:
        """! @brief 创建任务并保存计划步骤。

        @param repo_path 目标代码仓库路径。
        @param question 用户原始任务问题。
        @param plan Planner 生成的计划步骤。
        @return 已持久化的 DevelopmentTask。
        """
        task = DevelopmentTask(
            id=uuid4().hex,
            repo_path=repo_path,
            question=question,
            plan=plan,
        )
        now = _now_iso()

        # 主任务和全部计划步骤放在同一个连接上下文中：任一 INSERT 抛错时
        # sqlite3 会回滚本次事务，避免出现“有任务但计划只写了一半”。
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO tasks (
                    id,
                    repo_path,
                    question,
                    status,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    task.id,
                    task.repo_path,
                    task.question,
                    task.status,
                    now,
                    now,
                ),
            )
            # executemany 使用同一条参数化 SQL 批量写入步骤；问号占位符由
            # sqlite3 绑定值，既避免手工拼 SQL，也正确处理标题中的引号。
            conn.executemany(
                """
                INSERT INTO plan_steps (
                    task_id,
                    step_index,
                    title,
                    description,
                    status
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (
                        task.id,
                        step.id,
                        step.title,
                        step.description,
                        step.status,
                    )
                    for step in plan
                ],
            )

        return task

    def get_task(
        self,
        task_id: str,
    ) -> DevelopmentTask:
        """! @brief 按任务 id 查询任务。

        @param task_id 任务唯一标识。
        @return 对应的 DevelopmentTask。
        @exception TaskNotFoundError 当任务不存在时抛出。
        """
        with get_connection() as conn:
            task_row = conn.execute(
                """
                SELECT
                    id,
                    repo_path,
                    question,
                    status
                FROM tasks
                WHERE id = ?
                """,
                (task_id,),
            ).fetchone()

            if task_row is None:
                raise TaskNotFoundError(f"任务不存在: {task_id}")

            step_rows = conn.execute(
                """
                SELECT
                    step_index,
                    title,
                    description,
                    status
                FROM plan_steps
                WHERE task_id = ?
                ORDER BY step_index ASC
                """,
                (task_id,),
            ).fetchall()

        # tasks 与 plan_steps 分表保存，这里重新聚合为 API 层使用的领域模型。
        # ORDER BY step_index 保证重启后计划顺序仍与 Planner 输出一致。
        return DevelopmentTask(
            id=str(task_row["id"]),
            repo_path=str(task_row["repo_path"]),
            question=str(task_row["question"]),
            status=task_row["status"],
            plan=[
                PlanStep(
                    id=int(row["step_index"]),
                    title=str(row["title"]),
                    description=str(row["description"]),
                    status=row["status"],
                )
                for row in step_rows
            ],
        )

    def set_status(
        self,
        task_id: str,
        status: TaskStatus,
    ) -> DevelopmentTask:
        """! @brief 更新任务状态。

        @param task_id 任务唯一标识。
        @param status 新任务状态。
        @return 更新后的 DevelopmentTask。
        @exception TaskNotFoundError 当任务不存在时抛出。
        """
        now = _now_iso()

        with get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE tasks
                SET
                    status = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    status,
                    now,
                    task_id,
                ),
            )
            if cursor.rowcount == 0:
                raise TaskNotFoundError(f"任务不存在: {task_id}")

        return self.get_task(task_id)

    def add_event(
        self,
        task_id: str,
        sequence: int,
        event: AgentEvent,
    ) -> None:
        """! @brief 保存一条 Agent 事件。

        当前方法只负责把事件写入 agent_events 表，不参与接口流式返回逻辑。
        后续做 Tracing 时，可以在执行流中按需调用。

        @param task_id 任务 id。
        @param sequence 当前任务内的事件序号。
        @param event Agent 运行事件。
        """
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO agent_events (
                    task_id,
                    sequence,
                    event_type,
                    agent,
                    iteration,
                    message,
                    data_json,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    sequence,
                    event.type,
                    event.agent,
                    event.iteration,
                    event.message,
                    # SQLite 中以 TEXT 保存事件扩展数据。ensure_ascii=False
                    # 保留中文可读性，default=str 兼容 Path/时间等偶发值。
                    json.dumps(
                        event.data,
                        ensure_ascii=False,
                        default=str,
                    ),
                    _now_iso(),
                ),
            )

    def add_tool_call(
        self,
        task_id: str,
        agent: str,
        iteration: int,
        tool: str,
        arguments: dict[str, object],
        result_preview: str,
    ) -> None:
        """! @brief 保存一条工具调用记录。

        当前方法只负责把工具调用摘要写入 tool_calls 表。它不自动从
        AgentEvent 中推导字段，调用方需要显式传入工具名、参数和结果预览。

        @param task_id 任务 id。
        @param agent 发起工具调用的 Agent 名称。
        @param iteration 工具调用发生的 Agent 轮次。
        @param tool 工具名称。
        @param arguments 工具调用参数。
        @param result_preview 工具执行结果预览。
        """
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO tool_calls (
                    task_id,
                    agent,
                    iteration,
                    tool,
                    arguments_json,
                    result_preview,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    agent,
                    iteration,
                    tool,
                    # 参数完整保存用于追踪复现；工具结果只存上层传入的摘要，
                    # 避免大型文件内容或命令输出无限膨胀数据库。
                    json.dumps(
                        arguments,
                        ensure_ascii=False,
                        default=str,
                    ),
                    result_preview,
                    _now_iso(),
                ),
            )

    def get_events(
        self,
        task_id: str,
    ) -> list[dict[str, object]]:
        """! @brief 查询任务的 Agent 事件记录。

        用于给前端或调试页展示任务执行过程中的 SSE 事件历史。

        @param task_id 任务 id。
        @return Agent 事件记录列表，按 sequence 升序排列。
        """
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM agent_events
                WHERE task_id = ?
                ORDER BY sequence ASC
                """,
                (task_id,),
            ).fetchall()

        # 写入时保存为 JSON 文本，查询边界再还原成 Python dict，API 层
        # 不需要知道 SQLite 内部采用了 arguments_json/data_json 字段。
        return [
            {
                "sequence": row["sequence"],
                "type": row["event_type"],
                "agent": row["agent"],
                "iteration": row["iteration"],
                "message": row["message"],
                "data": json.loads(
                    row["data_json"]
                ),
                "created_at": row[
                    "created_at"
                ],
            }
            for row in rows
        ]

    def get_tool_calls(
        self,
        task_id: str,
    ) -> list[dict[str, object]]:
        """! @brief 查询任务的工具调用记录。

        @param task_id 任务 id。
        @return 工具调用记录列表，按写入顺序升序排列。
        """

        with get_connection() as conn:

            rows = conn.execute(
                """
                SELECT *
                FROM tool_calls
                WHERE task_id = ?
                ORDER BY id ASC
                """,
                (task_id,),
            ).fetchall()

        return [
            {
                "agent": row["agent"],
                "iteration": row[
                    "iteration"
                ],
                "tool": row["tool"],
                "arguments": json.loads(
                    row["arguments_json"]
                ),
                "result_preview": row[
                    "result_preview"
                ],
                "created_at": row[
                    "created_at"
                ],
            }
            for row in rows
        ]

    def set_source(
        self,
        task_id: str,
        source_type: str,
        source: dict[str, object],
    ) -> None:
        """! @brief 保存任务来源信息。

        例如 GitHub Issue 导入的任务，会把 owner/repo/issue_number/title/url
        存到 task_sources 表。后续查询任务详情时，就能知道这个任务来自哪里。

        @param task_id 任务 id。
        @param source_type 来源类型，例如 github_issue。
        @param source 来源详情字典。
        """

        with get_connection() as conn:

            # 每个任务只保留一个来源记录。重复导入或修正来源时，以 task_id
            # 主键覆盖旧值，使查询接口始终看到最新的一份来源元数据。
            conn.execute(
                """
                INSERT OR REPLACE INTO task_sources (
                    task_id,
                    source_type,
                    source_json
                )
                VALUES (?, ?, ?)
                """,
                (
                    task_id,
                    source_type,
                    json.dumps(
                        source,
                        ensure_ascii=False,
                    ),
                ),
            )

    def get_source(
        self,
        task_id: str,
    ) -> dict[str, object] | None:
        """! @brief 查询任务来源信息。

        @param task_id 任务 id。
        @return 来源信息字典；没有来源记录时返回 None。
        """

        with get_connection() as conn:

            row = conn.execute(
                """
                SELECT
                    source_type,
                    source_json
                FROM task_sources
                WHERE task_id = ?
                """,
                (task_id,),
            ).fetchone()

        if row is None:
            return None

        return {
            "source_type": row[
                "source_type"
            ],
            "source": json.loads(
                row["source_json"]
            ),
        }


task_repository = TaskRepository()
