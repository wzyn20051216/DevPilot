# DevPilot 当前评测结论

更新时间：2026-10-02。本文记录当前代码和 DeepSeek 实际调用得到的结果，用于区分已经验证的能力、尚不充分的证据和后续工作。

## 真实缺陷评测

评测从 `SWE-bench/SWE-bench_Lite` 的 dev split 选择 3 个 Python 缺陷。每个实例固定官方 base commit，并在对应的官方 Docker 镜像中执行受影响测试文件。运行前分别验证错误基线确实失败、官方金补丁能够通过；候选补丁中的测试与测试配置改动不会进入独立 verifier。官方资料可参考 [SWE-bench 数据集说明](https://github.com/SWE-bench/SWE-bench/blob/main/docs/guides/datasets.md)、[SWE-bench Lite](https://www.swebench.com/lite.html) 和 [Docker 评测说明](https://github.com/SWE-bench/SWE-bench/blob/main/docs/guides/docker_setup.md)。

| 实例 | `single_no_rag` | `single_rag` | 主要观察 |
|---|---:|---:|---|
| `marshmallow-code__marshmallow-1359` | 通过 | 通过 | 两种模式都修复了 root schema 选项引用 |
| `pydicom__pydicom-1139` | 失败 | 失败 | 实现了可迭代协议，但遗漏旧式 `next()` 兼容约束 |
| `pylint-dev__astroid-1268` | 通过 | 失败 | 无 RAG 通过运行时探针确认字符串语义；RAG 运行猜错返回值 |
| **成功率** | **2/3（66.7%）** | **1/3（33.3%）** | 样本很小，不能作为总体成功率估计 |

无 RAG 三次有效运行 ID 为 `450e5b00a63d4fb5b887aae090667225`、`dfac2d0422c64c53bf23edf4446f4e34`、`f9cb29a8317f491a899bd74396d8ced5`，平均端到端耗时约 70.54 秒，平均总 Token 约 186,056。RAG 批次 ID 为 `acd3c7f883104778ae261cab7b20896f`，平均耗时约 82.94 秒，平均总 Token 约 181,881。

当前证据支持把 `single_no_rag` 设为产品默认值。RAG 在这 3 个缺陷上没有提升端到端修复率，耗时约增加 17.6%；Token 约减少 2.2%，但不能抵消成功率下降。模型输出具有随机性，3 个实例且每种模式只运行一次，差异不具统计显著性。

## 合成回归与检索评测

合成 smoke 批次 `48a25f78b9d74421adf86b4c7fbd3443` 包含 9 个独立 fixture，每个 fixture 分别运行单 Agent 无 RAG 和单 Agent RAG，共 18 次任务，全部通过独立 verifier。

| 模式 | 成功率 | 平均耗时 | 平均 Token | 平均工具调用 |
|---|---:|---:|---:|---:|
| `single_no_rag` | 100% | 17.29 秒 | 11,733 | 8.44 |
| `single_rag` | 100% | 19.85 秒 | 12,314 | 8.67 |

这是一次回归 smoke，不代表统计稳定的架构对比。它说明当前实现能稳定完成项目内的已知任务，也说明在小仓库中启用 RAG 增加约 14.8% 延迟和 4.9% Token，没有带来通过率收益。

文件级检索批次 `81657a8f46f24de9a1d055e59dfa057f` 在 9 条标注查询上得到 Recall@5 = 1.0、MRR = 0.7222、平均查询耗时约 9.2 ms、平均索引构建耗时约 1.25 秒。这证明检索器能找到目标文件，但检索指标好并不等于 Agent 修复率更高。

## 项目价值判断

当前项目已经具备 AI 应用／Agent 实习和校招作品的核心证据：模型能调用真实工具修改代码，机器测试拥有最终裁决权；任务执行与浏览器连接解耦；事件、工具耗时和 Token 可追踪；实验能保存配置、补丁、轨迹与独立验证结果；并且真实仓库评测暴露了失败案例，而非只展示成功 Demo。

它还不能宣称为生产级自主软件工程系统。当前主要限制是：

- 真实评测只有 3 个实例、每种模式 1 次，结论方差很大。
- 单次真实修复约消耗 18 万 Token，成本与上下文效率仍需优化。
- pydicom 失败显示 Agent 对隐含兼容协议和历史行为的推断仍不稳定。
- SQLite 后台线程适合单机演示；多副本部署还需要外部队列、租约、心跳与幂等 Worker。
- 恢复操作会从已批准计划重新运行 Agent，不会恢复中断前的模型上下文。
- 本轮没有重新跑多 Agent 真实对照，旧结果不能与当前代码直接比较。

下一轮最有价值的工作是把真实集扩大到至少 20 个分层实例并重复运行；针对工具观测做摘要和上下文缓存，降低 Token；加入基于运行时探针的协议检查策略；最后再比较单 Agent、多 Agent和按仓库规模自适应启用 RAG 的策略。

## 复现命令

```powershell
# 数据集与 verifier 审计
.venv\Scripts\python.exe -m backend.src.evals.audit

# 文件级 RAG 评测
.venv\Scripts\python.exe -m backend.src.evals.retrieval --top-k 5

# 合成用例；示例为当前 smoke 的两种单 Agent 模式
.venv\Scripts\python.exe -m backend.src.evals.runner --full --repeats 1 `
  --variant single_no_rag --variant single_rag

# 真实缺陷评测，需要 Docker 和已配置的 DeepSeek 额度
.venv\Scripts\python.exe -m backend.src.evals.real_world `
  --variant single_no_rag --variant single_rag
```

生成的实验目录位于 `backend/data/experiments`、`backend/data/retrieval_evals` 和 `backend/data/real_world_evals`。这些运行产物默认不提交 Git；报告中的 run ID 用于在本机定位原始证据。
