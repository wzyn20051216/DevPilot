"""! @brief API Key 校验与仓库目录授权，共享给请求层和执行层。"""

import hmac
from pathlib import Path

from fastapi import Request

from .config import settings
from .exceptions import AuthenticationError, ToolPermissionError


def require_api_key(request: Request) -> None:
    """! @brief 校验业务请求的 Bearer 或 X-API-Key，不记录密钥。"""

    if request.url.path in {"/healthz", "/readyz"}:
        return
    keys = settings.api_key_list
    if not keys:
        return
    authorization = request.headers.get("Authorization", "")
    if authorization:
        scheme, _, token = authorization.partition(" ")
        supplied = token.strip() if scheme.lower() == "bearer" else ""
    else:
        supplied = request.headers.get("X-API-Key", "").strip()
    # bytes 比较兼容非 ASCII 输入；逐个比较避免匹配位置导致提前返回。
    matched = False
    for key in keys:
        matched |= hmac.compare_digest(supplied.encode("utf-8"), key.encode("utf-8"))
    if not supplied or not matched:
        raise AuthenticationError("请提供有效的 API Key")


def validate_repo_path(value: str) -> str:
    """! @brief 解析路径与符号链接，拒绝白名单以外的目录。

    @return 配置白名单时返回规范绝对路径；未配置时保持兼容行为。
    @exception ValueError 路径不合法或超出授权范围。
    """

    roots = settings.allowed_repo_root_list
    if not roots:
        return value
    try:
        resolved = Path(value).expanduser().resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        raise ValueError("仓库路径无法解析") from exc
    if not any(resolved.is_relative_to(root) for root in roots):
        raise ValueError("仓库路径不在 ALLOWED_REPO_ROOTS 授权范围内")
    return str(resolved)


def require_repo_path(value: str) -> str:
    """! @brief 再校验历史任务目录，防止配置收紧或链接替换后绕过授权。"""

    try:
        return validate_repo_path(value)
    except ValueError as exc:
        raise ToolPermissionError(str(exc)) from exc
