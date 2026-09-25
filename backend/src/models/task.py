"""! @brief 开发任务领域模型。

本模块定义两段式任务执行流程中使用的数据模型：
- TaskStatus：任务生命周期状态；
- DevelopmentTask：任务主对象，包含仓库、问题、状态和计划步骤。
"""

from typing import Literal

from pydantic import BaseModel, Field

from ..models.agent_state import PlanStep


TaskStatus = Literal[
    # 计划已生成，等待用户人工检查和批准；此时不会修改代码。
    "awaiting_approval",
    # 预留状态：如果后续拆成 approve / execute 两步，可以使用该状态。
    "approved",
    # 用户触发 execute 后，任务进入实际执行阶段。
    "running",
    # Coder / Tester / Reviewer 流程全部正常结束。
    "completed",
    # 执行过程中出现 error 事件或异常。
    "failed",
    # 预留状态：后续支持取消任务时使用。
    "cancelled",
]


class DevelopmentTask(BaseModel):
    """! @brief DevPilot 开发任务。

    DevelopmentTask 是 `/api/tasks/plan` 和 `/api/tasks/{id}/execute`
    之间传递的核心对象。计划生成后任务进入 awaiting_approval，执行接口会
    根据状态切换到 running / completed / failed。
    """

    id: str = Field(
        description="任务唯一标识",
    )

    repo_path: str = Field(
        description="目标代码仓库路径",
    )

    question: str = Field(
        description="用户原始任务描述",
    )

    status: TaskStatus = (
        "awaiting_approval"
    )

    plan: list[PlanStep] = Field(
        default_factory=list,
        description="Planner 生成的任务执行计划",
    )
