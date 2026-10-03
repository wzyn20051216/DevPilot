Use case: scientific-educational
Asset type: DevPilot 技术知识库中的 Hybrid Code RAG 教学信息图，横版 16:9
Reference images: primarily use the earlier attached MCP explainer as the visual benchmark—white canvas, dense but ordered teaching layout, numbered modules, fine blue outlines, compact icons, precise arrows and a clear legend. Use the latest system-blueprint image only as a secondary reference for routing discipline. Do not copy either image's content, watermark, branding, or exact arrangement.

Primary request: 制作一张专业课程级中文技术信息图，完整解释 DevPilot 的 Hybrid Code RAG。从仓库索引到双路检索、RRF 融合、按文件去重，再把证据交给 Agent。设计应像工程教材内页，信息可独立阅读，不能只是抽象插画。

Header text, exact:
Main title: “Hybrid Code RAG 双路检索”
Subtitle: “结构感知分块 × 语义检索 × 词法检索 × Reciprocal Rank Fusion”

Layout: four numbered vertical zones connected left-to-right. White background, navy typography, cobalt-blue semantic path, amber lexical path, violet fusion, teal output. Every zone is a bordered container with concise nested cards and relevant technical icons.

Zone ① exact header: “仓库索引”
Cards in order:
“过滤依赖、构建目录与二进制文件”
“Python AST / 文本窗口分块”
“保留 file_path、symbol、line range”
“构造 embedding_text”
Visual: repository folder becomes several structured code chunks.

Zone ② exact header: “两条互补检索路径”
Upper cobalt branch:
“向量检索”
“all-MiniLM-L6-v2”
“384 维归一化向量”
“Semantic Top 20”
Lower amber branch:
“BM25 词法检索”
“函数名 / 配置项 / 异常名”
“Lexical Top 20”
Show both branches operating on the same query and remaining visually separate.

Zone ③ exact header: “RRF 排名融合”
Center formula, render exactly: “RRF(d) = Σ 1 / (60 + rankᵢ(d))”
Three short notes:
“只融合排名，不直接相加原始分数”
“避免向量分数与 BM25 分数量纲冲突”
“两路共同命中的结果自然前置”
Visual: two colored ranked lists enter one precise fusion mechanism.

Zone ④ exact header: “上下文输出”
Cards:
“按文件去重”
“每个文件保留最高分 chunk”
“返回路径、符号、行号与代码片段”
“注入 Planner / Coder / Reviewer”
Avoid showing Tester receiving RAG.

Bottom strip exact header: “为什么需要混合检索？”
Three equal explanation cards:
“语义召回｜词面不同，也能找到相关实现”
“精确匹配｜函数名和异常名不会被稀释”
“控制冗余｜避免同一文件片段挤满上下文”

Bottom warning callout, exact:
“检索命中 ≠ 修复成功：Recall@5 = 1.0，但当前 3 个真实缺陷中 RAG 仅通过 1 个。”

Text requirements: render all quoted Chinese and technical terms verbatim. Use Simplified Chinese and a crisp sans-serif font. No fake code, no pseudo words, no watermark.
Accuracy constraints: vector and BM25 branches are parallel; RRF uses k=60; each branch retrieves Top 20; final output deduplicates by file; only Planner, Coder and Reviewer receive RAG; do not invent a vector database product.
