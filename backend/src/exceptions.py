"""! @brief DevPilot 业务异常层级。"""

from typing import ClassVar


class DevPilotError(Exception):
    """! @brief 可安全返回给 API 调用方的业务异常基类。"""

    status_code: ClassVar[int] = 500
    code: ClassVar[str] = "devpilot_error"
    message: str

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class TaskNotFoundError(DevPilotError):
    """! @brief 请求的持久化任务不存在。"""

    status_code: ClassVar[int] = 404
    code: ClassVar[str] = "task_not_found"


class InvalidTaskStateError(DevPilotError):
    """! @brief 当前任务状态不允许执行目标操作。"""

    status_code: ClassVar[int] = 409
    code: ClassVar[str] = "invalid_task_state"


class ToolPermissionError(DevPilotError):
    """! @brief Agent 请求了角色白名单外的工具。"""

    status_code: ClassVar[int] = 403
    code: ClassVar[str] = "tool_permission_denied"


class SandboxExecutionError(DevPilotError):
    """! @brief Docker Sandbox 无法启动或执行。"""

    status_code: ClassVar[int] = 500
    code: ClassVar[str] = "sandbox_execution_failed"


class ExternalServiceError(DevPilotError):
    """! @brief LLM、GitHub 或 MCP 等外部服务调用失败。"""

    status_code: ClassVar[int] = 502
    code: ClassVar[str] = "external_service_error"


class AuthenticationError(DevPilotError):
    """! @brief 请求未携带有效 API Key。"""

    status_code: ClassVar[int] = 401
    code: ClassVar[str] = "unauthenticated"
    headers: ClassVar[dict[str, str]] = {"WWW-Authenticate": "Bearer"}


class QueueFullError(DevPilotError):
    """! @brief 队列积压超过上限，入队被背压拒绝。

    ``retry_after`` 会写入响应头的 ``Retry-After``，提示调用方稍后重试。
    ``retry_after`` 为 None 时不带该头部。
    """

    status_code: ClassVar[int] = 429
    code: ClassVar[str] = "queue_full"

    def __init__(self, message: str, retry_after: int | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after
