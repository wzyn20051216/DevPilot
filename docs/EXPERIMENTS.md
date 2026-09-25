# DevPilot 实验说明

## 研究问题

实验比较单 Agent / 多 Agent、关闭 RAG / 开启 RAG 四种组合，回答两个问题：

1. Hybrid Code RAG 是否提高独立测试通过率并减少无效工具调用？
2. 多 Agent 编排是否提高复杂任务成功率，以及它付出了多少时间和 Token 成本？

## 数据集

- 总数：9 个可执行 Python 修复任务。
- 难度：easy、medium、hard 各 3 个。
- 类别：bugfix 2、configuration 2、cross_module 2、validation 2、feature 1。
- 验收：每个任务由独立 pytest verifier 判定，Agent 的文字声明不计为通过。
- 基线审计：9/9 个错误版 fixture 初始测试失败，结构检查全部通过。
- 数据集指纹：以 `backend/benchmarks/audit_report.json` 中的 `dataset_sha256` 为准。

## 运行方法

```powershell
# 先做零成本数据集审计
uv run python -m backend.src.evals.audit

# 正式运行：9 cases x 4 variants x 3 repeats = 108 个 Agent 任务
uv run python -m backend.src.evals.runner --full --repeats 3
```

完整实验会消耗真实 LLM 额度。调试时可先执行单 case：

```powershell
uv run python -m backend.src.evals.runner add_bug --variant single_no_rag
```

## 指标与统计

- 质量：`success_rate`、`test_pass_rate`。
- 效率：`tool_calls`、`iterations`、`repair_rounds`、`elapsed_seconds`。
- 成本：`prompt_tokens`、`completion_tokens`、`total_tokens`。
- 统计：先按 case 聚合重复实验，再计算配对差值、Bootstrap 95% 置信区间和 Wilcoxon signed-rank p 值。

`success` 要求 verifier 通过且执行协议中没有 error 事件；`tests_passed` 只表示最终代码通过独立测试。二者分开可识别“代码碰巧修好但 Agent 流程异常”的情况。

## 已完成正式实验

2026-09-25 已完成 `9 cases x 4 variants x 3 repeats = 108` 次真实 LLM 评测：

- Run ID：`68b84e0dd14f42afbe69788ed6260af3`
- 完整性：108 条唯一记录，无缺失、无重复。
- 成本：2,393,755 Total Tokens，累计执行耗时约 7.14 小时。
- 结论：单 Agent 两组的端到端成功率和测试通过率均为 100%。多 Agent 的测试通过率为 96.3% / 100%，但端到端成功率仅为 51.9% / 59.3%，主要损失发生在结构化输出校验和多 Agent 编排收尾。

详细方法、统计表、置信区间和图表见 [`experiments/formal_benchmark_2026-09-25.md`](experiments/formal_benchmark_2026-09-25.md)，逐次原始数据见 [`experiments/formal_benchmark_2026-09-25.csv`](experiments/formal_benchmark_2026-09-25.csv)。2026-09-23 的四组单题 pilot 仍保留在 [`experiments/pilot_add_bug.csv`](experiments/pilot_add_bug.csv)，仅用于评测链路冒烟验证。

## 可复现性

每个正式批次会保存模型名称、温度、随机种子、重复次数、case 列表、数据集 SHA-256、Embedding 模型、Python 版本和平台。每个 variant 在独立 Git workspace 中从同一错误基线开始，防止前一组修改污染后一组。

## 当前边界

- 9 个 case 足以展示完整实验方法，但还不足以代表所有真实软件工程任务。
- 模型服务可能不保证完全确定性，因此正式结果至少重复 3 次。
- Wilcoxon 检验在小样本下统计功效有限，报告时必须同时给出效应量和置信区间。
- 当前任务均为 Python 微型仓库；后续可加入 TypeScript、真实开源 Issue 和更大上下文仓库。
