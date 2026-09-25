"""! @brief GitHub MCP Client。

本模块通过 stdio 启动 GitHub 官方 MCP Server Docker 镜像，
并封装 tool discovery 与 tool call。当前默认启用只读模式，
用于把 GitHub Issue 导入为 DevPilot 开发任务。
"""
import asyncio
import os
from typing import Any

from mcp import (
    Client,
    StdioServerParameters,
)

from ..config import settings


def create_github_server_params(
) -> StdioServerParameters:
    """! @brief 创建 GitHub 官方 MCP Server 的 stdio 启动参数。

    GitHub 官方 MCP Server 以 Docker 容器运行。这里通过环境变量把
    token、只读开关和启用的 toolsets 传进容器。

    @return 可直接传给 MCP Client 的 stdio server 参数。
    @raise RuntimeError 未配置 GitHub token 时抛出。
    """

    if not settings.github_token:
        raise RuntimeError(
            "GITHUB_PERSONAL_ACCESS_TOKEN 未配置"
        )

    return StdioServerParameters(
        command="docker",
        args=[
            "run",
            "-i",
            "--rm",

            # Docker 容器内需要这些环境变量；具体值从下面 env 字典传入。
            "-e",
            "GITHUB_PERSONAL_ACCESS_TOKEN",

            "-e",
            "GITHUB_READ_ONLY",

            "-e",
            "GITHUB_TOOLSETS",

            "ghcr.io/github/github-mcp-server",
        ],
        env={
            # docker 命令只会把 `-e` 指定的变量转交容器；这里的 env 则是
            # docker 客户端进程自身看到的值，两者配合才完成 token 注入。
            **os.environ,
            "GITHUB_PERSONAL_ACCESS_TOKEN": (
                settings.github_token
            ),
            # 当前阶段只读取 Issue/评论，不允许 MCP Server 写 GitHub。
            "GITHUB_READ_ONLY": "1",
            "GITHUB_TOOLSETS": (
                "repos,issues,pull_requests"
            ),
        },
    )


async def list_github_tools(
) -> list[str]:
    """! @brief 查看 GitHub MCP 当前提供哪些工具。

    @return GitHub MCP Server 暴露的工具名称列表。
    """

    params = (
        create_github_server_params()
    )

    # GitHub 官方 MCP Server 当前按传统 initialize 流程工作。
    # 显式使用 legacy，避免 SDK auto discovery 触发 duplicate initialize。
    async with Client(params, mode="legacy") as client:

        result = await client.list_tools()

        return [
            tool.name
            for tool in result.tools
        ]


def list_github_tools_sync(
) -> list[str]:
    """! @brief 同步查看 GitHub MCP 工具列表。

    供 FastAPI 同步接口、REPL 或临时测试脚本直接调用。

    @return GitHub MCP Server 暴露的工具名称列表。
    """

    return asyncio.run(
        list_github_tools()
    )


async def call_github_tool(
    tool_name: str,
    arguments: dict[str, Any],
) -> Any:
    """! @brief 调用一个 GitHub MCP Tool。

    @param tool_name GitHub MCP 工具名，例如 issue_read。
    @param arguments 传给 MCP 工具的参数字典。
    @return 工具执行结果；优先返回 structured_content。
    """

    params = (
        create_github_server_params()
    )

    # GitHub 官方 MCP Server 当前按传统 initialize 流程工作。
    # 显式使用 legacy，避免 SDK auto discovery 触发 duplicate initialize。
    async with Client(params, mode="legacy") as client:

        result = await client.call_tool(
            tool_name,
            arguments,
        )

        # 新版 MCP 工具一般会提供 structured_content，优先返回结构化结果，
        # 方便上层按 dict/list 处理，而不是再解析文本。
        if (
            result.structured_content
            is not None
        ):
            structured = (
                result.structured_content
            )

            if (
                isinstance(
                    structured,
                    dict,
                )
                and set(
                    structured.keys()
                ) == {"result"}
            ):
                return structured[
                    "result"
                ]

            return structured

        # 兜底兼容：若工具只返回文本块，则拼接成一个字符串。
        text_parts: list[str] = []

        for block in result.content:

            text = getattr(
                block,
                "text",
                None,
            )

            if isinstance(text, str):
                text_parts.append(text)

        return "\n".join(
            text_parts
        )


def call_github_tool_sync(
    tool_name: str,
    arguments: dict[str, Any],
) -> Any:
    """! @brief 同步调用 GitHub MCP Tool。

    @param tool_name GitHub MCP 工具名。
    @param arguments 传给 MCP 工具的参数字典。
    @return 工具执行结果。
    """

    return asyncio.run(
        call_github_tool(
            tool_name,
            arguments,
        )
    )


