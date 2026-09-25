"""! @brief 发布预览数据仓储模块。

PublishRepository 负责保存和查询发布前审批数据，例如目标分支、
PR 标题/正文、待发布文件列表、快照 hash、发布状态和最终 PR URL。
它只操作 SQLite，不直接调用 GitHub；真正发布动作交给 service 层处理。
"""
import json
from sqlite3 import Row
from datetime import UTC, datetime
from typing import cast

from ..models.publish import (
    PublishPreview,
    PublishStatus,
)
from .connection import get_connection


def _now_iso() -> str:
    """! @brief 获取 UTC ISO 时间字符串。

    @return 当前 UTC 时间，精确到秒。
    """

    return datetime.now(
        UTC
    ).replace(
        microsecond=0
    ).isoformat()


class PublishRepository:
    """! @brief 基于 SQLite 的发布预览仓储。

    该仓储和 TaskRepository 写法保持一致：对外暴露小而明确的方法，
    上层无需直接拼 SQL，也不会把 SQLite 表结构泄漏到业务代码里。
    """

    def save_preview(
        self,
        preview: PublishPreview,
    ) -> PublishPreview:
        """! @brief 新增或覆盖保存一个发布预览。

        使用 task_id 作为主键；同一个任务重复生成预览时，会用最新内容覆盖旧记录。

        @param preview 发布前审批预览模型。
        @return 保存后的 PublishPreview。
        """

        now = _now_iso()

        with get_connection() as conn:
            # task_id 是主键，重复生成 Publish Preview 时用新快照整体替换旧值。
            # 这也会把状态恢复为 preview 当前值（通常是 awaiting_approval）。
            _ = conn.execute(
                """
                INSERT OR REPLACE INTO publish_previews (
                    task_id,
                    owner,
                    repo,
                    base_branch,
                    head_branch,
                    commit_message,
                    pr_title,
                    pr_body,
                    files_json,
                    snapshot_hash,
                    status,
                    pr_url,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    preview.task_id,
                    preview.owner,
                    preview.repo,
                    preview.base_branch,
                    preview.head_branch,
                    preview.commit_message,
                    preview.pr_title,
                    preview.pr_body,
                    # SQLite 没有 list 类型，因此文件路径列表以 JSON TEXT 保存；
                    # snapshot_hash 只校验内容，files_json 则用于发布时重新读文件。
                    json.dumps(
                        preview.files,
                        ensure_ascii=False,
                    ),
                    preview.snapshot_hash,
                    preview.status,
                    preview.pr_url,
                    now,
                    now,
                ),
            )

        return self.get_preview(
            preview.task_id
        )

    def get_preview(
        self,
        task_id: str,
    ) -> PublishPreview:
        """! @brief 查询某个任务的发布预览。

        @param task_id 任务 ID。
        @return 对应的 PublishPreview。
        @raise ValueError 该任务没有发布预览时抛出。
        """

        with get_connection() as conn:
            # sqlite3 的类型桩无法根据 row_factory 推断 fetchone() 返回 Row。
            # cast 只帮助 basedpyright 收窄类型，运行时不会转换或复制数据。
            row = cast(
                Row | None,
                conn.execute(
                    """
                    SELECT
                        task_id,
                        owner,
                        repo,
                        base_branch,
                        head_branch,
                        commit_message,
                        pr_title,
                        pr_body,
                        files_json,
                        snapshot_hash,
                        status,
                        pr_url
                    FROM publish_previews
                    WHERE task_id = ?
                    """,
                    (task_id,),
                ).fetchone(),
            )

        if row is None:
            raise ValueError(
                f"发布预览不存在: {task_id}"
            )

        # 先把 SQLite 动态值收窄成 str，再反序列化为列表；最终逐项 str()
        # 是为了给旧数据留出兼容空间，并交由 PublishPreview 做最终校验。
        files_json = cast(
            str,
            row["files_json"],
        )
        files = cast(
            list[str],
            json.loads(files_json),
        )

        return PublishPreview(
            task_id=cast(str, row["task_id"]),
            owner=cast(str, row["owner"]),
            repo=cast(str, row["repo"]),
            base_branch=cast(
                str,
                row["base_branch"],
            ),
            head_branch=cast(
                str,
                row["head_branch"],
            ),
            commit_message=cast(
                str,
                row["commit_message"],
            ),
            pr_title=cast(str, row["pr_title"]),
            pr_body=cast(str, row["pr_body"]),
            files=[
                str(item)
                for item in files
            ],
            snapshot_hash=cast(
                str,
                row["snapshot_hash"]
            ),
            status=cast(
                PublishStatus,
                row["status"],
            ),
            pr_url=cast(str, row["pr_url"]),
        )

    def set_status(
        self,
        task_id: str,
        status: PublishStatus,
    ) -> PublishPreview:
        """! @brief 更新发布预览状态。

        @param task_id 任务 ID。
        @param status 新发布状态。
        @return 更新后的 PublishPreview。
        @raise ValueError 该任务没有发布预览时抛出。
        """

        with get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE publish_previews
                SET
                    status = ?,
                    updated_at = ?
                WHERE task_id = ?
                """,
                (
                    status,
                    _now_iso(),
                    task_id,
                ),
            )

            # UPDATE 未命中意味着预览不存在。显式报错可以区分“状态没变”与
            # “调用方传错 task_id”，避免发布接口假装更新成功。
            if cursor.rowcount == 0:
                raise ValueError(
                    f"发布预览不存在: {task_id}"
                )

        return self.get_preview(task_id)

    def set_pr_url(
        self,
        task_id: str,
        pr_url: str,
    ) -> PublishPreview:
        """! @brief 保存 GitHub Pull Request URL。

        该方法只写 PR URL，不自动修改 status。调用方可以根据发布流程，
        再显式调用 set_status(task_id, "published")。

        @param task_id 任务 ID。
        @param pr_url GitHub PR 网页 URL。
        @return 更新后的 PublishPreview。
        @raise ValueError 该任务没有发布预览时抛出。
        """

        with get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE publish_previews
                SET
                    pr_url = ?,
                    updated_at = ?
                WHERE task_id = ?
                """,
                (
                    pr_url,
                    _now_iso(),
                    task_id,
                ),
            )

            if cursor.rowcount == 0:
                raise ValueError(
                    f"发布预览不存在: {task_id}"
                )

        return self.get_preview(task_id)


publish_repository = PublishRepository()
