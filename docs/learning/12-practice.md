# 第12章 · 实践、复现与自检

[← 第11章](11-boundaries-and-interview.md) · [课程目录](README.md) · [课程自检清单 →](README.md#完成课程后你应该能够)

> 本章目标：从概念读到实现。下列终端命令默认在仓库根目录执行。

## 学习目标

- 从无模型调用的回归开始验证理解。
- 分清研究重跑条件、收费模型与冻结证据。
- 核对发布清单及不同指标的来源。

---

- 最终结果包（唯一发布口径）：research/results/latest/

- 真实缺陷汇总：research/results/latest/real_issues.json

- RAG 参数网格：research/results/latest/rag_parameters.csv

- RAG 逐查询结果：research/results/latest/rag_queries.csv

- 服务负载：research/results/latest/service_performance.csv

- 工程统计：research/results/latest/engineering.json

- 合成数据审计：research/benchmarks/audit_report.json

- 后端测试：tests/backend/

- 前端测试：frontend/tests/

复现命令：

```powershell
# 无付费模型的合成基线审计（写到独立输出，不覆盖发布包）
uv run python -m research.evals.audit --output research/artifacts/audit-local.json

# 文件级 RAG 评测：需要模型缓存；缺缓存时首次下载
uv run python -m research.evals.retrieval --top-k 5

# 以下会真实调用配置的模型；这是新实验，不是直接复现冻结 18 次成绩
uv run python -m research.evals.runner --full --repeats 1 `
  --variant multi_no_rag --variant multi_rag

# 真实缺陷：还需指定／准备实例镜像、数据、版本与参数
uv run python -m research.evals.real_world --help
```

评测执行在 research/evals/，参数与工程评估在 research/scripts/；运行应用不导入 research。冻结结果应连同 manifest、源码／数据指纹、补丁和轨迹核验，不能用当前源码新跑的数据覆盖原批次。服务负载的 3,312 次请求来自本机可控 HTTP 负载，不是 LLM 修复吞吐或生产 SLA。

最可靠的项目介绍方式，是同时给出代码、复现命令、原始报告和失败案例。DevPilot 的价值不在于声称 Agent 已经无所不能，而在于建立了一套可以持续验证和改进 Agent 的工程闭环。

---

本课程于2026-10-06按项目源码核对；实验数据引用2026-10-05冻结批次。正文来自飞书知识库，编辑快照已移出项目归档，课程图文保留于docs/learning与docs/assets/learning。

## 源码导航

- [test_multi_agent_protocol.py](../../tests/backend/unit/test_multi_agent_protocol.py)
- [retrieval.py](../../research/evals/retrieval.py)
- [runner.py](../../research/evals/runner.py)
- [manifest.json](../../research/results/latest/manifest.json)

## 动手与自检

1. 完成本章的离线验收路径，并记录通过/跳过。
2. 检查9个公开发布文件的SHA-256与清单是否一致。
3. 准备真实模型实验前，先说明样本、版本、预算与独立裁判。

<details>
<summary>展开参考答案</summary>

先运行离线pytest与前端test/build；外部服务未配置时不能将跳过算作通过。真实模型评测是新实验，需要镜像、数据集、模型与预算，不能直接覆盖2026-10-05冻结成绩。

</details>

---

[← 第11章](11-boundaries-and-interview.md) · [返回目录](README.md) · [课程自检清单 →](README.md#完成课程后你应该能够)
