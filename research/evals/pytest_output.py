"""! @brief 规范化 pytest 终端输出，避免颜色代码影响裁判判分。"""

import re


_ANSI_CSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def strip_ansi_codes(output: str) -> str:
    """! @brief 去除终端控制序列，保留测试节点名和断言正文。"""

    return _ANSI_CSI.sub("", output)
