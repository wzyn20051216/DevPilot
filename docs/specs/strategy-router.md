# 实现规格：动态策略路由（`decide_strategy`）

## 目标

把当前"四个静态变体 + `enhanced: bool` 开关"的硬编码方式，替换为**按任务特征动态决策**的策略路由器。
API 与 Worker 共用同一决策函数，保证两种部署方式行为一致。

## 证据基础

来自已完成的 SWE-bench Verified 配对实验（7 题 × 2 变体 × 2 次，`backend/data/verified_ab_summary.csv`）：

| 观察 | 数据 |
|---|---|
| 大文件（2000+ 行）用 `code_outline` 后一次修对 | pylint-4970：增强 1/2、基线 0/2；增强第 3 轮看骨架、第 11 轮跑测试即通过 |
| 小改动场景下强制结构导航导致试错 | sphinx-7454：增强 1/2、基线 2/2；增强 r2 出现 17 次 `replace_in_file` |
| 行为语义类问题需要探针前置 | sphinx 基线两次各 6/2 次 `protocol_probe` 后成功；pylint 增强 1 次探针即确认 |

## 必须遵守的既有范式

参照 `backend/src/rag/policy.py`（已存在的同类实现）：

1. **纯函数 + frozen dataclass**：决策不构造索引、不发请求、除遍历目录外无副作用
2. **可解释**：返回 `mode` 与中文 `reasons` 列表，供日志和前端展示决策依据
3. **可观测**：返回 `metrics` 字典，记录触发决策的原始指标
4. **阈值来自 `Settings`**：不硬编码魔数，便于测试注入
5. **测试注入**：`settings: Settings | None = None` 参数，默认取单例

## 交付物

### 1. 新增 `backend/src/agents/strategy.py`

```python
@dataclass(frozen=True)
class AgentStrategy:
    """一次任务级策略决策的结果。"""
    use_outline: bool          # 是否暴露 code_outline 工具
    enforce_probe: bool        # 是否在提示词中强制「先探针观察再编辑」
    max_iterations: int        # 本轮任务的工具交互上限
    mode: str                  # 命中的决策分支名
    reasons: list[str]         # 中文理由
    metrics: dict[str, object] # 决策依据的原始指标

def decide_strategy(
    repo_path: str | Path,
    question: str,
    settings: Settings | None = None,
) -> AgentStrategy:
    """按仓库结构与问题语义决定本轮 Agent 策略。"""
```

决策规则（按顺序短路，每步都要有注释说明依据）：

- **`use_outline`**：
  - 仓库非 Python 主导（`count_source_files` 与 Python 文件数比例过低）→ `False`
  - 问题已给出明确文件引用且该文件行数 < `strategy_outline_min_lines`（建议 800）→ `False`
  - 目标文件行数 ≥ 阈值，或问题无明确文件引用且仓库源码文件数 ≥ `strategy_outline_min_repo_files`（建议 400）→ `True`
- **`enforce_probe`**：
  - 问题命中行为语义关键词（`AttributeError` / `TypeError` / 迭代 / 迭代器 / 生成器 / 协议 / 序列化 / `__iter__` / `__next__` / `next(`）→ `True`
  - 纯文档 / 纯字符串格式化 / 纯配置类问题 → `False`
- **`max_iterations`**：默认沿用现有 14；本轮不引入动态轮数（第六轮已证伪"加轮数有效"），字段先落地便于后续实验

**要求**：复用 `rag/policy.py` 里已有的 `count_source_files` 与 `extract_query_signals`，不要重复实现文件遍历与符号提取。

### 2. 新增 `Settings` 字段（`backend/src/config.py`）

| 字段 | 默认值 | 含义 |
|---|---|---|
| `strategy_router_enabled` | `True` | 是否启用动态路由；`False` 时回退到 `execution_mode` 的静态行为 |
| `strategy_outline_min_lines` | `800` | 目标文件达到该行数才启用 `code_outline` |
| `strategy_outline_min_repo_files` | `400` | 问题无明确文件引用时，仓库达到该规模才启用 `code_outline` |

按 `backend/.env.example` 的既有格式补充这三项，并更新根 `README.md` 的 Configuration 表格。

### 3. 接入 `SingleDeveloperAgent`（`backend/src/agents/single_developer_agent.py`）

- 新增构造参数 `strategy: AgentStrategy | None = None`
- `strategy` 为 `None` 时**保持现有行为逐字不变**（`enhanced` 布尔开关继续可用，保证已有实验可复现）
- 传入 `strategy` 时：`use_outline` 决定 `code_outline` 是否进工具集；`enforce_probe` 决定提示词是否包含强制探针段；`max_iterations` 覆盖默认值
- 提示词改为**分段组装**：基础段（现有原文，逐字不动）+ 可选的结构导航段 + 可选的强制探针段

### 4. 接入任务执行（`backend/src/services/task_policy.py`）

新增 `decide_task_strategy(task) -> AgentStrategy`，与既有 `task_rag_enabled` 并列：

- `strategy_router_enabled` 为假 → 返回静态回退策略（`mode="static_fallback"`）
- 否则调用 `decide_strategy(task.repo_path, task.question)`
- 记录日志：`logger.info("任务 {} 策略={}，依据={}", task.id, decision.mode, decision.reasons)`
- `backend/src/services/task_execution_service.py` 的 `SingleAgentTaskRunner` 接收并透传策略

### 5. 真实评测接入（`backend/src/evals/real_world.py`）

- 新增变体 `single_adaptive`：每题先 `decide_strategy`，再按决策构造 Agent
- 把决策结果（`mode` / `reasons` / `metrics`）写入 `report.json` 的行记录，便于事后归因
- 保留 `single_no_rag` 与 `single_enhanced` 两个既有变体，不改变它们的语义

### 6. 测试（追加到 `backend/tests/unit/`，新建 `test_strategy_router.py`）

- 决策边界：小文件不给 outline；大文件给 outline；明确文件引用 + 小文件不给
- `enforce_probe`：行为语义关键词命中为真；文档类问题为假
- 决定性：同一输入多次调用结果一致
- 静态回退：`strategy_router_enabled=False` 时返回 `static_fallback`
- Agent 组装：`strategy=None` 时 `allowed_tools` 与 `system_prompt` 与改动前的基线**逐字一致**（用快照断言）
- 禁止引入真实网络或 Docker 调用（纯单元测试）

## 验收标准

1. `uv run python -m pytest` 全部通过（当前 141 项，预期新增约 10 项）
2. `strategy=None` 时基线行为逐字不变（快照测试证明）
3. `decide_strategy` 对全部 12 个 Verified 抽样实例都能给出非空 `mode` 与 `reasons`
4. 不新增任何硬编码的仓库名或题目 ID

## 明确不做

- 不引入动态轮数分配（缺证据）
- 不改动 `base_tool_agent.py` 的内核循环
- 不删除任何既有变体或测试
- 不新增第三方依赖
