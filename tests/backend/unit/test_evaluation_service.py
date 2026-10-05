"""! @brief 发布结果与运行代码隔离验证。"""

import os
import subprocess
import sys

import pytest

from backend.src.services.evaluation_service import EvaluationService


def test_published_metrics_keep_failed_attempts_in_denominator():
    """! @brief 最终页不能混入旧批次或通过删除失败改变统计。"""
    result = EvaluationService().summary()
    assert set(result) == {"single_adaptive"}
    assert result["single_adaptive"]["runs"] == 18
    assert result["single_adaptive"]["test_pass_rate"] == 14 / 18
    assert result["single_adaptive"]["success_rate"] == 10 / 18
    with pytest.raises(ValueError):
        EvaluationService().summary("historical-batch")


def test_runtime_import_does_not_load_research_tools():
    """! @brief API 必须无需加载研究工具和统计绘图包即可导入。"""
    environment = {**os.environ, "LLM_API_KEY": "", "PYTHONUTF8": "1"}
    code = "from backend.src.main import app; import sys; assert not any(k == 'research' or k.startswith('research.') for k in sys.modules)"
    subprocess.run([sys.executable, "-c", code], env=environment, check=True, timeout=60,
                   capture_output=True, text=True, encoding="utf-8")
