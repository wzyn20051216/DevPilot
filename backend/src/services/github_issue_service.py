from typing import Any

from ..mcp_clients.github_client import (
    call_github_tool_sync,
)

from ..models.github import (
    GitHubIssueContext,
)


class GitHubIssueService:
    """! @brief 读取并标准化 GitHub Issue。

    本服务负责隔离 GitHub MCP 的原始返回格式：上层只拿
    `GitHubIssueContext`，不用关心 issue_read 的参数和响应细节。
    """

    def fetch(
        self,
        owner: str,
        repo: str,
        issue_number: int,
    ) -> GitHubIssueContext:
        """! @brief 从 GitHub 拉取 Issue 主体和评论。

        @param owner 仓库所属 GitHub 用户或组织名。
        @param repo GitHub 仓库名称。
        @param issue_number Issue 编号。
        @return 标准化后的 Issue 上下文。
        @raise RuntimeError GitHub MCP 返回的 Issue 主体不是字典时抛出。
        """

        # 读取 Issue 主体信息：标题、正文、状态、URL 等。
        issue = call_github_tool_sync(
            "issue_read",
            {
                "method": "get",
                "owner": owner,
                "repo": repo,
                "issue_number": (
                    issue_number
                ),
            },
        )

        # 再读取评论，后续会拼进开发任务上下文，让 Planner 能看到讨论过程。
        comments = (
            call_github_tool_sync(
                "issue_read",
                {
                    "method": (
                        "get_comments"
                    ),
                    "owner": owner,
                    "repo": repo,
                    "issue_number": (
                        issue_number
                    ),
                    "perPage": 50,
                },
            )
        )

        if not isinstance(
            issue,
            dict,
        ):
            preview = str(issue)[:500]
            raise RuntimeError(
                (
                    "GitHub Issue 返回格式异常："
                    f"type={type(issue).__name__}, preview={preview}"
                )
            )

        comment_items: list[
            dict[str, Any]
        ] = []

        # GitHub MCP 理论上返回 list[dict]，这里做一次防御性过滤，
        # 避免异常元素污染后续 prompt。
        if isinstance(comments, list):

            comment_items = [
                item
                for item in comments
                if isinstance(
                    item,
                    dict,
                )
            ]

        return GitHubIssueContext(
            owner=owner,
            repo=repo,
            issue_number=issue_number,
            title=str(
                issue.get(
                    "title",
                    "",
                )
            ),
            body=str(
                issue.get(
                    "body",
                    "",
                )
                or ""
            ),
            state=str(
                issue.get(
                    "state",
                    "",
                )
            ),
            url=str(
                issue.get(
                    "html_url",
                    issue.get(
                        "url",
                        "",
                    ),
                )
            ),
            comments=comment_items,
            raw=issue,
        )


github_issue_service = (
    GitHubIssueService()
)


def build_development_request(
    issue: GitHubIssueContext,
) -> str:
    """! @brief 把 GitHub Issue 转成 DevPilot 可执行的开发任务描述。

    @param issue 标准化后的 GitHub Issue 上下文。
    @return 面向 Planner/Agent 的中文开发任务 prompt。
    """

    # 评论保留原始字典文本，便于 Agent 看到作者、时间、正文等完整上下文。
    comments_text = "\n\n".join(
        (
            f"Comment {index}:\n"
            f"{comment}"
        )
        for index, comment
        in enumerate(
            issue.comments,
            start=1,
        )
    )

    return f"""
你正在处理一个真实 GitHub Issue。

Repository:
{issue.owner}/{issue.repo}

Issue:
#{issue.issue_number}

Title:
{issue.title}

Description:
{issue.body}

Discussion:
{comments_text or "No comments"}

请结合当前本地代码仓库：
1. 理解 Issue 的真实需求；
2. 定位相关代码；
3. 制定最小修改方案；
4. 明确需要运行哪些测试；
5. 不要修改与 Issue 无关的代码。
""".strip()