def create_github_write_server_params(
) -> StdioServerParameters:
    """! @brief 创建 GitHub 写操作 MCP Server 的 stdio 启动参数。

    该配置只开放 create_branch / push_files / create_pull_request 三个工具，
    用于发布阶段，避免把 GitHub MCP 的全部写能力暴露给业务流程。

    @return 可直接传给 MCP Client 的 stdio server 参数。
    @raise RuntimeError 未配置 GitHub token 时抛出。
    """

    if not settings.github_token:
        raise RuntimeError(
            "GITHUB_PERSONAL_ACCESS_TOKEN 未配置"
        )

    # 使用环境副本隔离只读与写入两套 MCP 配置，避免某次发布请求永久修改
    # Web 进程的全局环境，进而影响后续 Issue 读取请求。
    env = dict(os.environ)

    env[
        "GITHUB_PERSONAL_ACCESS_TOKEN"
    ] = settings.github_token

    # 非常重要：
    # 避免继承之前的 READ_ONLY 配置。
    _ = env.pop(
        "GITHUB_READ_ONLY",
        None,
    )

    # 只开放我们真正需要的三个写工具
    env["GITHUB_TOOLS"] = (
        "create_branch,"
        "push_files,"
        "create_pull_request"
    )

    return StdioServerParameters(
        command="docker",
        args=[
            "run",
            "-i",
            "--rm",

            "-e",
            "GITHUB_PERSONAL_ACCESS_TOKEN",

            "-e",
            "GITHUB_TOOLS",

            "ghcr.io/github/github-mcp-server",
        ],
        env=env,
    )


async def list_github_write_tools(
) -> list[str]:
    """! @brief 查看 GitHub 写 MCP Server 暴露的工具列表。

    @return GitHub 写工具名称列表。
    """

    params = (
        create_github_write_server_params()
    )

    async with Client(params, mode="legacy") as client:

        result = await client.list_tools()

        return [
            tool.name
            for tool in result.tools
        ]


def list_github_write_tools_sync(
) -> list[str]:
    """! @brief 同步查看 GitHub 写工具列表。

    @return GitHub 写工具名称列表。
    """

    return asyncio.run(
        list_github_write_tools()
    )


async def call_github_write_tool(
    tool_name: str,
    arguments: dict[str, Any],
) -> Any:
    """! @brief 调用一个受限 GitHub 写工具。

    @param tool_name 工具名，只允许 create_branch / push_files / create_pull_request。
    @param arguments 传给 MCP 工具的参数字典。
    @return 工具执行结果；优先返回 structured_content。
    @raise PermissionError 工具名不在写工具白名单内时抛出。
    """

    # 这里是客户端侧第二道白名单。即使容器配置意外暴露了更多写工具，
    # 业务代码仍不能借此调用删除仓库、合并 PR 等未授权能力。
    allowed = {
        "create_branch",
        "push_files",
        "create_pull_request",
    }

    if tool_name not in allowed:
        raise PermissionError(
            f"禁止调用 GitHub 写工具: {tool_name}"
        )

    params = (
        create_github_write_server_params()
    )

    async with Client(params, mode="legacy") as client:

        result = await client.call_tool(
            tool_name,
            arguments,
        )

        if (
            result.structured_content
            is not None
        ):
            structured = (
                result.structured_content
            )

            if (
                isinstance(
                    structured,
                    dict,
                )
                and set(
                    structured.keys()
                ) == {"result"}
            ):
                # 与只读调用保持同一返回约定，去掉 SDK 的单字段包装层。
                return structured[
                    "result"
                ]

            return structured

        text_parts: list[str] = []

        for block in result.content:

            text = getattr(
                block,
                "text",
                None,
            )

            if isinstance(text, str):
                text_parts.append(text)

        return "\n".join(
            text_parts
        )


def call_github_write_tool_sync(
    tool_name: str,
    arguments: dict[str, Any],
) -> Any:
    """! @brief 同步调用受限 GitHub 写工具。

    @param tool_name GitHub 写工具名。
    @param arguments 传给 MCP 工具的参数字典。
    @return 工具执行结果。
    """

    # 发布接口是同步路由；该包装函数负责临时运行异步 MCP 客户端。
    # 若未来改为 async 路由，应直接 await call_github_write_tool()。
    return asyncio.run(
        call_github_write_tool(
            tool_name,
            arguments,
        )
    )
