"""外部服务、Agent 循环和 Sandbox 的故障注入测试。"""

import asyncio
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Self, cast

import pytest
from pytest import MonkeyPatch

from backend.src import llm_client
from backend.src.agents import base_tool_agent
from backend.src.agents.base_tool_agent import BaseToolAgent
from backend.src.agents.orchestrator import DevPilotOrchestrator
from backend.src.exceptions import ExternalServiceError
from backend.src.mcp_clients import repository_client
from backend.src.models.agent_state import AgentEvent
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


def _tool_call(
    arguments: str = "{}",
    name: str = "read_file",
) -> SimpleNamespace:
    """构造一个 function tool call。"""

    return SimpleNamespace(
        id="call-1",
        type="function",
        function=SimpleNamespace(name=name, arguments=arguments),
    )


class _FakeCompletions:
    """按顺序返回响应或抛出异常的 completions stub。"""

    def __init__(self, outcomes: list[Any]) -> None:
        self.outcomes = iter(outcomes)
        self.requests: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)
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


def test_agent_forces_final_answer_after_tool_iteration_limit(
    monkeypatch: MonkeyPatch,
) -> None:
    """工具轮数耗尽后应禁用工具并请求一次最终回答。"""

    agent = _build_agent(
        monkeypatch,
        [
            _response(tool_calls=[_tool_call()]),
            _response(content="finalized"),
        ],
        max_iterations=1,
    )
    monkeypatch.setattr(base_tool_agent, "execute_tool", lambda **_: {"ok": True})
    events = list(agent.run_stream("question"))

    assert events[-1].type == "final"
    assert events[-1].message == "finalized"
    completions = cast(Any, agent.client.chat.completions)
    assert completions.requests[-1]["tool_choice"] == "none"
    assert events[-1].data["usage"]["total_tokens"] == 10


def test_run_test_event_exposes_structured_report(
    monkeypatch: MonkeyPatch,
) -> None:
    """run_test 事件应携带可供 Orchestrator 兜底的测试事实。"""

    agent = _build_agent(
        monkeypatch,
        [
            _response(tool_calls=[_tool_call(name="run_test")]),
            _response(content='{"passed": true}'),
        ],
    )
    agent.allowed_tools = {"run_test"}
    monkeypatch.setattr(
        base_tool_agent,
        "execute_tool",
        lambda **_: {
            "passed": True,
            "returncode": 0,
            "stdout": "x" * 5_000,
            "stderr": "",
            "timed_out": False,
        },
    )

    events = list(agent.run_stream("test"))
    tool_result = next(event for event in events if event.type == "tool_result")

    assert tool_result.data["test_report"]["passed"] is True
    assert tool_result.data["test_report"]["stdout"] == "x" * 4_000


def test_tester_falls_back_to_run_test_result() -> None:
    """最终 JSON 损坏时，Orchestrator 应使用 run_test 的真实结果。"""

    class FakeTester:
        def run_stream(self, _: str) -> Any:
            yield AgentEvent(
                type="tool_result",
                agent="tester",
                message="run_test 执行完成",
                data={
                    "tool": "run_test",
                    "test_report": {
                        "passed": True,
                        "summary": "pytest returncode=0",
                        "stdout": "1 passed",
                        "stderr": "",
                    },
                },
            )
            yield AgentEvent(
                type="final",
                agent="tester",
                message="{not valid json",
            )

    orchestrator = cast(
        DevPilotOrchestrator,
        cast(object, SimpleNamespace(tester=FakeTester())),
    )
    _, report = DevPilotOrchestrator._run_tester(orchestrator, "test")

    assert report is not None
    assert report.passed is True
    assert report.stdout == "1 passed"


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
