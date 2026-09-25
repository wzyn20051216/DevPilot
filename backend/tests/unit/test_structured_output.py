"""LLM 结构化输出解析测试。"""

from pydantic import BaseModel

from backend.src.models.agent_state import ReviewerOutput
from backend.src.services.structured_output import parse_structured_output


class DemoOutput(BaseModel):
    """测试专用结构化模型。"""

    name: str
    value: int


def test_parse_plain_json() -> None:
    """纯 JSON 文本应直接通过 Pydantic 校验。"""

    result = parse_structured_output(
        '{"name": "DevPilot", "value": 1}',
        DemoOutput,
    )
    assert result == DemoOutput(name="DevPilot", value=1)


def test_parse_markdown_json() -> None:
    """带说明文字和 Markdown 围栏的模型输出也应被提取。"""

    result = parse_structured_output(
        """
        Result:
        ```json
        {"name": "DevPilot", "value": 2}
        ```
        """,
        DemoOutput,
    )
    assert result.value == 2


def test_parse_json_with_unescaped_control_character() -> None:
    """LLM 把真实换行写入字符串时仍应通过 Schema 校验。"""

    result = parse_structured_output(
        '{"name": "Dev\nPilot", "value": 3}',
        DemoOutput,
    )

    assert result.name == "Dev\nPilot"


def test_reviewer_output_normalizes_object_issues() -> None:
    """Reviewer 常见的对象问题列表不应使编排流程失败。"""

    result = parse_structured_output(
        """
        {
          "approved": false,
          "summary": "发现一个边界问题",
          "issues": [
            {
              "severity": "minor",
              "file": "app.py",
              "description": "需要处理空值"
            }
          ]
        }
        """,
        ReviewerOutput,
    )

    assert result.approved is False
    assert len(result.issues) == 1
    assert "app.py" in result.issues[0]
