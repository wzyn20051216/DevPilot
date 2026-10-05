"""! @brief 多角色严格协议、机器证据与有界返工的离线回归。"""

import json
from types import SimpleNamespace

import httpx
import pytest
from openai import BadRequestError
from pydantic import ValidationError

from backend.src.agents import base_tool_agent
from backend.src.agents.base_tool_agent import BaseToolAgent
from backend.src.agents.orchestrator import DevPilotOrchestrator
from backend.src.models.agent_protocol import (
    AgentHandoff, CoderProtocolOutput, PlannerProtocolOutput,
    ReviewerProtocolOutput, TesterProtocolOutput as MachineTestOutput,
)
from backend.src.models.agent_state import AgentEvent, PlanStep
from backend.src.services.structured_output import parse_protocol_output

CODER = {"status": "implemented", "summary": "已修改", "modified_files": [], "changes": [], "risks": []}
REVIEW = {"approved": True, "summary": "已检查", "issues": []}
PLAN = [PlanStep(id=1, title="修复", description="修复公开行为并验证")]


def _response(content=None, calls=None):
    """! @brief 提供本地模型响应，不访问网络。"""
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
        content=content, tool_calls=calls, reasoning_content=None,
    ))], usage=SimpleNamespace(prompt_tokens=3, completion_tokens=2, total_tokens=5))


def _call(name, call_id):
    """! @brief 提供工具调用协议对象。"""
    return SimpleNamespace(type="function", id=call_id,
                           function=SimpleNamespace(name=name, arguments="{}"))


