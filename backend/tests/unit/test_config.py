"""类型化配置测试。"""

import pytest

from backend.src.config import Settings


def test_cors_origins_are_normalized() -> None:
    """CORS 字符串应去除空格和空项。"""

    config = Settings(
        _env_file=None,
        cors_origins="http://localhost:5173, ,http://localhost:8080",
    )
    assert config.cors_origin_list == [
        "http://localhost:5173",
        "http://localhost:8080",
    ]


def test_validate_llm_reports_missing_fields() -> None:
    """真正调用模型前应一次性列出缺失的关键配置。"""

    config = Settings(_env_file=None, llm_api_key="", llm_model="")
    with pytest.raises(RuntimeError, match="LLM_API_KEY, LLM_MODEL"):
        config.validate_llm()


def test_external_service_limits_are_validated() -> None:
    """外部服务边界必须为正数，重试次数不能无限增长。"""

    with pytest.raises(ValueError):
        Settings(_env_file=None, llm_timeout_seconds=0)
    with pytest.raises(ValueError):
        Settings(_env_file=None, llm_max_retries=11)
    with pytest.raises(ValueError):
        Settings(_env_file=None, mcp_timeout_seconds=-1)
