"""! @brief Adaptive Agent 的可执行工作流契约与运行进度。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class WorkflowContract:
    """! @brief 由 Agent 内核强制执行的任务工作流约束。"""

    require_code_change: bool = False
    require_pre_edit_reproducer: bool = False
    require_post_edit_reproducer: bool = False
    require_passing_test_after_edit: bool = False
    candidate_count: int = 1
    auto_replay_reproducer: bool = False


@dataclass
class WorkflowProgress:
    """! @brief 一次 Agent 运行中的机器可验证工作流证据。"""

    candidate_count_recorded: int = 0
    candidate_selected_index: int | None = None
    pre_edit_reproducer_signature: str | None = None
    pre_edit_reproducer_call: int = 0
    post_edit_reproducer_signature: str | None = None
    post_edit_reproducer_call: int = 0
    denied_writes: int = 0
    rejected_finals: int = 0
    automatic_replays: int = 0
    notices: list[str] = field(default_factory=list)

    def write_blockers(self, contract: WorkflowContract) -> list[str]:
        """! @brief 返回首次写入前尚未满足的约束。"""

        blockers: list[str] = []
        if (
            contract.require_pre_edit_reproducer
            and self.pre_edit_reproducer_signature is None
        ):
            blockers.append("缺少修改前失败的 reproducer 探针")
        if self.candidate_count_recorded < contract.candidate_count:
            blockers.append(
                f"修复候选不足：需要 {contract.candidate_count} 个，"
                f"当前 {self.candidate_count_recorded} 个"
            )
        return blockers

    def final_blockers(
        self,
        contract: WorkflowContract,
        *,
        has_changes: bool,
        last_edit_call: int,
        last_test_call: int,
        latest_test_passed: bool,
    ) -> list[str]:
        """! @brief 返回提交最终答案前尚未满足的约束。"""

        blockers: list[str] = []
        if contract.require_code_change and not has_changes:
            blockers.append("尚未产生代码修改")
        if has_changes and contract.require_post_edit_reproducer:
            if self.post_edit_reproducer_signature is None:
                blockers.append("缺少修改后同一 reproducer 的通过结果")
            elif (
                self.post_edit_reproducer_signature
                != self.pre_edit_reproducer_signature
            ):
                blockers.append("修改前后 reproducer 定义不一致")
            elif self.post_edit_reproducer_call <= last_edit_call:
                blockers.append("reproducer 证据早于最近一次修改")
        if has_changes and contract.require_passing_test_after_edit:
            if last_test_call <= last_edit_call:
                blockers.append("最近一次修改后尚未运行 run_test")
            elif not latest_test_passed:
                blockers.append("最近一次 run_test 未通过")
        return blockers

    def payload(
        self,
        contract: WorkflowContract,
        *,
        has_changes: bool,
        last_edit_call: int,
        last_test_call: int,
        latest_test_passed: bool,
    ) -> dict[str, Any]:
        """! @brief 构造终态事件与评测报告共享的合规数据。"""

        blockers = self.final_blockers(
            contract,
            has_changes=has_changes,
            last_edit_call=last_edit_call,
            last_test_call=last_test_call,
            latest_test_passed=latest_test_passed,
        )
        return {
            "contract": asdict(contract),
            "progress": asdict(self),
            "compliant": not blockers,
            "blockers": blockers,
        }
