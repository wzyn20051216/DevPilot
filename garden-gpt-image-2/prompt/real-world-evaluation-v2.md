Use case: productivity-visual
Asset type: DevPilot 技术知识库中的真实实验结果信息图，横版 16:9
Reference images: use both attached images only as a benchmark for crisp Chinese typography, precise borders, disciplined spacing and publication polish. Deliberately choose a different research-report composition: a protocol ribbon, paired metric cards, an instance evidence matrix and a conclusion panel. Do not copy either image's content, watermark, branding, or exact arrangement.

Primary request: 设计一张严谨、克制、出版物级的实验结果信息图，展示 DevPilot 在同一组 3 个 SWE-bench Lite 校准实例上，Single Agent · No RAG 与 Single Agent · RAG 的真实对照结果。重点是诚实表达小样本证据，不能把它画成营销战报。

Header exact text:
Main title: “SWE-bench Lite 真实缺陷对照实验”
Subtitle: “同一组 3 个校准实例 · 每实例每策略运行 1 次 · 描述性证据”

Canvas: clean warm-white background, navy text, cobalt blue for “No RAG”, violet for “RAG”, teal for passed cells, muted red for failed cells. Use a strict editorial grid with four numbered sections.

Section ① exact header: “实验协议”
Four compact icon cards:
“固定官方 base commit”
“错误基线必须失败”
“金补丁必须通过”
“候选测试改动不进入 verifier”

Section ② exact header: “核心指标”
Three metric rows, each with two aligned horizontal bars and exact numbers:
“成功率” — “No RAG 66.7%（2/3）” vs “RAG 33.3%（1/3）”
“平均耗时” — “No RAG 70.54 秒” vs “RAG 82.94 秒”
“平均 Token” — “No RAG 186,056” vs “RAG 181,881”
Bars must respect numeric proportions within each row. Do not combine units across rows.

Section ③ exact header: “实例级结果”
Create a precise three-row matrix:
Row 1: “marshmallow-1359” | “No RAG 通过” | “RAG 通过” | “均修复 root schema 选项引用”
Row 2: “pydicom-1139” | “No RAG 失败” | “RAG 失败” | “遗漏旧式 next() 兼容约束”
Row 3: “astroid-1268” | “No RAG 通过” | “RAG 失败” | “RAG 猜错运行时字符串语义”
Use check icons only for 通过, cross icons only for 失败.

Section ④ exact header: “当前决策”
Large conclusion card:
“默认模式：single_no_rag”
Supporting sentence:
“当前小样本中，无 RAG 的成功率和耗时更好；RAG 保留为可切换实验变量。”
Small caution card:
“不能宣称总体成功率为 66.7%：n = 3，单实例仅运行一次，不具统计显著性。”

Bottom insight strip exact text:
“Recall@5 = 1.0 只证明相关文件可被找到；检索命中不等于端到端修复成功。”

Text requirements: render every quoted label and every number exactly. Simplified Chinese, clean sans-serif font, no pseudo text, no invented metrics, no percentage rounding changes, no extra instances, no watermark.
Visual requirements: engineering research report rather than marketing dashboard; restrained color; icons and charts are subordinate to the evidence; information density and polish comparable to the reference image.