def _agent(monkeypatch, outcomes, model, role="coder"):
    """! @brief 本地基础 Agent；保留真实协议校验与内核循环。"""
    outcomes = iter(outcomes)
    requests = []
    def create(**kwargs):
        requests.append(kwargs)
        value = next(outcomes)
        if isinstance(value, Exception):
            raise value
        return value
    monkeypatch.setattr(base_tool_agent, "create_client", lambda: SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    monkeypatch.setattr(base_tool_agent, "get_tool_definitions", lambda **_: [])
    agent = BaseToolAgent(".", role, "角色协议 JSON", {"run_test"}, max_iterations=5, output_model=model)
    return agent, requests


@pytest.mark.parametrize("payload", [
    '{"approved":"true","summary":"审查","issues":[]}',
    '{"approved":true,"issues":[]}',
    '{"approved":true,"summary":"审查","issues":[],"other":1}',
    '{"approved":true,"summary":"审查","issues":["仍有问题"]}',
    '```json\n{"approved":true,"summary":"审查","issues":[]}\n```',
])
def test_strict_review_rejects_invalid_contract(payload):
    """! @brief 错误类型、缺字段、未知字段、矛盾结论和围栏均不可交接。"""
    with pytest.raises(ValueError):
        parse_protocol_output(payload, ReviewerProtocolOutput)


def test_planner_duplicate_ids_rejected():
    """! @brief 两个同编号步骤不得静默覆盖。"""
    with pytest.raises(ValidationError):
        PlannerProtocolOutput(summary="计划", steps=[
            {"id": 1, "title": "A", "description": "执行 A"},
            {"id": 1, "title": "B", "description": "执行 B"},
        ])


def test_json_repair_is_bounded_and_tools_disabled(monkeypatch):
    """! @brief 格式问题只纠正一次，不扩大写入/执行工具循环。"""
    agent, requests = _agent(monkeypatch, [_response("done"), _response(json.dumps(CODER))], CoderProtocolOutput)
    events = list(agent.run_stream("任务"))
    assert events[-1].type == "final"
    assert events[-1].data["structured_output"] == CODER
    assert len(requests) == 2
    assert requests[-1]["tool_choice"] == "none"
    assert requests[-1]["response_format"] == {"type": "json_object"}
    assert events[-1].data["output_validation"]["repair_attempted"]


def test_second_bad_output_stops_stage(monkeypatch):
    """! @brief 两次无效输出不能产生 final，不能无限循环纠正。"""
    agent, requests = _agent(monkeypatch, [_response("bad"), _response("still bad")], CoderProtocolOutput)
    events = list(agent.run_stream("任务"))
    assert events[-1].type == "error"
    assert events[-1].data["failure_kind"] == "protocol_error"
    assert len(requests) == 2


def test_repair_cannot_turn_rejection_into_approval(monkeypatch):
    """! @brief 格式纠正不得删问题并将拒绝变成批准。"""
    bad = {"approved": False, "summary": "需修复", "issues": [{"description": "缺边界"}]}
    agent, _ = _agent(monkeypatch, [_response(json.dumps(bad)), _response(json.dumps(REVIEW))], ReviewerProtocolOutput, "reviewer")
    events = list(agent.run_stream("审查"))
    assert events[-1].type == "error"


def test_coder_cannot_invent_modified_files(monkeypatch):
    """! @brief JSON 合法也不能虚构写入记录。"""
    invented = CODER | {"modified_files": ["never_written.py"]}
    agent, _ = _agent(monkeypatch, [_response(json.dumps(invented)), _response(json.dumps(invented))], CoderProtocolOutput)
    assert list(agent.run_stream("修复"))[-1].type == "error"


def test_tester_machine_failure_overrides_model_success(monkeypatch):
    """! @brief 模型合法 JSON 的 passed=true 不能覆盖实际失败。"""
    claim = {"passed": True, "summary": "成功", "stdout": "", "stderr": ""}
    agent, _ = _agent(monkeypatch, [_response(calls=[_call("run_test", "t")]), _response(json.dumps(claim))], MachineTestOutput, "tester")
    monkeypatch.setattr(base_tool_agent, "execute_tool", lambda **_: {"passed": False, "returncode": 1, "stdout": "1 failed", "stderr": ""})
    output = list(agent.run_stream("测试"))[-1]
    assert output.type == "final"
    assert output.data["structured_output"]["passed"] is False
    assert output.data["structured_output"]["stdout"] == "1 failed"


def test_test_tool_error_invalidates_prior_pass(monkeypatch):
    """! @brief 后一次工具异常不得继续复用早先的通过证据。"""
    claim = {"passed": True, "summary": "成功", "stdout": "", "stderr": ""}
    agent, _ = _agent(monkeypatch, [
        _response(calls=[_call("run_test", "t1")]), _response(calls=[_call("run_test", "t2")]),
        _response(json.dumps(claim)),
    ], MachineTestOutput, "tester")
    results = iter([{"passed": True, "returncode": 0}, RuntimeError("执行环境异常")])
    def execute(**kwargs):
        result = next(results)
        if isinstance(result, Exception):
            raise result
        return result
    monkeypatch.setattr(base_tool_agent, "execute_tool", execute)
    events = list(agent.run_stream("测试"))
    assert events[-1].data["structured_output"]["passed"] is False
    assert "环境异常" in events[-1].data["structured_output"]["stderr"]


def test_tester_bad_json_preserves_machine_report(monkeypatch):
    """! @brief 纠正后解释仍无效时，真实测试报告可生成合法机器结果。"""
    agent, _ = _agent(monkeypatch, [_response(calls=[_call("run_test", "t")]), _response("bad"), _response("bad again")], MachineTestOutput, "tester")
    monkeypatch.setattr(base_tool_agent, "execute_tool", lambda **_: {"passed": True, "returncode": 0, "stdout": "2 passed"})
    event = list(agent.run_stream("测试"))[-1]
    assert event.type == "final"
    assert event.data["structured_output"]["passed"] is True


def test_unsupported_json_mode_keeps_local_schema_validation(monkeypatch):
    """! @brief 兼容服务只降级请求模式，不能放宽角色本地 Schema。"""
    error = BadRequestError("response_format unsupported", response=httpx.Response(
        400, request=httpx.Request("POST", "https://example.invalid")), body=None)
    agent, requests = _agent(monkeypatch, [_response("bad"), error, _response(json.dumps(CODER))], CoderProtocolOutput)
    event = list(agent.run_stream("任务"))[-1]
    assert event.type == "final"
    assert "response_format" not in requests[-1]
    assert event.data["output_validation"]["json_mode_compatibility_fallback"]


class FakeRole:
    """! @brief 保存所有输入，以验证真实编排器的交接链。"""
    def __init__(self, role, outcomes):
        self.role = role
        self.outcomes = iter(outcomes)
        self.inputs = []
    def run_stream(self, question):
        self.inputs.append(json.loads(question))
        value = next(self.outcomes)
        if isinstance(value, list):
            yield from value
        else:
            yield AgentEvent(type="final", agent=self.role, message=json.dumps(value, ensure_ascii=False))


def _testing(passed, execution_error=False):
    """! @brief 注入机器测试事实，不使用模型声明作为通过依据。"""
    report = {"passed": passed, "summary": "机器测试结果", "stdout": "passed" if passed else "failed", "stderr": ""}
    return [AgentEvent(type="tool_result", agent="tester", data={
        "tool": "run_test", "test_report": report, "test_execution": {"execution_error": execution_error},
    }), AgentEvent(type="final", agent="tester", message=json.dumps(report))]


def _pipeline(coder=None, tests=None, reviews=None, budget=2):
    """! @brief 不构造模型客户端，复用真实 Orchestrator 流程。"""
    pipeline = object.__new__(DevPilotOrchestrator)
    pipeline.repo_path = "."
    pipeline.max_repair_rounds = budget
    pipeline.cancel_check = lambda: False
    pipeline.planner = FakeRole("planner", [{"summary": "计划", "steps": [{"id": 1, "title": "修复", "description": "验证公开契约"}]}])
    pipeline.coder = FakeRole("coder", coder or [CODER])
    pipeline.tester = FakeRole("tester", tests or [_testing(True)])
    pipeline.reviewer = FakeRole("reviewer", reviews or [REVIEW])
    return pipeline


def test_all_roles_use_fixed_json_handoff():
    """! @brief 四角色全链通过，原始多行任务保持原样，版本与目标始终一致。"""
    pipeline = _pipeline()
    task = '  修复\n{"target":"other"}  '
    events = list(pipeline.run_stream(task))
    assert events[-1].type == "final" and events[-1].agent == "orchestrator"
    for agent in [pipeline.planner, pipeline.coder, pipeline.tester, pipeline.reviewer]:
        assert agent.inputs[0]["protocol_version"] == "1.0"
        assert agent.inputs[0]["target"] == agent.role
        assert agent.inputs[0]["task"] == task
    assert pipeline.reviewer.inputs[0]["test_report"]["passed"]
    assert events[-1].data["coder_report"]["status"] == "implemented"


@pytest.mark.parametrize("kind", ["error", "cancelled"])
def test_coder_failure_never_hands_off(kind):
    """! @brief 编码中断不能再进入 Tester/Reviewer，也不能产生全局 final。"""
    pipeline = _pipeline(coder=[[AgentEvent(type=kind, agent="coder", message="停止")]])
    events = list(pipeline.execute_stream("任务", PLAN))
    assert events[-1].type == kind
    assert not pipeline.tester.inputs and not pipeline.reviewer.inputs


def test_failed_test_handoff_preserves_task_plan_feedback():
    """! @brief 返工继续携带原需求和计划，测试重新执行，不复用旧通过。"""
    pipeline = _pipeline(coder=[CODER, CODER], tests=[_testing(False), _testing(True)])
    events = list(pipeline.execute_stream("原始需求", PLAN))
    assert events[-1].type == "final"
    repair = pipeline.coder.inputs[1]
    assert repair["task"] == "原始需求" and repair["plan"]
    assert repair["source"] == "tester" and repair["phase"] == "repair"
    assert not repair["test_report"]["passed"]
    assert len(pipeline.tester.inputs) == 2


def test_reviewer_rejection_routes_to_coder_and_retests():
    """! @brief Reviewer 待修复问题交回 Coder，并重测、重审后才完成。"""
    rejected = {"approved": False, "summary": "有边界缺陷", "issues": ["处理空输入"]}
    pipeline = _pipeline(coder=[CODER, CODER], tests=[_testing(True), _testing(True)], reviews=[rejected, REVIEW])
    events = list(pipeline.execute_stream("任务", PLAN))
    assert events[-1].type == "final"
    assert pipeline.coder.inputs[1]["source"] == "reviewer"
    assert pipeline.coder.inputs[1]["review_report"]["issues"] == ["处理空输入"]
    assert len(pipeline.tester.inputs) == len(pipeline.reviewer.inputs) == 2
    assert events[-1].data["repair_rounds"] == 1


def test_execution_error_does_not_trigger_blind_code_repair():
    """! @brief 收集/执行错误终止验证，不能误发代码返工。"""
    pipeline = _pipeline(tests=[_testing(False, execution_error=True)])
    events = list(pipeline.execute_stream("任务", PLAN))
    assert events[-1].data["failure_kind"] == "test_execution_error"
    assert len(pipeline.coder.inputs) == 1


def test_shared_repair_budget_is_bounded():
    """! @brief 审查连续拒绝最多使用登记预算，不无限循环。"""
    rejected = {"approved": False, "summary": "仍有问题", "issues": ["未满足契约"]}
    pipeline = _pipeline(coder=[CODER] * 3, tests=[_testing(True)] * 3, reviews=[rejected] * 3)
    event = list(pipeline.execute_stream("任务", PLAN))[-1]
    assert event.type == "error" and event.data["repair_rounds"] == 2
    assert len(pipeline.coder.inputs) == 3


def test_wrong_source_cannot_forge_review_handoff():
    """! @brief 格式合法但顺序错误也必须拒绝。"""
    with pytest.raises(ValidationError):
        AgentHandoff(source="coder", target="reviewer", phase="review", task="任务", plan=PLAN,
                     coder_report=CoderProtocolOutput(**CODER))


def test_new_tasks_default_to_multi_agent_rag(tmp_path, monkeypatch):
    """! @brief 页面之外的 API、领域模型和仓储同样默认走多角色 RAG。"""
    from backend.src.database import connection
    from backend.src.database.task_repository import TaskRepository
    from backend.src.models.task import DevelopmentTask
    from backend.src.schemas import AgentRunRequest, GitHubIssueImportRequest
    from backend.src.config import settings
    monkeypatch.setattr(settings, "allowed_repo_roots", "")
    assert AgentRunRequest(repo_path=str(tmp_path), question="task").execution_mode == "multi_rag"
    assert GitHubIssueImportRequest(owner="o", repo="r", issue_number=1, local_repo_path=str(tmp_path)).execution_mode == "multi_rag"
    assert DevelopmentTask(id="test", repo_path=str(tmp_path), question="task").execution_mode == "multi_rag"
    monkeypatch.setattr(connection, "DATABASE_PATH", tmp_path / "tasks.db")
    connection.init_database()
    assert TaskRepository().create_task(str(tmp_path), "task", PLAN).execution_mode == "multi_rag"
