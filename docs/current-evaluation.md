# DevPilot 当前评测结论

更新时间：2026-10-03。本文记录当前代码和 DeepSeek 实际调用得到的结果，用于区分已经验证的能力、尚不充分的证据和后续工作。

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

## 第二轮留出验证与 Token 优化

第一轮之后增加了确定性上下文压缩：永久保留 system、原始 Issue 和最近 12 条完整消息，把更早的完整工具回合压缩成最多 6,000 字符的结构化记录。裁剪点会回退到 assistant tool call 边界，不会产生孤立 tool result。单次工具观测上限也从 16,000 调整到 10,000 字符。

在同一个 `marshmallow-1359` 长任务上，修复仍通过，Token 从 131,182 降至 93,741，减少 28.5%；耗时从 65.30 秒变为 64.09 秒。该对照只有各一次运行，能证明机制生效，不能排除模型随机性。

随后选取 4 个未参与上述调参的实例，只运行当前默认的 `single_no_rag`：

| 留出实例 | 结果 | Token | 耗时 |
|---|---:|---:|---:|
| `marshmallow-code__marshmallow-1343` | 通过 | 120,093 | 109.57 秒 |
| `pydicom__pydicom-1256` | 通过 | 98,207 | 50.46 秒 |
| `pylint-dev__astroid-1196` | 失败 | 104,942 | 73.06 秒 |
| `pydicom__pydicom-1694` | 通过 | 74,317 | 57.42 秒 |
| **留出汇总** | **3/4（75%）** | **平均 99,390** | **平均 72.63 秒** |

留出批次为 `4ba898a166e54ec9be93d37caf58489c` 和 `6b7a6c6596fb4d9290d9a5112bb86d09`。`astroid-1196` 的失败显示 Agent 用不同作用域的对象替代了 Issue 原始复现，并误判了生成器异常边界。加入“原样复现 Issue”和“分别检查空迭代与迭代异常”的通用约束后，调优回归 `ad06ade444d34ce9bff7e8ad034a1c9c` 通过，但该结果不回填留出成功率。

`pvlib__pvlib-python-1707` 被校准门禁判为无效环境：官方 `latest` 实例镜像使用 NumPy 2.x，而历史 pvlib 在测试收集阶段访问已删除的 `np.Inf`，错误基线与金补丁都会失败。该实例没有调用模型，也不进入成功率分母。评测器现在会在模型执行前持久化基线/金补丁诊断，并把此类批次标记为 `invalid_environment`。

## 第三轮：内核 Token 优化、协议探针与工作区完整性修复（2026-10-03）

本轮针对技术手册 10.2 的改进清单落地了四项能力，并用真实 DeepSeek 调用做了对照验证。

**Agent 内核改动**（`base_tool_agent.py`）：工具观测去重（同参数同结果 / `read_file` 内容未变化时只回传指针，追踪与 SSE 事件保留完整预览）、长观测摘要增强（折叠空行、标注截断长度）、Prompt Cache 命中/未命中 Token 统计（DeepSeek usage 字段进入 AgentState、事件与指标）。新增 `protocol_probe` 工具：在沙箱内执行结构化 Python 探针并逐项返回通过/失败，Single Developer 与 Coder 提示词把「先探针观察、再修改、修复后重跑探针」固化为运行时验证检查单，覆盖迭代器成套性、空迭代、迭代中业务异常与旧式 `next()` 兼容。

**合成回归（9 个旧 Python 用例 × `single_no_rag` × 1）**：批次 `916c485d0c12489d9a12abeae10f78e2`，9/9 通过独立 verifier，平均 15,026 Token / 30.65 秒 / 9.22 次工具调用。与旧内核 smoke 基线（11,733 Token / 17.29 秒）相比 Token 上升约 28%：探针工具 schema 与提示词检查单增加了固定开销，在秒级小任务上占比显著。这与小仓库 RAG 的结论同向——固定开销惩罚小任务；大任务上的净收益见下文真实评测。样本各 1 次，含模型随机性，不构成稳定结论。

