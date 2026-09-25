"""! @brief RAG 检索工具对 Agent 探索效率影响的 A/B 实验脚本。

本脚本使用同一个 BaseToolAgent、同一个问题和同一个仓库，只替换工具白名单：
- A 组：list_files + search_code + read_files
- B 组：retrieve_code + read_files

脚本会记录：
- 工具调用次数；
- Agent 迭代轮数；
- 发现或读取到的相关文件；
- 总响应时间；
- 最终回答和事件摘要。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from src.agents.base_tool_agent import BaseToolAgent
from src.tools.retrieval_tool import retrieve_code


REPO_PATH = Path(r"E:\desktop\DevPilot\backend")
QUESTION = "分析 LLM 初始化和配置加载关系"

SYSTEM_PROMPT = """
你是一个代码仓库分析实验 Agent。

你必须先使用可用工具查找证据，再回答问题。
请分析：LLM 客户端如何初始化，配置从哪里加载，配置对象和调用方之间是什么关系。

最终回答必须包含：
1. 相关文件列表；
2. 关键调用链；
3. 简短结论。
"""


def _extract_files_from_result(result: Any) -> set[str]:
    """! @brief 从工具结果中提取文件路径。

    @param result 工具返回值，可以是 list、dict 或字符串。
    @return 结果中出现的文件路径集合。
    """
    files: set[str] = set()

    if isinstance(result, list):
        for item in result:
            if isinstance(item, str):
                files.add(item)
            elif isinstance(item, dict):
                value = item.get("file_path")
                if isinstance(value, str):
                    files.add(value)
    elif isinstance(result, dict):
        value = result.get("file_path")
        if isinstance(value, str):
            files.add(value)

    return files


def run_group(
    name: str,
    allowed_tools: set[str],
) -> dict[str, Any]:
    """! @brief 运行单组 Agent 实验并汇总指标。

    @param name 实验组名称。
    @param allowed_tools 当前组允许使用的工具集合。
    @return 包含工具调用、轮次、耗时、相关文件和最终回答的实验结果。
    """
    agent = BaseToolAgent(
        repo_path=str(REPO_PATH),
        name="planner",
        system_prompt=SYSTEM_PROMPT,
        allowed_tools=allowed_tools,
        max_iterations=8,
    )

    events: list[dict[str, Any]] = []
    tool_calls: list[dict[str, Any]] = []
    relevant_files: set[str] = set()
    final_answer = ""
    max_iteration = 0

    start = time.perf_counter()
    for event in agent.run_stream(QUESTION):
        event_data = event.model_dump()
        events.append(event_data)
        max_iteration = max(max_iteration, int(event.iteration or 0))

        if event.type == "tool_call":
            tool_calls.append(
                {
                    "iteration": event.iteration,
                    "tool": event.data.get("tool"),
                    "arguments": event.data.get("arguments", {}),
                }
            )
            arguments = event.data.get("arguments", {})
            if isinstance(arguments, dict):
                file_path = arguments.get("file_path")
                if isinstance(file_path, str):
                    relevant_files.add(file_path)

        elif event.type == "tool_result":
            result = event.data.get("result_preview")
            # BaseToolAgent 为节省上下文只给 result_preview，这里记录预览中的明显文件名。
            if isinstance(result, str):
                for marker in (
                    "src/config.py",
                    "src/llm_client.py",
                    "src/agents/base_tool_agent.py",
                    "src/agents/planner_agent.py",
                    "src/main.py",
                ):
                    if marker in result:
                        relevant_files.add(marker)

        elif event.type == "final":
            final_answer = event.message

    elapsed = time.perf_counter() - start

    return {
        "group": name,
        "question": QUESTION,
        "allowed_tools": sorted(allowed_tools),
        "tool_call_count": len(tool_calls),
        "iterations": max_iteration,
        "relevant_file_count": len(relevant_files),
        "relevant_files": sorted(relevant_files),
        "elapsed_seconds": round(elapsed, 3),
        "tool_calls": tool_calls,
        "final_answer": final_answer,
        "events": events,
    }


def main() -> None:
    """! @brief 执行 A/B 实验并保存 JSON 报告。"""
    output_dir = Path("ai_team")
    output_dir.mkdir(exist_ok=True)

    # 预热 retrieve_code，避免把 embedding 模型首次加载时间计入 B 组。
    # 这样更接近长期运行的 Agent 服务进程，也更能衡量检索策略本身。
    retrieve_code(
        repo_path=str(REPO_PATH),
        query=QUESTION,
        top_k=1,
    )

    results = [
        run_group(
            name="A",
            allowed_tools={
                "list_files",
                "search_code",
                "read_files",
            },
        ),
        run_group(
            name="B",
            allowed_tools={
                "retrieve_code",
                "read_files",
            },
        ),
    ]

    report = {
        "repo_path": str(REPO_PATH),
        "question": QUESTION,
        "results": results,
    }

    output_path = output_dir / "rag_ab_experiment_result.json"
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    summary = [
        {
            "group": item["group"],
            "tool_call_count": item["tool_call_count"],
            "iterations": item["iterations"],
            "relevant_file_count": item["relevant_file_count"],
            "relevant_files": item["relevant_files"],
            "elapsed_seconds": item["elapsed_seconds"],
            "tools": [call["tool"] for call in item["tool_calls"]],
        }
        for item in results
    ]
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"报告已保存: {output_path}")


if __name__ == "__main__":
    main()
