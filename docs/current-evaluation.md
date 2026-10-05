# DevPilot 当前评测结论

更新时间：2026-10-05。本文记录当前代码和 DeepSeek 实际调用得到的结果，用于区分已经验证的能力、尚不充分的证据和后续工作。

## 真实缺陷评测

评测从 `SWE-bench/SWE-bench_Lite` 的 dev split 选取真实 Python 缺陷。首轮对照使用 3 题，第五轮分层抽样 20 题。每个实例固定官方 base commit，并在对应的官方 Docker 镜像中执行受影响测试文件。运行前分别验证错误基线确实失败、官方金补丁能够通过；候选补丁中的测试与测试配置改动不会进入独立 verifier。官方资料可参考 [SWE-bench 数据集说明](https://github.com/SWE-bench/SWE-bench/blob/main/docs/guides/datasets.md)、[SWE-bench Lite](https://www.swebench.com/lite.html) 和 [Docker 评测说明](https://github.com/SWE-bench/SWE-bench/blob/main/docs/guides/docker_setup.md)。

### 先说人话：这里的“实例”和“镜像”是什么

- **实例（instance）**：一个历史 GitHub Issue，例如 `pydicom-1139`。它包含问题描述、缺陷出现时的代码提交、官方测试和官方正确补丁。一个实例就是一道真实的修 Bug 题。
- **Docker 镜像（image）**：这道题配套的标准化考试环境，里面已经安装好当时版本的 Python、依赖和测试工具。它不是实验结果，也不是生成的配图。
- **容器（container）**：从镜像临时启动的一次考试房间。测试结束后容器会删除，镜像留在本机供下次复用。
- **候选池 23 个**：代码目前能够抽样的题目总数，不代表 23 个都已经跑完。
- **已缓存镜像**：本机已经下载好的考试环境，不代表对应题目已经调用模型评测。
- **已完成实验**：真正让 Agent 修改代码并由独立测试判分的运行，只有这一项才能进入成功率统计。

之所以看起来“一道题一个镜像”，是因为这些 Issue 来自不同年份、不同仓库，甚至同一仓库的依赖版本也可能不同。统一使用当前电脑环境会出现“代码没错但依赖不兼容”的假失败，所以 SWE-bench 为每个实例提供独立、可复现的运行环境。镜像名中的 `_1776_` 只是 SWE-bench 的命名格式，不表示运行了 1776 次。

### 一道真实缺陷是怎样被判分的

```text
读取真实 Issue 与缺陷提交
  → 在实例镜像中运行官方测试：确认原始代码确实失败
  → 应用官方金补丁再测试：确认标准答案确实通过
  → 回到原始代码，只把 Issue 和仓库交给 Agent
  → Agent 读取代码、修改实现、运行受控测试
  → 独立 verifier 丢弃候选测试改动，只验证生产代码补丁
  → 目标测试文件经自研 verifier 判定通过（可能排除已知环境失败），才记为成功
```

前两步叫**环境校准**，不调用模型，也不计入成功率。它们的作用是先证明“考题和考场都有效”；例如 `pvlib-1707` 就因为镜像中的 NumPy 版本导致金补丁也无法通过，被标记为 `invalid_environment`，没有拿去考 Agent。

### 当前到底完成了哪些实验

| 层次 | 实际运行 | 回答的问题 |
|---|---|---|
| 合成回归 | 旧 9 个 Python fixture 的 18 次 A/B smoke；另有 3 个跨语言 fixture | 基础执行链是否被代码改动破坏 |
| RAG 检索 | 9 条标注查询 | 检索器能否找到目标文件 |
| 真实 A/B 对照 | 3 个实例，各跑无 RAG 与 RAG | RAG 是否提高真实修复率 |
| 真实回归评测 | 7 个实例，只跑当前默认 `single_no_rag` | 默认策略在包含旧题的回归集上的表现 |
| 大样本扩样 | 20 实例分层抽样 × 3 次重复，11 个有效实例 | 第五轮完成 33 次运行，21 次成功；9/20 未进入模型阶段 |

因此，“评测池 23 个”是**可选题库规模**，“本地有多少镜像”是**已准备多少考试环境**，“成功率 63.6%（21/33）”才是**当前最大一轮已完成真实实验**。这三个数字不能混在一起。

| 实例 | `single_no_rag` | `single_rag` | 主要观察 |
|---|---:|---:|---|
| `marshmallow-code__marshmallow-1359` | 通过 | 通过 | 两种模式都修复了 root schema 选项引用 |
| `pydicom__pydicom-1139` | 失败 | 失败 | 实现了可迭代协议，但遗漏旧式 `next()` 兼容约束 |
| `pylint-dev__astroid-1268` | 通过 | 失败 | 无 RAG 通过运行时探针确认字符串语义；RAG 运行猜错返回值 |
| **成功率** | **2/3（66.7%）** | **1/3（33.3%）** | 样本很小，不能作为总体成功率估计 |

无 RAG 三次有效运行 ID 为 `450e5b00a63d4fb5b887aae090667225`、`dfac2d0422c64c53bf23edf4446f4e34`、`f9cb29a8317f491a899bd74396d8ced5`，平均 Agent 执行耗时约 70.54 秒，平均总 Token 约 186,056。RAG 批次 ID 为 `acd3c7f883104778ae261cab7b20896f`，平均耗时约 82.94 秒，平均总 Token 约 181,881。

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

**合成回归（9 个旧 Python 用例 × `single_no_rag` × 1）**：批次 `916c485d0c12489d9a12abeae10f78e2`，9/9 通过独立 verifier，平均 15,026 Token / 30.65 秒 / 9.22 次工具调用。与旧内核 smoke 基线（11,733 Token / 17.29 秒）相比 Token 上升约 28%：探针工具 schema 与提示词检查单增加了固定开销，在秒级小任务上占比显著。这与小仓库 RAG 的结论同向——固定开销惩罚小任务；单个真实任务的成本观察见下文，尚未证明大任务总体收益。样本各 1 次，含模型随机性，不构成稳定结论。

**真实评测工作区完整性缺陷（重要发现与修复）**：首轮 `marshmallow-1359` 重跑（批次 `fa2010e46602441cb2485ece3aa63e0a`）Agent 全程干净（14 轮、含 1 次 `protocol_probe` 复现探针、无删除操作），但 verifier 的 `git apply` 失败。历史排查观察到 checkout 后工作树不完整，并尝试用 reset 恢复；Git 版本缺陷、系统文件拦截、文件系统状态等候选根因尚未完成独立对照验证，不能把特定 Git 版本“竞态”写成已证实事实。修复：`create_real_workspace` 在 checkout 后强制 `reset --hard` 对齐，并新增工作区干净度校验快速失败，避免浪费一次 LLM 运行。**重判**：将原候选补丁应用到修复后的干净工作区，官方 `tests/test_fields.py` 全部通过（77 passed）——Agent 的修复本身正确，此前失败是环境缺陷的假阴性。

**修复后的真实对照（`marshmallow-1359` × `single_no_rag` × 1）**：批次 `0e453ee58419424994373fc3fc0cf145`，`success=True tests=True`，总 Token **66,778**（prompt 63,226 / completion 3,552），对比上下文压缩后的历史基线 93,741 Token **下降 28.8%**；Agent 执行耗时 78.5 秒。`prompt_cache_hit_tokens=32,000`，输入 Token 缓存命中率 50.6%。Agent 实际调用 `protocol_probe` 验证 Issue 复现后完成单点修复（`replace_in_file` 1 次、`run_test` 1 次）。单实例单次运行，不能外推为总体成功率；Token 对比同样受模型随机性影响，现有重复次数不足以估计历史波动，更不能归因于某一项优化。

**评测扩容能力（10.2.1）**：当前候选池收录 SWE-bench Lite dev split 中的 23 个实例（`REAL_INSTANCE_POOL`），支持 `--pool N` 按 repo 分层轮询抽样与 `--repeats N` 同实例重复运行，报告新增按 (instance, variant) 聚合的均值/标准差。第五轮已按 `--pool 20 --repeats 3` 执行，20 题中 9 题未通过校准，实际调用模型的是 11 题 × 3 次；见下文。

## 第四轮：7 实例分层真实评测（2026-10-03 下午）

用本地已缓存的全部 7 个真实实例（跨 marshmallow / pydicom / astroid 三个仓库）跑了 `single_no_rag × 1` 的真实评测，批次 `64475892baa647a7878ed43a81eed865`。这是第四轮的完整回归报告。它复用了第一轮的 marshmallow-1359、pydicom-1139、astroid-1268，以及已针对失败改过提示词的 astroid-1196，不能称为无污染留出集。

| 实例 | 结果 | 总 Token | 失败点（verifier） |
|---|---|---:|---|
| marshmallow-1343 | ✅ 成功 | 116,116 | — |
| marshmallow-1359 | ✅ 成功 | 58,640 | — |
| pydicom-1256 | ✅ 成功 | 140,643 | — |
| pydicom-1694 | ✅ 成功 | 101,446 | — |
| pydicom-1139 | ❌ 失败 | 120,125 | `test_valuerep.py::test_next`（`next()` 旧式迭代协议） |
| astroid-1196 | ❌ 失败 | 142,779 | `test_unpacking_in_dict_getitem_uninferable`（类型推断） |
| astroid-1268 | ❌ 失败 | 123,347 | `test_as_string_unknown`（AST 字符串化） |

**成功率 4/7 ≈ 57.1%**。这是 7 个选定 dev 实例在本项目 verifier 下的描述性结果，不能和完整官方榜单或其他方法直接比较。三个失败案例的根因各不相同，如实记录如下：

- **pydicom-1139**：Agent 主动调用了 `protocol_probe`（4 个探针，覆盖 `PersonName` 的迭代/`__contains__`/空迭代/多轮迭代，全部 `ok=True`），但失败点 `test_next` 测的是旧式 `next(pn)` 协议，恰是探针未覆盖的一环。探针机制正确观察了行为，但覆盖引导仍不足以让模型在 14 轮内定位到 `next()` 兼容分支。
- **astroid-1196 / 1268**：失败在静态类型推断（`uninferable`）与 AST 字符串化（`as_string`），属于 astroid 库的深层语义，超出「运行时协议观察」能覆盖的范围，是模型推理能力的边界而非机制缺失。

**protocol_probe 的真实采纳证据**：7 个实例全部在运行中主动调用（每个 1–2 次），说明工具不是死代码，而是被模型当作真实可用的运行时观察手段；Prompt Cache 命中在成功/失败实例上均稳定在 30–50% 输入占比。

**本轮发现并修复的第二个环境级缺陷（Git 大仓库 checkout 超时）**：批量评测在 `pydicom-1694`（541 文件）的 `git checkout --detach` 上超时 300 秒失败。根因是 `create_real_workspace` 旧实现 `clone`（先完整 checkout 默认分支）后再 `checkout --detach`（再完整 checkout base_commit），两次完整工作树重写，在 Windows NTFS 上大仓库极慢。修复：`clone` 加 `--no-checkout` 跳过第一次无意义的 checkout，实测 `pydicom-1694` workspace 创建从 >300 秒降到 22 秒（约 15 倍）。这些是本机观察和防御性修复，不能据此断言 Git 上游缺陷或 WorkBuddy 沙箱拦截是唯一根因。

## 第五轮：20 实例分层抽样 × 3 次重复（2026-10-03 晚）

用 `--pool 20 --repeats 3` 从 dev split 分层抽样 20 个实例，每个有效实例用 `single_no_rag` 重复运行 3 次，批次 `c1f4af0e7bf141b0bb4bb77da165d610`。这是当前最大规模的真实评测。抽中的 20 题里有 9 题未通过当前校准门禁，因此跳过且未调用模型：5 个 pvlib 题在导入时遇到 NumPy 2 移除的 `np.Inf`；pyvista-4315 缺少 `libGL.so.1`；sqlfluff-2419 缺少已安装包元数据；sqlfluff-1517 和 1625 在应用金补丁后目标测试仍分别有 59、63 项失败。后两题需要进一步核查镜像、测试目标和金补丁，不能笼统归因为 conda 崩溃或已证明的环境缺陷。

**通过当前校准门禁的 11 题 × 3 次 = 33 次 Agent 运行，其中 21 次成功（21/33 ≈ 63.6%）；按题统计为 7/11。** 这是选择后样本与自研 verifier 下的结果，不是 20 题或整个数据集的成功率。

| 实例 | 结果 | 成功率 | 平均 Token | 平均耗时 |
|---|---|---:|---:|---:|
| marshmallow-1343 | ✅ | 3/3 | 115,247 | 97 秒 |
| marshmallow-1359 | ✅ | 3/3 | 63,229 | 59 秒 |
| pydicom-1256 | ✅ | 3/3 | 120,050 | 78 秒 |
| pydicom-1413 | ✅ | 3/3 | 193,518 | 159 秒 |
| pydicom-1694 | ✅ | 3/3 | 97,498 | 81 秒 |
| astroid-1196 | ✅ | 3/3 | 143,291 | 101 秒 |
| astroid-1866 | ✅ | 3/3 | 112,756 | 100 秒 |
| pydicom-1139 | ❌ | 0/3 | 138,754 | 98 秒 |
| astroid-1333 | ❌ | 0/3 | 267,031 | 407 秒 |
| astroid-1978 | ❌ | 0/3 | 115,087 | 120 秒 |
| sqlfluff-1763 | ❌ | 0/3 | 173,307 | 134 秒 |

四个失败实例在本项目 verifier 下各有目标测试失败点。行记录的 `error=null` 只表示评测流程没有抛异常，不能证明运行环境完全无问题：sqlfluff-1763 每次都排除了 37 个金补丁上已有的失败节点，最后仍有 1 个非预期失败。

- **pydicom-1139**：`test_valuerep.py::TestPersonName::test_next`。与第四轮同一失败点，旧式 `next(pn)` 协议兼容分支，探针覆盖引导仍不足以在 14 轮内定位。
- **astroid-1333**：`unittest_modutils.py::test_load_packages_without_init`。模块包加载语义，静态类型推断边界之外的另一类 astroid 深层语义。
- **astroid-1978**：`unittest_raw_building.py::test_build_module_getattr_catch_output`。模块构建的 `getattr` 捕获行为。
- **sqlfluff-1763**：`linter_test.py::test_safe_create_replace_file`。文件安全替换（`safe_create_replace_file`），属仓库特有 API 语义。

值得注意的是 4 个失败实例都表现出**高一致性**：同一实例的 3 次重复结果完全一致（0/3 或 3/3），说明这 3 次运行的二值结果一致；重复次数少、样本经过校准选择，尚不能排除模型随机性或把失败归结为稳定能力边界——Agent 会产出补丁（patch 从 382 字节到 3 KB，14 轮迭代 + 15~37 次工具调用），但补丁无法通过 verifier 的目标测试。

**本批暴露的评测器环境缺陷（均已修复）**：本轮三次启动才得到完整报告，逐层定位并修改了三个评测器问题——(1) `_sandbox_profile` 的 `conda activate testbed` 在 sqlfluff/pyvista 镜像崩溃，改为 `export PATH=/opt/miniconda3/envs/testbed/bin:$PATH` 直接指向 testbed 环境；(2) pvlib 等无效环境此前会让整批评测终止，改为跳过无效实例继续跑其余；(3) `_failed_pytest_nodes` 未归一化 pytest 参数化后缀，导致 `fail_to_pass` 的 `[...]` 节点名永远匹配不上，新增 `_normalize_pytest_node` 在比较两侧统一去后缀。

## 第六轮：失败题的反证实验（2026-10-04）

针对第五轮 0/3 的失败题，固定原实例、官方镜像和当前文件级 verifier，逐次运行不同设置。以下每行仅 1 次，是定位机制的探索实验，不能与第五轮 3 次重复结果合并为新的成功率；先前查看过失败节点，因此也不是留出集。`DeepSeek Pro` 的思考强度 `max` 按[供应商接口文档](https://api-docs.deepseek.com/guides/thinking_mode/)显式启用，并在后续工具请求中保留 `reasoning_content`。报告中的 `elapsed_seconds` 仍只计 Agent 执行阶段。

| 实例与设置 | run_id | 结果 | 轮数 | Token | Agent 耗时 |
|---|---|---:|---:|---:|---:|
| pydicom-1139，Flash，24 轮与提示词调整 | `6b4db8ac075e4506ab72f0c3cec0250a` | 失败 | 24 | 239,954 | 134 秒 |
| astroid-1333，Flash，24 轮与提示词调整 | `b34d4174a76c47d9999e47aedfa35cef` | 失败 | 24 | 392,776 | 294 秒 |
| pydicom-1139，Pro，24 轮上限、未显式指定思考强度 | `d050f6eef8784d27b87dd8932d3503d5` | 失败 | 13 | 99,841 | 约 63 秒 |
| pydicom-1139，Pro，`max`，默认 14 轮 | `50901173c3a947fbb04828ca44f50846` | 失败 | 14 | 163,371 | 约 145 秒 |
| astroid-1333，Pro，`max`，默认 14 轮 | `23ff3477eb884bb5ab88eb392734e2b5` | 失败，未生成补丁 | 14 | 163,272 | 约 347 秒 |
| astroid-1333，Pro，`max`，执行层编辑门禁 | `065d3e301db846dd8effeeba84cfc47f` | 失败，生成补丁但目标测试未过 | 14 | 449,610 | 约 639 秒 |

可分享的逐次数据见[第六轮 CSV](experiments/real_failure_exploration_2026-10-04.csv)；CSV 的非预期失败列只记录 verifier 判分节点，原始 pytest 输出还可能包含已排除的环境失败。

结论是**没有观察到通过率提升**。24 轮两题均失败且显著增加 Token，默认 14 轮已恢复；Pro 与 `max` 也没有解决 pydicom 的协议边界。astroid 首次 Pro／`max` 运行在第 9 轮后继续执行搜索工具，14 轮耗尽仍无补丁：内核此前只缩小了发给模型的工具列表，却仍按角色总权限执行模型返回的旧工具名。现已让执行层按本轮工具列表拒绝越界调用，并通过回归测试。修复后的同题重跑在第 9 轮拒绝搜索、第 10 轮生成补丁，但 verifier 仍有 1 个非预期失败；pytest 输出中的另一项是金补丁上也失败、已被校准排除的节点。可见门禁修复了执行漏洞，**尚未证明修复质量提升**。

另一个生产行为缺陷是模型会在 `run_test` 未通过时口头宣称“测试已通过”。现在最终答复前会根据**最后一次代码修改之后**的 `run_test` 记录追加机器验证状态；测试通过后再次修改会撤销旧结论。截至本轮，后端完整套件 134 项通过。这是结论可信度修复，不改变上述独立 verifier 的失败结果。后续实验需要先冻结配置和评测口径，再用未调参的新题比较；不能靠不断试已知失败题追求 100%。

## 第七轮：官方 harness 复核与失败归因（2026-10-04）

第五轮的 21/33 是自研文件级 verifier 的结果，不能直接称为 SWE-bench 官方 resolved。现在已从原始 `report.json` 导出每题每次的标准 JSONL，字段为 `instance_id`、`model_name_or_path`、`model_patch`，并保存来源 manifest。旧补丁在 Windows 落盘时曾从 LF 变成 CRLF，导致磁盘字节数与报告不符；导出器只还原 CRLF→LF 再核验记录长度，今后的补丁写入固定使用 LF。三个重复批次均成功导出 11 份补丁，实例 ID 无重复、无空补丁。导出器见 `backend/src/evals/export_official_predictions.py`。

用官方 `swebench==4.0.5` Linux harness、`princeton-nlp/SWE-bench_Lite` 的 `dev` split 和已缓存的官方实例镜像评测全部三个重复批次，`run_id` 分别为 `devpilot_c1f4a_r1_official_all`、`devpilot_c1f4a_r2_official_all`、`devpilot_c1f4a_r3_official_all`。**每批 11 份提交都是 6 resolved、4 unresolved、1 harness error；合计 18 resolved、12 unresolved、3 harness error。**这不是 18/33 的可比榜单成绩，因为样本经本项目校准筛选，且有环境异常。逐次对照见[官方复核 CSV](experiments/official_lite_dev_probe_2026-10-04.csv)。官方指南说明了[预测格式、`--split`/`--instance_ids` 与唯一 run_id 要求](https://github.com/SWE-bench/SWE-bench/blob/main/docs/guides/evaluation.md)。

- 6 个官方 resolved：marshmallow-1343/1359、pydicom-1256/1694、astroid-1196/1866。
- 3 个候选补丁确实未解决：pydicom-1139、astroid-1333、astroid-1978。它们的失败节点与自研 verifier 一致。`astroid-1978` 的一次旧运行曾修改原测试，造成工作区 `run_test` 通过而独立验证失败；真实评测现已在写入层阻止修改测试/配置。防护后的真实重跑 `a01ac923109e40ce970067a889d1e788` 仍失败，说明可信度提升不等于能力提升。
- pydicom-1413 的候选补丁通过 3 个 FAIL_TO_PASS，但 2 个 PASS_TO_PASS 失败，所以官方判 unresolved；同一镜像下官方金补丁也因**相同 2 项**失败而 unresolved（对照 `run_id=devpilot_gold_pydicom1413_official`）。本项目自研 verifier 排除了这两项后判通过，应明确标为“校准后通过／官方环境不可有效判分”，不能算官方成功或归咎于 Agent。
- sqlfluff-1763 的官方实例镜像无法按 harness 指定的 `root` 用户启动：`unable to find user root: no matching entries in passwd file`。这是 harness error；自研 verifier 使用该镜像自身默认用户执行时则有 1 个未预期失败。两种结果不能合并成一次模型失败。

这次对照揭示：**优先修评测口径与环境，随后再谈优化通过率。** 当前不应以“剩余 12 次未通过”作为 12 个独立代码缺陷去逐一补答案；它们是 4 道题各重复 3 次，其中有无法按官方标准有效判分的环境问题。对已知失败题继续针对测试节点调提示词，只能算开发集诊断，不能证明泛化；下一轮必须冻结策略，在未看过结果的实例上做官方判分。

在项目根目录的 PowerShell 中复核第 1 次重复（`--split dev` 不可省；重复补丁必须更换 `--run_id`，避免官方缓存复用旧结果）：

```powershell
.\.venv\Scripts\python.exe -m backend.src.evals.export_official_predictions backend/data/real_world_evals/c1f4af0e7bf141b0bb4bb77da165d610/report.json --variant single_no_rag --repeat-index 1
docker build -t devpilot-swebench-harness:4.0.5 -f backend/docker/swebench-harness.Dockerfile backend/docker
docker run --rm --mount "type=bind,source=E:/desktop/DevPilot/backend/data,target=/workspace" --mount "type=bind,source=/var/run/docker.sock,target=/var/run/docker.sock" --workdir /workspace/official_harness -e HF_HOME=/workspace/official_harness/hf_cache devpilot-swebench-harness:4.0.5 python -m swebench.harness.run_evaluation --dataset_name princeton-nlp/SWE-bench_Lite --split dev --predictions_path /workspace/real_world_evals/c1f4af0e7bf141b0bb4bb77da165d610/predictions__single_no_rag__r1.jsonl --max_workers 3 --run_id devpilot_c1f4a_r1_official_all
```

最后一条命令的 `source=E:/desktop/DevPilot/backend/data` 须按本机项目路径调整；本机已有 11 个实例镜像。日志和官方报告保存在被忽略的 `backend/data/official_harness/` 中。`swebench==5.0.2` 当前要求新格式的 `image`/`eval_script` 字段，不适用于这里的旧 Lite dev 数据；`4.0.5` 的旧 harness 在 Windows 原生 Python 下依赖 Linux `resource` 模块，因此用隔离 Docker 运行。

## 合成回归与检索评测

合成 smoke 批次 `48a25f78b9d74421adf86b4c7fbd3443` 包含 9 个独立 fixture，每个 fixture 分别运行单 Agent 无 RAG 和单 Agent RAG，共 18 次任务，全部通过独立 verifier。

| 模式 | 成功率 | 平均耗时 | 平均 Token | 平均工具调用 |
|---|---:|---:|---:|---:|
| `single_no_rag` | 100% | 17.29 秒 | 11,733 | 8.44 |
| `single_rag` | 100% | 19.85 秒 | 12,314 | 8.67 |

这是一次回归 smoke，不代表统计稳定的架构对比。它说明当前实现能稳定完成项目内的已知任务，也说明在小仓库中启用 RAG 增加约 14.8% 延迟和 4.9% Token，没有带来通过率收益。

文件级检索批次 `81657a8f46f24de9a1d055e59dfa057f` 在 9 条标注查询上得到 Recall@5 = 1.0、MRR = 0.7222、平均查询耗时约 9.2 ms、平均索引构建耗时约 1.25 秒。这证明检索器能找到目标文件，但检索指标好并不等于 Agent 修复率更高。

## 动态策略路由 smoke（2026-10-04）

基于上一轮 `single_no_rag` / `single_enhanced` 配对轨迹，已实现 `decide_strategy`：大型 Python 结构问题才暴露 `code_outline`，迭代器、生成器、序列化和典型运行时异常问题才追加强制探针段。该决策同时接入 API、Worker 和真实评测 `single_adaptive` 变体。

为避免再次进行高成本 LLM 评测，本轮先对 `backend/data/verified_ab_summary.csv` 中已完成配对评测的 7 个 Verified 实例做无网络、无模型的决策 smoke。输入为原始 `problem_statement` 和对应基线工作区：

| 实例 | 决策 | `code_outline` | 强制探针 | 主要观测 |
|---|---|---:|---:|---|
| astropy-13579 | `outline_only` | 是 | 否 | 1,163 个源码文件 |
| seaborn-3187 | `standard` | 否 | 否 | 153 个源码文件，显式引用未定位到仓库内 Python 文件 |
| flask-5014 | `standard` | 否 | 否 | 80 个源码文件 |
| xarray-6744 | `probe_only` | 否 | 是 | 167 个源码文件，命中 iterator/iteration |
| pylint-4970 | `outline_only` | 是 | 否 | 878 个源码文件，与既有胜例中的大文件导航一致 |
| pytest-5631 | `standard` | 否 | 否 | 202 个源码文件，显式引用未定位到仓库内 Python 文件 |
| sphinx-7454 | `standard` | 否 | 否 | 文档特征命中，不强制探针 |

分支分布为 `outline_only=2`、`probe_only=1`、`standard=4`；7/7 都有非空 `mode` 和 `reasons`。这是决策边界的 smoke，**没有调用 LLM、没有生成新补丁，因此不是修复率实验**。下一步若要比较效果，应冻结阈值后在新题上对 `single_no_rag` 和 `single_adaptive` 做配对重复。

### 两题端到端 pilot

同日使用 `deepseek-v4-flash` 对现有 Verified 题做了一次真实配对运行（Run ID `138f9f61ed6c468e941b1a685e111616`）。每题分别运行 `single_no_rag` 和 `single_adaptive` 一次，使用相同模型、14 轮上限、仓库基线与独立 verifier。原始摘要见 [adaptive pilot CSV](experiments/adaptive_pilot_2026-10-04.csv)。

| 实例 | 变体 | 决策 | verifier | 轮次 / 工具 | Total Token | Agent 耗时 |
|---|---|---|---:|---:|---:|---:|
| pylint-4970 | `single_no_rag` | 静态基线 | 失败 | 14 / 15 | 99,074 | 120.7 s |
| pylint-4970 | `single_adaptive` | `outline_only` | 失败 | 10 / 12 | 104,893 | 98.7 s |
| xarray-6744 | `single_no_rag` | 静态基线 | 通过 | 14 / 16 | 155,098 | 304.8 s |
| xarray-6744 | `single_adaptive` | `probe_only` | 通过 | 14 / 16 | 159,303 | 157.1 s |

结果是基线与动态策略各通过 1/2，**这次 pilot 没有观察到成功率改善**。动态组合计 264,196 Token，比基线 254,172 多 3.9%；Agent 阶段耗时合计少 39.9%，但只有单次串行运行，受模型服务延迟和随机性影响，不能当成稳定的性能收益。

工具轨迹揭示了两个比成败更重要的事实：

- `pylint` 动态组确实调用 1 次 `code_outline`，但未调用 `run_test`，独立 verifier 与基线一样失败在 `test_set_duplicate_lines_to_zero`。结构导航被采用，但不足以保证完成验证闭环。
- `xarray` 动态组命中 `probe_only` 并最终修复成功，但模型没有调用 `protocol_probe`；它在首次编辑后用两次 `run_command` 执行了行为探针。因此当前 `enforce_probe` 是提示词级约束，不是工具层硬性门禁；“编辑前必须探针”尚未被机器保证。

因此当前最稳妥的判断是：路由器能改变工具暴露和行为轨迹，但两题单次证据不支持宣称质量提升。如果继续优化，应优先将“首次编辑前已有行为观测”变成可观测的运行时状态，再用新题做至少 3 次重复配对。

## 项目价值判断

当前项目已经具备 AI 应用／Agent 实习和校招作品的核心证据：模型能调用真实工具修改代码，机器测试拥有最终裁决权；任务执行与浏览器连接解耦；事件、工具耗时和 Token 可追踪；实验能保存配置、补丁、轨迹与独立验证结果；并且真实仓库评测暴露了失败案例，而非只展示成功 Demo。

它还不能宣称为生产级自主软件工程系统，也没有证据证明“各方面都优秀”：

- 第四轮 7 题回归复用了开发题；第五轮 21/33 来自通过校准的 11/20 题，其中包含既有任务，仍不是独立泛化估计。要冻结策略并使用未调参任务验证。
- 单个任务 Token 下降 28.8% 不能证明大仓库净收益，也不能把缓存命中统计当作已测得的货币成本节省。
- 协议探针已实现，但第五轮 4 个失败题说明现有策略仍有不足；未完成消融，不能断言失败只能归咎于模型上限。
- 单机 SQLite 队列可演示 API/Worker 分离。Redis 原子性、执行 fencing、背压、鉴权、多机状态存储和真实崩溃演练仍有缺口，详见 [项目复核](project-review.md)。
- 检查点恢复的是角色消息；多角色阶段、返工计数和工具副作用没有事务检查点，不是无损续跑。

### 核查口径补充（2026-10-03）

1. `elapsed_seconds` 计量真实评测中的 Agent 执行阶段，不含仓库克隆、前置校准和最终独立 verifier；不可称为用户端到端等待时间。
2. 这里使用官方实例镜像及数据，但执行的是自研 pytest 文件级 verifier，**不是官方 harness 的完整 resolved 指标**。校准允许金补丁存在不涉及 FAIL_TO_PASS 的已知失败，候选判分会排除这些节点。必须同时检查 `unstable_pass_to_pass`、`excluded_unstable_tests`、`ignored_environment_failures` 和原始 stdout，不能统称“官方测试全部通过”。
3. 早期尝试曾只留下工作目录，没有 `report.json`；第五轮评测器改为在校准阶段写 `partial_invalid_environment` 报告并逐行更新。`c1f4af0e7bf141b0bb4bb77da165d610` 现有完整 `report.json`，不能再把早期目录的缺报告状况当作当前实验状态。
4. 最大完整单批为 `c1f4af0e7bf141b0bb4bb77da165d610`：20 项审计，11 个题进入模型阶段，33 条记录、21 条 success。可分享的逐次指标见 [第五轮 CSV](experiments/real_expansion_2026-10-03.csv)；第四轮数据仍见 [7 题回归 CSV](experiments/real_regression_2026-10-03.csv)。补丁和追踪位于本机运行目录。
5. WorkBuddy 的补丁字节传输、clone 超时和换行配置改动保留。本次不会把“有代码”“离线测试通过”“真实模型评测完成”混作一个验收级别。

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

# 动态策略变体；每条 report row 会记录 strategy.mode/reasons/metrics
.venv\Scripts\python.exe -m backend.src.evals.real_world `
  --variant single_adaptive

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


## 多角色链路可靠性复测（2026-10-04）

批次 `da27d422f1824d5691dc712296825833` 使用 DeepSeek V4 Flash、temperature=0.2，针对 9 个自建 Python 用例，每题重复 3 次，变体仅为 multi_no_rag。数据库核对共 27 条结果，端到端 success 与固定裁判测试均为 **27/27（100%）**；平均执行耗时 76.03 秒、平均总 Token 46,696，平均工具调用 28 次，仅 1 次运行触发修复轮。

本轮接续没有重新调用 LLM，而是对保存的 27 份候选工作区重新运行独立 verifier（不采纳 Agent 对测试通过的声明、不把候选测试改动交给裁判）。**裁判重放仍为 27/27 通过**，当前数据集指纹与该批配置的 `0d766f28…` 一致。逐次结果与配置见 [CSV](experiments/synthetic_reliability_2026-10-04.csv) 和 [JSON](experiments/synthetic_reliability_2026-10-04.json)。

历史批次 `68b84e0dd14f42afbe69788ed6260af3` 中相同 9 个 case ID 的 multi_no_rag 也是 27 次，但只有 **14/27（51.9%）success**，固定测试为 **26/27（96.3%）**。这种“测试大多通过、流程仍失败”的差距与会话中定位的结构化输出/收尾故障一致。历史与当前批次的数据集指纹不同，且没有逐项控制所有代码和策略变化，因此只能视为历史回归比较，不能当作严格消融或把提升全部归因于一个补丁。

本结果证明这些合成用例上的执行链和收尾已经能完整通过，不说明大型真实缺陷也有 100% 修复率，不改变第五轮真实缺陷自研 verifier 的 21/33 或官方 harness 的复核记录。RAG 是否有收益仍需大仓库配对实验，本次未新增相关结论。

## 真实配对实验续跑（2026-10-05）

本轮已完成十二次真实模型调用，全部使用 Flash。普通单 Agent 与带工作流门禁的动态策略统一为 14 轮、250,000 Token 的 Agent 预算，在 Astropy 与 scikit-learn 两道 Issue 上各做三次配对。两个环境均证明原始代码失败、官方修复通过；Requests 因参考修复在禁网下仍失败而在模型运行前排除。冻结快照包含既有工作流 WIP，比较的是策略组合，不是两个已发布版本。

| 指标 | 普通单 Agent | 动态工作流 |
|---|---:|---:|
| 独立测试通过 | 4/6 | 4/6 |
| 端到端成功（包含各组执行要求） | 4/6 | 1/6 |
| 平均总 Token | 202,384 | 165,735 |
| 平均 Agent 阶段耗时 | 144.34 秒 | 138.59 秒 |

**没有观察到独立修复率提高。** 动态组平均 Token 少 18.1%，但多数流程未完成，不能称为同等成果下的整体效率提升。三个动态候选通过独立测试却缺少修改后同一 reproducer 的通过记录，部分还漏掉最后修改后的测试或测试未通过；主实验仅 2/6 动态流程合规。两组独立测试都通过的四对，动态 Token 少 11.6% 而 Agent 耗时多 11.9%。共同端到端成功仅一对，不足以支撑稳定性能结论。

按用户要求取消整批额外软预算后，原主比较完整续跑。为诊断唯一一次 Agent Token 截断，又追加四次双方统一 16 轮、无额外 Agent Token 上限的运行：Astropy 两组均失败，scikit-learn 两组均通过并完成流程，因此独立与端到端成功均为 1/2。这是已见题的单次预算诊断，不合并主实验，也不证明泛化或稳定收益。仅放宽资源不是全部失败的解释。

十六次新运行合计 2,975,576 Token。此前 402 余额中断的 3,213 Token 单独保存、不计修复率；所有完整失败均保留。已核对补丁哈希、固定源码与公共 CSV/JSON 数值；没有追加排除失败节点。这里仍是文件级独立 verifier，不能作为官方 harness 的 resolved 成绩。配置、环境校准、限制与本机复核命令见 [完整配对报告](experiments/paired_verified_2026-10-05.md)、[十二次主实验 CSV](experiments/paired_verified_2026-10-05.csv) 和 [四次诊断 CSV](experiments/flash16_diagnostic_2026-10-05.csv)。

## 四项工程评估（2026-10-05）

本轮新增本项目 81 文件源码上的 32 条人工查询，按 development/validation 各 16 条划分，完成 720 组分块、重叠、top_k、候选数及 RRF/融合权重对照。开发集选定 80 行窗口、无重叠、top_k=5、candidate_k=20、RRF=10、等权融合；validation Recall@5 从默认配置的 68.75% 到 84.38%，MRR 从 0.4604 到 0.6615，上下文字符数减少 47.65%。真实温查询平均耗时从 37.25ms 到 40.40ms，没有速度收益。验证标注与开发标注目标文件重合，且补网格前已看过早期结果，属于开发评估；不作为盲测或跨仓库泛化结论，未修改产品默认配置。

真实 localhost HTTP 的任务详情读取与 50ms 可控运行器执行共 3,312 次测量请求，响应与内容错误均为零。读取并发 32 时约 221.77 请求/秒、P95 242.23ms；执行链 Worker 并发 1/2/4 吞吐分别约 6.95/8.24/11.70 任务/秒，增长不线性，且并发 2 的一轮延迟明显波动。指标包含 API/Worker 完整进程树的 CPU 和工作集；不是 LLM 修复吞吐或生产容量承诺。

真实进程 kill 后约 30 秒隔离，确认旧 Worker 停止后显式恢复成功；有界重试、跨进程取消、本地真实 HTTP 的 429/401/超时探针通过。原批次取消后业务状态正确但队列暂留 queued 的缺口，随后已[修复并复测](experiments/cancellation_fix_2026-10-05.md)：取消任务不可重试，业务 cancelled、队列 dead，原始实验保留。真实 Docker 安全探针发现超时后容器残留，已改为唯一名称定向清理并复测零残留。该工程批次后端 216/216（含真实 MySQL/Redis、无跳过）、前端 8 项与构建通过；本轮没有付费模型请求。

完整参数、逐轮资源、已发现问题和复现方法见 [四项工程报告](experiments/engineering_evaluation_2026-10-05.md)，机器摘要见 [JSON](experiments/engineering_evaluation_2026-10-05.json)。这些新结果不改变前一轮真实 Issue 的独立修复率持平结论。

## 动态策略执行链修复与单题确认（2026-10-05）

本次让内核保存失败 reproducer 并在编辑后自动原样重跑，修复全量测试通过后仍缺验证却被关闭工具的收尾死锁；补齐普通缺陷的复现提示、行为关键词优先级与末段测试安排。后端回归为 **201 passed、15 skipped、0 failures**。

在已见真实题 `scikit-learn__scikit-learn-26323` 上，首次模型运行的流程、复验和目标测试已通过，但独立裁判受 Windows 长路径限制而失败。首次报告如实保留；修复独立工作区的长路径配置后，同一补丁重放为 **189 passed**。随后从头确认运行得到 **success=true、tests_passed=true、workflow compliant=true**，同一探针由失败转为通过，Agent 目标测试 188 项、独立裁判 189 项全部通过。确认运行为 8 轮、87,529 Token、Agent 阶段 63.09 秒。

这证明当前动态策略在该题上能够走完修复与验证链路，不证明已有 90% 以上总体成功率，不衡量前端、队列和发布流程；不与此前 12 次主实验合并。首次失败、同补丁重放、从头确认和冻结证据见 [实验报告](experiments/adaptive_e2e_2026-10-05.md) 与 [指标 JSON](experiments/adaptive_e2e_2026-10-05.json)。

## 动态策略多题回归与正确率（2026-10-05）

已完成 **9 道有效真实题 × 2 次，共 18 次模型任务**。原定 10 题中的 Matplotlib 因只读目录导致测试收集失败，在模型阶段前排除，校准错误单列保存。冻结源码和模型配置下，文件级独立裁判判定修对 **14/18（77.8%）**，机器工作流合规 **11/18（61.1%）**，端到端成功 **10/18（55.6%）**，未达到 90% 以上。

六道题两次都修对，八道题至少一次修对，四道题两次都完整跑通。四次候选虽然修对，却被错误的辅助复现断言或测试范围/环境问题卡住；另外四次候选未通过独立裁判，主要包括 Pylint 的 CLI 输出契约与 Astropy 的类型协议边界。单题确认通过不能代表多题稳定性，本次未与基线配对，也不是官方 SWE-bench harness 的成绩。

本轮补齐四个 bare 缓存的 refs，恢复磁盘中断的原候选与轨迹，没有重复调用模型；对象存储改为复用缓存并恢复仓库内硬链接，容器内基准提交读取已验证。全部运行累计 3,236,730 Token。核对了冻结源码、数据集、镜像 ID、18 份补丁哈希、唯一任务键和终态；计为正确的候选均为独立测试原始退出码 0，没有失败豁免。

另修复后续裁判把测试收集/内部错误视为可豁免失败的判定问题，相关离线回归 **54 passed**；该改动未混入冻结实验，也未改写旧结果。逐题数据、恢复记录、限制和后续优先级见 [报告](experiments/adaptive_multi_2026-10-05.md)、[CSV](experiments/adaptive_multi_2026-10-05.csv) 和 [JSON](experiments/adaptive_multi_2026-10-05.json)。
