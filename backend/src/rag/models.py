from pydantic import BaseModel, Field


class CodeChunk(BaseModel):
    """! @brief 代码仓库中的一个可检索片段。

    CodeChunk 是 RAG 索引的最小数据单元。每个 chunk 记录来源文件、
    行号范围、符号名和实际内容，同时额外保存一份带上下文的
    embedding_text，用于提升语义检索时的命中质量。
    """

    id: str = Field(
        description="chunk 的稳定唯一标识，通常由文件路径和行号范围生成",
    )

    file_path: str = Field(
        description="chunk 所属文件相对于仓库根目录的路径",
    )

    language: str = Field(
        description="chunk 对应的源码语言或文本类型",
    )

    symbol: str | None = Field(
        default=None,
        description="chunk 对应的函数名、类名等符号；普通文本块可以为空",
    )

    start_line: int = Field(
        description="chunk 在原文件中的起始行号，1-based",
    )

    end_line: int = Field(
        description="chunk 在原文件中的结束行号，1-based，包含该行",
    )

    content: str = Field(
        description="chunk 的原始代码或文本内容",
    )

    embedding_text: str = Field(
        description="实际用于生成 embedding 的文本，通常包含文件名、符号名和内容",
    )
