"""! @brief LLM 结构化输出解析服务。

本模块集中管理 Agent 对 LLM JSON 输出的解析逻辑。
模型经常会在 JSON 前后附加说明文字，或把 JSON 包在 Markdown 代码块里；
调用方不应各自手写 json.loads() 兜底逻辑，而应统一使用
parse_structured_output() 解析为指定的 Pydantic 模型。
"""

from typing import TypeVar

from pydantic import BaseModel


StructuredModel = TypeVar(
    "StructuredModel",
    bound=BaseModel,
)


def extract_json_block(
    text: str,
) -> str:
    """! @brief 从 LLM 原始输出中提取第一个完整 JSON 块。

    LLM 输出可能是以下形式：
    - 纯 JSON；
    - ```json 包裹的 JSON；
    - 前后带自然语言解释的 JSON。

    本函数用括号配平方式定位第一个完整 JSON 对象或数组，并正确跳过
    JSON 字符串内部的括号字符。

    @param text LLM 返回的原始文本。
    @return 提取出的 JSON 文本；如果未找到明确 JSON 块，则返回清理后的原文。
    @exception ValueError 当输入为空时抛出。
    """
    text = text.strip()
    if not text:
        raise ValueError("结构化输出为空。")

    # 去掉可能包裹 JSON 的 Markdown 代码块围栏。
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].lstrip()
        text = text.rstrip("`").strip()

    # 先尝试对象再尝试数组。大多数 Agent 输出是对象；优先对象可以避免
    # 说明文字里较早出现的普通方括号被误当成最终结构化结果。
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start = text.find(open_ch)
        if start == -1:
            continue

        # depth 负责配平嵌套括号；in_str/escape 保证 JSON 字符串中的
        # `}`、`\"` 不会被错误地当成结构边界。
        depth = 0
        in_str = False
        escape = False

        for index in range(start, len(text)):
            char = text[index]

            if in_str:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == '"':
                    in_str = False
                continue

            if char == '"':
                in_str = True
            elif char == open_ch:
                depth += 1
            elif char == close_ch:
                depth -= 1
                if depth == 0:
                    return text[start:index + 1]

    return text


def parse_structured_output(
    text: str,
    output_model: type[StructuredModel],
) -> StructuredModel:
    """! @brief 将 LLM 输出解析为指定 Pydantic 模型。

    调用方只需要提供原始文本和目标模型类型，本函数会先提取 JSON 块，
    再使用 Pydantic 完成字段校验和类型转换。

    @param text LLM 返回的原始文本。
    @param output_model 目标 Pydantic 模型类型。
    @return 解析和校验后的模型实例。
    @exception ValueError 当找不到 JSON 或模型校验失败时由上层感知。
    """
    json_text = extract_json_block(text)
    # 这里直接交给目标 Pydantic 模型解析：除 JSON 语法外，必填字段、
    # Literal 枚举和嵌套模型也会在同一个入口完成校验。
    return output_model.model_validate_json(
        json_text
    )