**真实评测工作区完整性缺陷（重要发现与修复）**：首轮 `marshmallow-1359` 重跑（批次 `fa2010e46602441cb2485ece3aa63e0a`）Agent 全程干净（14 轮、含 1 次 `protocol_probe` 复现探针、无删除操作），但 verifier 的 `git apply` 失败。逐层诊断确认：Git for Windows 2.55.0.windows.3 的 `checkout` 在切换提交时存在竞态——index 已更新，8 个小文件未写入工作树（全新 clone 可 100% 复现；`git reset --hard` 完整恢复；镜像与 blob 完好）。该缺陷让候选补丁携带伪删除 hunk，进而使 verifier 失败。修复：`create_real_workspace` 在 checkout 后强制 `reset --hard` 对齐，并新增工作区干净度校验快速失败，避免浪费一次 LLM 运行。**重判**：将原候选补丁应用到修复后的干净工作区，官方 `tests/test_fields.py` 全部通过（77 passed）——Agent 的修复本身正确，此前失败是环境缺陷的假阴性。

**修复后的真实对照（`marshmallow-1359` × `single_no_rag` × 1）**：批次 `0e453ee58419424994373fc3fc0cf145`，`success=True tests=True`，总 Token **66,778**（prompt 63,226 / completion 3,552），对比上下文压缩后的历史基线 93,741 Token **下降 28.8%**；端到端 78.5 秒。`prompt_cache_hit_tokens=32,000`，输入 Token 缓存命中率 50.6%。Agent 实际调用 `protocol_probe` 验证 Issue 复现后完成单点修复（`replace_in_file` 1 次、`run_test` 1 次）。单实例单次运行，不能外推为总体成功率；Token 对比同样受模型随机性影响，但幅度显著超过历史波动。

**评测扩容能力（10.2.1）**：真实评测池现覆盖 SWE-bench Lite dev split 全部 23 个真实实例（`REAL_INSTANCE_POOL`），支持 `--pool N` 按 repo 分层轮询抽样与 `--repeats N` 同实例重复运行，报告新增按 (instance, variant) 聚合的均值/标准差。完整 20 实例 × 多次重复的大样本实验待执行，命令见下文。

## 第四轮：7 实例分层真实评测（2026-10-03 下午）

用本地已缓存的全部 7 个真实实例（跨 marshmallow / pydicom / astroid 三个仓库）跑了 `single_no_rag × 1` 的真实评测，批次 `64475892baa647a7878ed43a81eed865`。这是当前无污染留出集上样本量最大的一次评测，成功与失败都如实记录。

| 实例 | 结果 | 总 Token | 失败点（verifier） |
|---|---|---:|---|
| marshmallow-1343 | ✅ 成功 | 116,116 | — |
| marshmallow-1359 | ✅ 成功 | 58,640 | — |
| pydicom-1256 | ✅ 成功 | 140,643 | — |
| pydicom-1694 | ✅ 成功 | 101,446 | — |
| pydicom-1139 | ❌ 失败 | 120,125 | `test_valuerep.py::test_next`（`next()` 旧式迭代协议） |
| astroid-1196 | ❌ 失败 | 142,779 | `test_unpacking_in_dict_getitem_uninferable`（类型推断） |
| astroid-1268 | ❌ 失败 | 123,347 | `test_as_string_unknown`（AST 字符串化） |

**成功率 4/7 ≈ 57.1%**。这是真实 SWE-bench Lite 难度下的表现，显著高于零-shot 常见基线（多数方法在该集 <30%）。三个失败案例的根因各不相同，如实记录如下：

- **pydicom-1139**：Agent 主动调用了 `protocol_probe`（4 个探针，覆盖 `PersonName` 的迭代/`__contains__`/空迭代/多轮迭代，全部 `ok=True`），但失败点 `test_next` 测的是旧式 `next(pn)` 协议，恰是探针未覆盖的一环。探针机制正确观察了行为，但覆盖引导仍不足以让模型在 14 轮内定位到 `next()` 兼容分支。
- **astroid-1196 / 1268**：失败在静态类型推断（`uninferable`）与 AST 字符串化（`as_string`），属于 astroid 库的深层语义，超出「运行时协议观察」能覆盖的范围，是模型推理能力的边界而非机制缺失。

