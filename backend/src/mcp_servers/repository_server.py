"""! @brief Repository MCP Server。

本模块把 DevPilot 现有的仓库工具包装为 MCP tools。
当前使用 MCP Python SDK 2.x 的 MCPServer，而不是旧版 FastMCP。
"""
from typing import Any

from mcp.server import MCPServer

from ..tools.file_tool import (
    list_files as local_list_files,
    read_files as local_read_file,
    search_code as local_search_code,
)

from ..tools.git_tool import (
    git_diff as local_git_diff,
)

from ..tools.retrieval_tool import (
    retrieve_code as local_retrieve_code,
)


def create_repository_server(
    repo_path: str,
) -> MCPServer:
    """! @brief 为指定代码仓库创建 Repository MCP Server。

    这里通过闭包绑定 repo_path，让 MCP 客户端调用 list_files/read_file 等
    工具时不需要每次传仓库路径，只需要传工具自身参数。

    @param repo_path 被 MCP tools 操作的代码仓库根目录。
    @return 已注册仓库工具的 MCPServer 实例。
    """

    mcp = MCPServer(
        "devpilot_repository_mcp"
    )

    @mcp.tool()
    def list_files(
        max_files: int = 200,
    ) -> list[str]:
        """! @brief 列出当前代码仓库中的文件。

        @param max_files 最多返回的文件数量。
        @return 仓库内文件的相对路径列表。
        """

        return local_list_files(
            repo_path=repo_path,
            max_files=max_files,
        )

    @mcp.tool()
    def read_file(
        file_path: str,
        max_chars: int = 20_000,
    ) -> str:
        """! @brief 读取当前代码仓库中的文本文件。

        @param file_path 相对于仓库根目录的文件路径。
        @param max_chars 最多返回的字符数。
        @return 文件文本内容，超长时由底层工具截断。
        """

        return local_read_file(
            repo_path=repo_path,
            file_path=file_path,
            max_chars=max_chars,
        )

    @mcp.tool()
    def search_code(
        keyword: str,
        max_results: int = 50,
    ) -> list[dict[str, Any]]:
        """! @brief 在当前代码仓库中搜索关键词。

        @param keyword 需要搜索的关键词。
        @param max_results 最多返回的匹配文件数量。
        @return 匹配文件及命中行列表。
        """

        return local_search_code(
            repo_path=repo_path,
            keyword=keyword,
            max_results=max_results,
        )

    @mcp.tool()
    def retrieve_code(
        query: str,
        top_k: int = 8,
    ) -> list[dict[str, Any]]:
        """! @brief 使用 Hybrid RAG 检索最相关代码片段。

        @param query 语义检索问题。
        @param top_k 返回的代码片段数量。
        @return 相关代码片段及分数。
        """

        return local_retrieve_code(
            repo_path=repo_path,
            query=query,
            top_k=top_k,
        )

    @mcp.tool()
    def git_diff(
        file_path: str | None = None,
    ) -> dict[str, Any]:
        """! @brief 查看当前代码仓库的 Git Diff。

        @param file_path 可选文件路径；为空时返回整个仓库 diff。
        @return Git diff 执行结果。
        """

        return local_git_diff(
            repo_path=repo_path,
            file_path=file_path,
        )

    return mcp
