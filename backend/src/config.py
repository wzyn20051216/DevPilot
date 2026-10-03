"""! @brief DevPilot 全局配置模型。

所有运行时配置统一从环境变量或 ``backend/.env`` 读取。模块导入时只解析
配置，不强制要求 LLM/GitHub 密钥存在；具体能力在真正使用前再进行校验，
因此健康检查、离线测试和只读页面可以在无 Secret 环境下正常启动。
"""

from functools import lru_cache
from pathlib import Path
from typing import ClassVar, Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = BACKEND_ROOT / ".env"


class Settings(BaseSettings):
    """! @brief DevPilot 的类型化全局配置。"""

    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "DevPilot"
    app_env: Literal["development", "test", "production"] = "development"
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"

    llm_api_key: str = ""
    llm_base_url: str | None = None
    llm_model: str = ""
    llm_timeout_seconds: float = Field(default=60.0, gt=0)
    llm_max_retries: int = Field(default=2, ge=0, le=10)
    agent_token_budget: int = Field(
        default=0,
        ge=0,
        description="单个 Agent 的 Token 上限；0 表示不限制",
    )
    tool_observation_max_chars: int = Field(default=10_000, ge=2_000)
    agent_recent_messages: int = Field(
        default=12,
        ge=4,
        description="上下文压缩时完整保留的最近消息数量",
    )
    agent_history_summary_max_chars: int = Field(default=6_000, ge=1_000)
    agent_dedupe_observations: bool = Field(
        default=True,
        description="对完全一致的重复工具观测去重，改写成紧凑指针以降低 Token；False 时关闭",
    )
    llm_prompt_cost_per_million: float = Field(default=0.0, ge=0)
    llm_completion_cost_per_million: float = Field(default=0.0, ge=0)
    mcp_timeout_seconds: float = Field(default=30.0, gt=0)

    # --- 动态 RAG 策略（技术手册 10.2.3）---
    rag_mode: Literal["manual", "auto"] = Field(
        default="manual",
        description=(
            "manual 保持既有显式 enable_rag 行为不变；"
            "auto 时由 rag.policy 按仓库规模与查询歧义决定是否启用 RAG"
        ),
    )
    rag_auto_small_repo_files: int = Field(
        default=120,
        ge=1,
        description="源码文件数不超过该值视为小仓库；小仓库检索收益低，auto 模式默认不启用 RAG",
    )
    rag_auto_large_repo_files: int = Field(
        default=2_000,
        ge=1,
        description="源码文件数达到该值视为大仓库，auto 模式默认启用 RAG",
    )
    rag_auto_query_signal_chars: int = Field(
        default=60,
        ge=1,
        description="查询歧义度判定参考阈值：问题描述短于该值且缺少定位信号时视为歧义查询",
    )

    # --- Agent 上下文断点恢复（技术手册 10.1）---
    agent_checkpoint_enabled: bool = Field(
        default=True,
        description="是否把 Agent 对话上下文持久化到 SQLite，供服务重启后恢复继续执行",
    )
    agent_checkpoint_max_messages: int = Field(
        default=400,
        ge=10,
        description="单条上下文检查点保存的最大消息数，超出时保留最近消息",
    )

    # --- 任务队列与 Sandbox Worker 分离（技术手册 10.2.5）---
    task_queue_backend: Literal["inline", "sqlite", "redis"] = Field(
        default="inline",
        description=(
            "inline 维持进程内后台线程；sqlite/redis 时 API 只入队，"
            "由独立 Worker 进程领取执行（租约+心跳+幂等）"
        ),
    )
    redis_url: str = Field(default="", description="task_queue_backend=redis 时的 Redis 连接地址")
    task_worker_lease_seconds: int = Field(
        default=300,
        ge=30,
        description="Worker 领取任务后的租约时长；心跳会在到期前续租",
    )
    task_worker_heartbeat_seconds: int = Field(
        default=30,
        ge=5,
        description="Worker 心跳续租间隔，必须小于租约时长",
    )
    task_worker_poll_seconds: float = Field(
        default=1.0,
        gt=0,
        description="Worker 轮询队列的间隔秒数",
    )
    task_max_attempts: int = Field(
        default=3,
        ge=1,
        description="任务最大尝试次数，超过后标记为死信（dead）",
    )

    github_personal_access_token: str | None = None

    database_path: Path = BACKEND_ROOT / "data" / "devpilot.db"
    sandbox_image: str = "devpilot-sandbox:py312"
    sandbox_image_polyglot: str = Field(
        default="devpilot-sandbox:polyglot",
        description="含 Node 22 + tsx + Temurin JDK 22 的沙箱镜像，用于非 Python benchmark",
    )
    workspace_root: Path = Path("/workspace")
    host_workspace_root: Path | None = None

    cors_origins: str = (
        "http://localhost:5173,"
        "http://127.0.0.1:5173,"
        "http://localhost:8080,"
        "http://127.0.0.1:8080"
    )

    @property
    def github_token(self) -> str | None:
        """! @brief 保留旧调用方使用的 GitHub Token 属性名。"""

        return self.github_personal_access_token

    @property
    def cors_origin_list(self) -> list[str]:
        """! @brief 把逗号分隔的 CORS 配置转换为来源列表。"""

        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    def validate_llm(self) -> None:
        """! @brief 在真正调用模型前校验必填 LLM 配置。"""

        missing: list[str] = []
        if not self.llm_api_key:
            missing.append("LLM_API_KEY")
        if not self.llm_model:
            missing.append("LLM_MODEL")
        if missing:
            raise RuntimeError("缺少 LLM 配置: " + ", ".join(missing))

@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """! @brief 返回进程内缓存的配置单例。"""

    return Settings()


settings = get_settings()
