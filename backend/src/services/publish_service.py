"""! @brief 发布预览服务。

本模块负责生成发布前人工审批所需的 PublishPreview：
收集 Git 改动文件、过滤敏感路径、读取待发布内容、计算快照 hash，
并组装 PR 分支和草稿信息。真正的 GitHub 写操作不在 create_preview 中执行。
"""
import hashlib
import subprocess
from pathlib import Path
from typing import cast

from ..mcp_clients.github_client import (
    call_github_write_tool_sync,
)
from ..models.publish import (
    PublishPreview,
)
from ..models.task import (
    DevelopmentTask,
)
from ..tools.file_tool import (
    resolve_safe_path,
)


PROTECTED_PUBLISH_FILES = {
    ".env",
    ".env.local",
    ".env.production",
}


PROTECTED_PREFIXES = {
    ".git/",
    ".devpilot/",
    "data/",
}


def get_changed_files(
    repo_path: str,
) -> list[str]:
    """! @brief 获取仓库中准备发布的修改和新增文件。

    只收集相对于 HEAD 的新增/修改文件，并过滤 .env、.git、.devpilot、
    data 等敏感或运行时目录。删除文件当前不在这里处理，由
    ensure_no_deleted_files() 单独拦截。

    @param repo_path 本地 Git 仓库路径。
    @return 可进入发布预览的相对文件路径列表。
    """

    repo = Path(
        repo_path
    ).resolve()

    # 已跟踪文件的修改
    diff_result = subprocess.run(
        [
            "git",
            "diff",
            "--name-only",
            "--diff-filter=AM",
            "HEAD",
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
        timeout=15,
    )

    modified = [
        line.strip()
        for line
        in diff_result.stdout.splitlines()
        if line.strip()
    ]

    # 未跟踪的新文件
    untracked_result = subprocess.run(
        [
            "git",
            "ls-files",
            "--others",
            "--exclude-standard",
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
        timeout=15,
    )

    untracked = [
        line.strip()
        for line
        in untracked_result.stdout.splitlines()
        if line.strip()
    ]

    # 同一路径可能同时出现在不同 Git 查询结果中，先去重再排序，保证预览
    # 文件顺序稳定，从而使后续快照 hash 不受 Git 输出顺序影响。
    files = sorted(
        set(
            modified
            + untracked
        )
    )

    safe_files: list[str] = []

    for file_path in files:

        normalized = (
            file_path
            .replace("\\", "/")
        )

        if (
            Path(normalized).name
            in PROTECTED_PUBLISH_FILES
        ):
            continue

        if any(
            normalized.startswith(
                prefix
            )
            for prefix
            in PROTECTED_PREFIXES
        ):
            continue

        safe_files.append(
            normalized
        )

    return safe_files


def ensure_no_deleted_files(
    repo_path: str,
) -> None:
    """! @brief 阻止当前发布流程处理删除文件。

    删除文件的 PR 风险更高，容易误删配置或关键代码。当前阶段先禁止，
    等后续有更完整的 diff 审批界面后再放开。

    @param repo_path 本地 Git 仓库路径。
    @raise RuntimeError 检测到删除文件时抛出。
    """

    repo = Path(
        repo_path
    ).resolve()

    result = subprocess.run(
        [
            "git",
            "diff",
            "--name-only",
            "--diff-filter=D",
            "HEAD",
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
        timeout=15,
    )

    deleted = [
        x.strip()
        for x
        in result.stdout.splitlines()
        if x.strip()
    ]

    if deleted:
        raise RuntimeError(
            "当前发布流程暂不支持删除文件: "
            + ", ".join(deleted)
        )


def collect_publish_files(
    repo_path: str,
    files: list[str],
) -> list[dict[str, str]]:
    """! @brief 读取待发布文件内容。

    只允许普通文本文件进入发布预览；二进制文件或过大的文件会被拒绝，
    避免自动发布阶段上传不可审查内容。

    @param repo_path 本地 Git 仓库路径。
    @param files 待发布的相对文件路径列表。
    @return 形如 {"path": "...", "content": "..."} 的文件快照列表。
    @raise RuntimeError 文件不存在、过大或不是 UTF-8 文本时抛出。
    """

    repo = Path(
        repo_path
    ).resolve()

    result: list[
        dict[str, str]
    ] = []

    for file_path in files:

        path = resolve_safe_path(
            repo,
            file_path,
        )

        if not path.is_file():
            raise RuntimeError(
                f"不是文件: {file_path}"
            )

        if (
            path.stat().st_size
            > 500_000
        ):
            raise RuntimeError(
                (
                    "文件过大，不允许自动发布: "
                    f"{file_path}"
                )
            )

        try:

            content = path.read_text(
                encoding="utf-8",
                errors="strict",
            )

        except UnicodeDecodeError as exc:

            raise RuntimeError(
                (
                    "当前阶段不支持自动发布二进制文件: "
                    f"{file_path}"
                )
            ) from exc

        result.append({
            "path": file_path,
            "content": content,
        })

    return result


def calculate_snapshot_hash(
    files: list[
        dict[str, str]
    ],
) -> str:
    """! @brief 计算待发布文件快照 hash。

    hash 同时包含文件路径和文件内容。用户批准后，如果发布前文件又被改动，
    可以重新计算 hash 并与预览中的 snapshot_hash 比较，防止审批内容和
    实际发布内容不一致。

    @param files collect_publish_files() 返回的文件快照列表。
    @return SHA-256 十六进制摘要。
    """

    digest = hashlib.sha256()

    # 按路径排序使 hash 与调用方传入列表的顺序无关。
    for item in sorted(
        files,
        key=lambda x: x["path"],
    ):

        digest.update(
            item["path"].encode(
                "utf-8"
            )
        )

        # NUL 作为字段边界，避免 (path="ab", content="c") 与
        # (path="a", content="bc") 这类直接拼接产生相同字节串。
        digest.update(b"\0")

        digest.update(
            item["content"].encode(
                "utf-8"
            )
        )

        digest.update(b"\0")

    return digest.hexdigest()


def make_branch_name(
    task_id: str,
) -> str:
    """! @brief 根据任务 ID 生成 DevPilot 发布分支名。

    @param task_id DevPilot 任务 ID。
    @return 形如 devpilot/task-xxxxxxxxxx 的分支名。
    """

    return (
        "devpilot/"
        f"task-{task_id[:10]}"
    )


class PublishService:
    """! @brief 发布预览业务服务。"""

    def create_preview(
        self,
        task_id: str,
        repo_path: str,
        owner: str,
        repo: str,
        base_branch: str,
        pr_title: str,
        pr_body: str,
        commit_message: str,
    ) -> PublishPreview:
        """! @brief 创建发布前审批预览。

        此方法只生成预览，不推送 GitHub。调用方应先把返回的
        PublishPreview 保存到 PublishRepository，再展示给用户确认。

        @param task_id DevPilot 任务 ID。
        @param repo_path 本地 Git 仓库路径。
        @param owner GitHub owner。
        @param repo GitHub 仓库名称。
        @param base_branch PR 目标分支。
        @param pr_title PR 标题。
        @param pr_body PR 正文。
        @param commit_message 建议提交信息。
        @return 发布前审批预览。
        @raise RuntimeError 没有可发布文件、存在删除文件或文件不安全时抛出。
        """

        ensure_no_deleted_files(
            repo_path
        )

        changed_files = (
            get_changed_files(
                repo_path
            )
        )

        if not changed_files:
            raise RuntimeError(
                "没有检测到需要发布的代码修改"
            )

        publish_files = (
            collect_publish_files(
                repo_path,
                changed_files,
            )
        )

        snapshot_hash = (
            calculate_snapshot_hash(
                publish_files
            )
        )

        return PublishPreview(
            task_id=task_id,
            owner=owner,
            repo=repo,
            base_branch=base_branch,
            head_branch=(
                make_branch_name(
                    task_id
                )
            ),
            commit_message=(
                commit_message
            ),
            pr_title=pr_title,
            pr_body=pr_body,
            files=changed_files,
            snapshot_hash=(
                snapshot_hash
            ),
        )

    def verify_snapshot(
        self,
        task: DevelopmentTask,
        preview: PublishPreview,
    ) -> list[dict[str, str]]:
        """! @brief 发布前重新校验待发布文件快照。

        该校验是人工审批流程的关键安全边界：用户看到的是 preview 生成时
        的文件列表和内容 hash。如果审批之后本地代码又发生变化，就必须
        重新生成发布预览并重新审批，不能继续使用旧 preview 发布。

        @param task DevPilot 任务对象，提供本地仓库路径。
        @param preview 用户批准的发布预览。
        @return 当前待发布文件内容；hash 与 preview 一致时才返回。
        @raise RuntimeError 当前文件快照与 preview.snapshot_hash 不一致时抛出。
        """

        current_files = collect_publish_files(
            task.repo_path,
            preview.files,
        )

        current_hash = calculate_snapshot_hash(
            current_files
        )

        if current_hash != preview.snapshot_hash:
            raise RuntimeError(
                (
                    "代码在发布审批后发生了变化。"
                    "请重新生成 Publish Preview 并再次审批。"
                )
            )

        return current_files

    def publish(
        self,
        task: DevelopmentTask,
        preview: PublishPreview,
        current_files: list[dict[str, str]] | None = None,
    ) -> dict[str, object]:
        """! @brief 将已审批的发布预览真正推送到 GitHub。

        流程：
        1. 再次校验 snapshot，防止审批后代码变化；
        2. 创建 DevPilot 发布分支；
        3. 把 preview 中批准的文件内容推送到分支；
        4. 创建 draft Pull Request。

        @param task DevPilot 任务对象。
        @param preview 已处于 awaiting_approval 的发布预览。
        @param current_files 已校验过的文件快照；为空时本方法会自行校验。
        @return GitHub 写工具返回结果摘要。
        @raise RuntimeError snapshot 不一致或 GitHub 写操作失败时抛出。
        """

        # 路由层可把刚校验过的 current_files 传进来，避免同一次请求重复
        # 读取磁盘；直接调用服务时则由服务自己完成安全校验。
        files_to_publish = (
            current_files
            if current_files is not None
            else self.verify_snapshot(
                task,
                preview,
            )
        )

        # GitHub 操作必须保持顺序：后两步分别依赖分支已存在、文件已推送。
        # 任一步抛异常都会阻止后续调用，并由 API 层把预览状态置为 failed。
        branch_result = cast(
            object,
            call_github_write_tool_sync(
                "create_branch",
                {
                    "owner": preview.owner,
                    "repo": preview.repo,
                    "branch": preview.head_branch,
                    "from_branch": preview.base_branch,
                },
            ),
        )

        push_result = cast(
            object,
            call_github_write_tool_sync(
                "push_files",
                {
                    "owner": preview.owner,
                    "repo": preview.repo,
                    "branch": preview.head_branch,
                    "message": preview.commit_message,
                    "files": files_to_publish,
                },
            ),
        )

        pr_result = cast(
            object,
            call_github_write_tool_sync(
                "create_pull_request",
                {
                    "owner": preview.owner,
                    "repo": preview.repo,
                    "base": preview.base_branch,
                    "head": preview.head_branch,
                    "title": preview.pr_title,
                    "body": preview.pr_body,
                    "draft": True,
                },
            ),
        )


        return {
            "branch": preview.head_branch,
            "create_branch": branch_result,
            "push_files": push_result,
            "pull_request": pr_result,
        }


# 全局单例：FastAPI 路由层直接复用，避免每次请求重复创建服务对象。
publish_service = PublishService()