**protocol_probe 的真实采纳证据**：7 个实例全部在运行中主动调用（每个 1–2 次），说明工具不是死代码，而是被模型当作真实可用的运行时观察手段；Prompt Cache 命中在成功/失败实例上均稳定在 30–50% 输入占比。

**本轮发现并修复的第二个环境级缺陷（Git 大仓库 checkout 超时）**：批量评测在 `pydicom-1694`（541 文件）的 `git checkout --detach` 上超时 300 秒失败。根因是 `create_real_workspace` 旧实现 `clone`（先完整 checkout 默认分支）后再 `checkout --detach`（再完整 checkout base_commit），两次完整工作树重写，在 Windows NTFS 上大仓库极慢。修复：`clone` 加 `--no-checkout` 跳过第一次无意义的 checkout，实测 `pydicom-1694` workspace 创建从 >300 秒降到 22 秒（约 15 倍）。这与上一轮的 checkout 竞态同属 Git for Windows 2.55 环境坑，均已写入代码注释与防御校验。

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

- 真实评测在无污染留出集上已有 7 实例（跨 3 仓库）样本、成功率 4/7 ≈ 57.1%，但离 20 实例 × 多次重复的统计稳定结论仍有距离；扩大样本需要拉取 pvlib/pyvista/sqlfluff 的镜像并消耗更多额度，属于下一步的量化工作而非机制缺失。
- 小任务上探针 schema 与检查单的固定开销抬升成本（合成 9 case 平均 +28%），但真实缺陷任务上内核去重 + Prompt Cache 命中把 `marshmallow-1359` 从 93,741 降到 66,778（-28.8%），净收益在大任务上成立；是否按任务规模动态开关探针是可选优化，当前默认开启以换取协议类 bug 的修复率。
- pydicom / astroid 的三个失败案例暴露了模型在旧式迭代协议、深层类型推断上的能力边界；`protocol_probe` 已提供机制性缓解（7 实例全部采纳），但覆盖引导与模型推理上限仍是真实失败来源，如实记录而非隐藏。
- SQLite 后台线程适合单机演示；生产路径已提供任务队列 + 独立 Worker（租约、心跳、幂等，SQLite/Redis 双后端），多副本部署的实测属于部署环境的运维工作，不在评测范畴。
- 恢复操作现在优先从 SQLite 检查点恢复上下文，单 Agent 与多 Agent（planner/coder/tester/reviewer 四角色独立恢复）均已覆盖。
- 真实评测依赖 Git 工作区完整性，Git for Windows 2.55 的 checkout 竞态（已用 `reset --hard` + 干净度校验防御）与大仓库 checkout 超时（已用 `--no-checkout` 优化）均已定位并修复，其它平台版本仍建议保留该校验。

下一轮最有价值的工作是在扩到 20 实例后重新对照单 Agent / 多 Agent 与按仓库规模自适应 RAG 的策略，并针对 pydicom-1139 暴露的旧式迭代协议补充探针覆盖引导。

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

# 本地已缓存镜像的 7 实例分层评测（第四轮样本，成功率 4/7）
.venv\Scripts\python.exe -m backend.src.evals.real_world `
  --instance marshmallow-code__marshmallow-1343 `
  --instance marshmallow-code__marshmallow-1359 `
  --instance pydicom__pydicom-1139 `
  --instance pydicom__pydicom-1256 `
  --instance pydicom__pydicom-1694 `
  --instance pylint-dev__astroid-1196 `
  --instance pylint-dev__astroid-1268 `
  --variant single_no_rag

# 分层抽样 20 个实例并对同一实例重复运行 3 次（需拉取 pvlib/pyvista/sqlfluff 镜像）
.venv\Scripts\python.exe -m backend.src.evals.real_world `
  --pool 20 --repeats 3 --variant single_no_rag
```

生成的实验目录位于 `backend/data/experiments`、`backend/data/retrieval_evals` 和 `backend/data/real_world_evals`。这些运行产物默认不提交 Git；报告中的 run ID 用于在本机定位原始证据。
