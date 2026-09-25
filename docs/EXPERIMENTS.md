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

## 已完成 Pilot

2026-09-23 已对 `add_bug` 执行一次四组真实实验，原始数据保存在 [`experiments/pilot_add_bug.csv`](experiments/pilot_add_bug.csv)：

| Variant | 测试通过 | Tool Calls | 迭代 | 耗时（秒） | Total Tokens |
|---|---:|---:|---:|---:|---:|
| `single_no_rag` | 1/1 | 7 | 5 | 100.48 | 7,768 |
| `single_rag` | 1/1 | 6 | 5 | 81.66 | 8,309 |
| `multi_no_rag` | 1/1 | 21 | 17 | 313.42 | 24,613 |
| `multi_rag` | 1/1 | 24 | 17 | 323.13 | 30,256 |

该 pilot 只能证明评测链路能够产生完整记录，不能证明某个架构更优。单 Agent + RAG 在这一题少 1 次工具调用、快约 18.83 秒，但多用了 541 Token；多 Agent 在简单题上开销明显。正式结论必须等待 9 个 case 的配对重复实验。

## 可复现性

每个正式批次会保存模型名称、温度、随机种子、重复次数、case 列表、数据集 SHA-256、Embedding 模型、Python 版本和平台。每个 variant 在独立 Git workspace 中从同一错误基线开始，防止前一组修改污染后一组。

## 当前边界

- 9 个 case 足以展示完整实验方法，但还不足以代表所有真实软件工程任务。
- 模型服务可能不保证完全确定性，因此正式结果至少重复 3 次。
- Wilcoxon 检验在小样本下统计功效有限，报告时必须同时给出效应量和置信区间。
- 当前任务均为 Python 微型仓库；后续可加入 TypeScript、真实开源 Issue 和更大上下文仓库。
