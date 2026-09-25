"""! @brief Evals 数据模型。

模型只描述稳定的评测输入和输出，不包含 Agent 执行逻辑，便于后续把同一份
结果用于命令行报告、API 查询或论文实验统计。
"""

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field


EvaluationVariant = Literal[
    "single_no_rag",
    "single_rag",
    "multi_no_rag",
    "multi_rag",
]

BenchmarkDifficulty = Literal["easy", "medium", "hard"]
BenchmarkCategory = Literal[
    "bugfix",
    "feature",
    "refactor",
    "validation",
    "configuration",
    "cross_module",
]


class BenchmarkCase(BaseModel):
    """! @brief 一条可重复执行的基准测试用例。"""

    id: str = Field(min_length=1, description="用例唯一标识")
    title: str = Field(min_length=1, description="用例标题")
    description: str = Field(default="", description="用例背景说明")
    difficulty: BenchmarkDifficulty = Field(description="用例难度等级")
    category: BenchmarkCategory = Field(description="用例任务类别")
    tags: list[str] = Field(
        default_factory=list,
        description="用于更细粒度实验切片的标签",
    )
    repo_fixture: str = Field(min_length=1, description="benchmarks/repos 下的仓库目录名")
    task: str = Field(min_length=1, description="发送给 Agent 的开发任务")
    verification_target: str | None = Field(
        default=None,
        description="独立验证时传给 pytest 的目标路径",
    )
    timeout_seconds: int = Field(default=120, gt=0, description="独立验证超时秒数")


class EvaluationResult(BaseModel):
    """! @brief 一次 variant 运行产生的标准化评测结果。"""

    run_id: str = Field(description="同一批实验共享的运行标识")
    case_id: str
    variant: EvaluationVariant
    success: bool
    tests_passed: bool = Field(description="独立 Verifier 的真实测试结果")
    tool_calls: int = Field(default=0, ge=0)
    iterations: int = Field(default=0, ge=0)
    repair_rounds: int = Field(default=0, ge=0)
    elapsed_seconds: float = Field(default=0.0, ge=0)
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    workspace_path: str
    error: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
