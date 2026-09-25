"""外部服务、Agent 循环和 Sandbox 的故障注入测试。"""

import asyncio
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Self

import pytest
from pytest import MonkeyPatch

from backend.src import llm_client
from backend.src.agents import base_tool_agent
from backend.src.agents.base_tool_agent import BaseToolAgent
from backend.src.exceptions import ExternalServiceError
from backend.src.mcp_clients import repository_client
from backend.src.sandbox import docker_runner


def _response(
    *,
    content: str | None = None,
    tool_calls: list[Any] | None = None,
) -> SimpleNamespace:
    """构造 BaseToolAgent 需要的最小 OpenAI 兼容响应。"""

    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    usage = SimpleNamespace(prompt_tokens=3, completion_tokens=2, total_tokens=5)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message)],
        usage=usage,
    )


def _tool_call(arguments: str = "{}") -> SimpleNamespace:
    """构造一个 function tool call。"""

    return SimpleNamespace(
        id="call-1",
        type="function",
        function=SimpleNamespace(name="read_file", arguments=arguments),
    )


class _FakeCompletions:
    """按顺序返回响应或抛出异常的 completions stub。"""

    def __init__(self, outcomes: list[Any]) -> None:
        self.outcomes = iter(outcomes)

    def create(self, **_: Any) -> Any:
        outcome = next(self.outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def test_openai_client_uses_configured_limits(monkeypatch: MonkeyPatch) -> None:
    """客户端构造必须真正传入超时和重试设置。"""

    captured: dict[str, Any] = {}

    def fake_openai(**kwargs: Any) -> object:
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(llm_client, "OpenAI", fake_openai)
    monkeypatch.setattr(llm_client.settings, "llm_api_key", "test-key")
    monkeypatch.setattr(llm_client.settings, "llm_model", "test-model")
    monkeypatch.setattr(llm_client.settings, "llm_timeout_seconds", 12.5)
    monkeypatch.setattr(llm_client.settings, "llm_max_retries", 4)

    _ = llm_client.create_client()

    assert captured["timeout"] == 12.5
    assert captured["max_retries"] == 4


def _build_agent(monkeypatch: MonkeyPatch, outcomes: list[Any], max_iterations: int = 2) -> BaseToolAgent:
    """创建不联网、不启动 MCP 的基础 Agent。"""

    client = SimpleNamespace(chat=SimpleNamespace(completions=_FakeCompletions(outcomes)))
    monkeypatch.setattr(base_tool_agent, "create_client", lambda: client)
    monkeypatch.setattr(base_tool_agent, "get_tool_definitions", lambda **_: [])
    return BaseToolAgent(
        repo_path=".",
        name="coder",
        system_prompt="test",
        allowed_tools={"read_file"},
        max_iterations=max_iterations,
    )


def test_agent_turns_llm_failure_into_error_event(monkeypatch: MonkeyPatch) -> None:
    """LLM 异常应结束为可观测 error 事件，而不是逃出生成器。"""

    agent = _build_agent(monkeypatch, [TimeoutError("model timeout")])
    events = list(agent.run_stream("question"))

    assert [event.type for event in events] == ["start", "thinking", "error"]
    assert events[-1].message == "model timeout"


def test_agent_can_recover_after_tool_failure(monkeypatch: MonkeyPatch) -> None:
    """工具异常应作为 observation 返回模型，使下一轮仍可正常完成。"""

    agent = _build_agent(
        monkeypatch,
        [_response(tool_calls=[_tool_call("not-json")]), _response(content="recovered")],
    )

    def fail_tool(**_: Any) -> Any:
        raise OSError("tool unavailable")

    monkeypatch.setattr(base_tool_agent, "execute_tool", fail_tool)
    events = list(agent.run_stream("question"))

    assert events[-1].type == "final"
    assert events[-1].message == "recovered"
    tool_result = next(event for event in events if event.type == "tool_result")
    assert tool_result.data["arguments"] == {}
    assert "tool unavailable" in str(tool_result.data["result_preview"])


def test_agent_reports_iteration_exhaustion(monkeypatch: MonkeyPatch) -> None:
    """模型持续请求工具时必须在上限处停止。"""

    agent = _build_agent(
        monkeypatch,
        [_response(tool_calls=[_tool_call()])],
        max_iterations=1,
    )
    monkeypatch.setattr(base_tool_agent, "execute_tool", lambda **_: {"ok": True})
    events = list(agent.run_stream("question"))

    assert events[-1].type == "error"
    assert "最大迭代次数" in events[-1].message


def test_sandbox_timeout_is_returned_as_structured_result(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Docker 超时应返回 timed_out，供 Agent 决定是否修复或终止。"""

    monkeypatch.setattr(docker_runner.shutil, "which", lambda _: "docker")

    def timeout(*_: Any, **__: Any) -> Any:
        raise subprocess.TimeoutExpired(cmd="docker", timeout=1)

    monkeypatch.setattr(docker_runner.subprocess, "run", timeout)
    result = docker_runner.run_in_sandbox(
        repo_path=str(tmp_path),
        argv=["pytest", "-q"],
        timeout=1,
    )

    assert result["timed_out"] is True
    assert result["returncode"] is None
    assert "超过 1 秒" in result["stderr"]


@pytest.mark.asyncio
async def test_repository_mcp_timeout_becomes_domain_error(
    monkeypatch: MonkeyPatch,
) -> None:
    """MCP 子进程失去响应时应在配置期限内返回稳定业务异常。"""

    class SlowClient:
        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def list_tools(self) -> Any:
            await asyncio.sleep(1)
            return SimpleNamespace(tools=[])

    monkeypatch.setattr(repository_client, "Client", lambda _: SlowClient())
    monkeypatch.setattr(repository_client.settings, "mcp_timeout_seconds", 0.01)

    with pytest.raises(ExternalServiceError, match="工具发现超过"):
        await repository_client.list_repository_tools(".")
