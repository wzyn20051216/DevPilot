# DevPilot 技术文档配图提示词

本轮配图使用 GPT Image 2 技能的 Host-Native 模式生成，并以用户提供的两张高密度中文技术信息图作为视觉参考。参考图只约束版式、层级、图标语言与信息密度；所有业务节点、参数和实验数字均来自 DevPilot 当前代码与评测结果。

## 最终资产与提示词

| 配图 | 最终资产 | 完整提示词 |
|---|---|---|
| 项目总览 | `docs/assets/devpilot-cover.png` | `garden-gpt-image-2/prompt/devpilot-overview-v2.md` |
| Hybrid Code RAG | `docs/assets/hybrid-code-rag.png` | `garden-gpt-image-2/prompt/hybrid-code-rag-v2.md` |
| 任务全生命周期 | `docs/assets/task-lifecycle.png` | `garden-gpt-image-2/prompt/task-lifecycle-v2.md` |
| SWE-bench Lite 真实对照 | `docs/assets/real-world-evaluation.png` | `garden-gpt-image-2/prompt/real-world-evaluation-v2.md` |

## 视觉系统

- **项目总览**：系统蓝图式布局，以五列主链路解释入口、审批、执行、工具与证据闭环。
- **RAG 原理**：课程讲义式布局，以四个编号区讲清索引、双路检索、RRF 与上下文输出。
- **生命周期**：主流程 + 可观测性 + 取消机制三层结构，颜色承担分支语义。
- **实验结果**：研究报告式布局，以实验协议、指标对照、实例矩阵和证据边界组织结论。

## 事实校对

- Planner 只生成计划，批准前不修改代码。
- Tester 与 Reviewer 不写代码；测试失败最多返工 2 次。
- SSE 消费 SQLite 中持久化的事件，不负责执行任务。
- 发布需要第二次人工确认，Snapshot Hash 不一致时阻止发布。
- RAG 使用 384 维向量、BM25、两路 Top 20 和 `k=60` 的 RRF，并按文件去重。
- 真实实验为 3 个校准实例、每策略每实例一次；`2/3` 和 `1/3` 只能作为描述性证据。

生命周期图在首次生成后做了三次局部编辑，专门消除“测试通过”分支与“任务失败”节点的视觉歧义；最终图中通过路径直接进入 Reviewer，返工额度耗尽才进入失败状态。
