"""! @brief 运行服务保存的评测结果数据结构。"""

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

EvaluationVariant = Literal["single_no_rag", "single_rag", "multi_no_rag", "multi_rag", "single_enhanced", "single_adaptive"]

class EvaluationResult(BaseModel):
    """! @brief 一次 variant 运行产生的标准化评测结果。"""

    run_id: str = Field(description="同一批实验共享的运行标识")
    case_id: str
    variant: EvaluationVariant
    repeat_index: int = Field(default=1, ge=1, description="同一配置的重复实验序号")
    success: bool
    tests_passed: bool = Field(description="独立 Verifier 的真实测试结果")
    tool_calls: int = Field(default=0, ge=0)
    iterations: int = Field(default=0, ge=0)
    repair_rounds: int = Field(default=0, ge=0)
    elapsed_seconds: float = Field(default=0.0, ge=0)
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    llm_seconds: float = Field(default=0.0, ge=0)
    tool_seconds: float = Field(default=0.0, ge=0)
    estimated_cost: float = Field(default=0.0, ge=0)
    workspace_path: str
    error: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
