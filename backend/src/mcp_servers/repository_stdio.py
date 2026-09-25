"""! @brief Repository MCP stdio 启动入口。

本模块用于以标准输入/输出模式启动 Repository MCP Server，方便被
支持 MCP 的客户端或 IDE 进程拉起。
"""
import os

from ..mcp_servers.repository_server import (
    create_repository_server,
)


def main() -> None:

    repo_path = os.getenv(
        "DEVPILOT_REPO_PATH"
    )

    if not repo_path:
        raise RuntimeError(
            "DEVPILOT_REPO_PATH 未设置"
        )

    mcp = create_repository_server(
        repo_path
    )

    # 默认 transport 就是 stdio
    mcp.run()


if __name__ == "__main__":
    main()