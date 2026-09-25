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
    mcp_timeout_seconds: float = Field(default=30.0, gt=0)

    github_personal_access_token: str | None = None

    database_path: Path = BACKEND_ROOT / "data" / "devpilot.db"
    sandbox_image: str = "devpilot-sandbox:py312"
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
