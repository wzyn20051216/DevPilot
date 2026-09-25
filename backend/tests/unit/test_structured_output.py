"""LLM 结构化输出解析测试。"""

from pydantic import BaseModel

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
