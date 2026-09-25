"""! @brief DevPilot 统一日志配置。"""

import sys

from loguru import logger

from .config import BACKEND_ROOT, settings


def configure_logging() -> None:
    """! @brief 初始化控制台与滚动文件日志。

    日志只记录运行状态，不记录 API Key、GitHub Token、Authorization Header
    或完整 ``.env`` 内容。重复调用会先移除旧 handler，避免测试和热重载时
    同一条消息被打印多次。
    """

    logger.remove()
    _ = logger.add(
        sys.stderr,
        level=settings.log_level.upper(),
        colorize=True,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level:<8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
            "<level>{message}</level>"
        ),
    )

    log_dir = BACKEND_ROOT / "data" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    _ = logger.add(
        log_dir / "devpilot.log",
        level=settings.log_level.upper(),
        rotation="10 MB",
        retention="14 days",
        compression="zip",
        enqueue=True,
        encoding="utf-8",
    )
