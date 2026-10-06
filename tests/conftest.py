"""! @brief 测试会话默认关闭鉴权与仓库白名单，避免受本机 .env 影响。

测试断言的是公开行为，不应依赖开发者本机 backend/.env 里是否配置了
API_KEYS / ALLOWED_REPO_ROOTS。这里在会话开始时把这两个开关清空，
需要验证鉴权/白名单的用例再自行 monkeypatch 打开。
"""

from pydantic import SecretStr


def pytest_configure() -> None:
    from backend.src.config import settings

    settings.api_keys = SecretStr("")
    settings.allowed_repo_roots = ""
