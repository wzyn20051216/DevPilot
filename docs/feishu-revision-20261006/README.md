# 飞书知识库修订交付

日期：2026-10-06。已获用户授权直接编辑[飞书知识库](https://dcn4btp2pzsc.feishu.cn/wiki/TLjZwYvmRivtOZk0nvkcuXEBnJg)。依据当前工作区源码、冻结发布包与本次离线回归，保留原章节结构，修订 76 个正文段落并替换 10 个配图位。

## 主要修订

- 实验正确率从错误的 `16/18（90.8%）` 改为 `14/18（77.8%）`，另列完整成功 `10/18（55.6%）`；说明无效实例、缓存补跑、冻结版本及文件级裁判口径。
- 区分合成初始基线失败与候选修复通过、检索成绩与修复收益、产品默认与冻结对照证据；不将后续严格协议修改算入历史成绩。
- 写明四角色权限与全部输出字段、唯一 JSON 对象的严格校验、一次禁止工具的格式纠正、测试机器事实优先，以及测试／审查共享返工预算。
- 区分默认 inline、持久化队列入队与 Worker 实际领取，补充取消、中断恢复、失租隔离与 fencing 的竞态／副作用边界。
- 写明本地 RAG 索引、默认与研究选定参数差异、加权 RRF、文件去重、缓存和精确观测去重；没有添加向量数据库或增量索引实现。
- 说明状态库与队列独立配置、六种组合、显式迁移、容器重建，校正未实现 Sentinel／Cluster、已移除前端评测页等内容。
- 说明 Preview 复核、远端 GitHub MCP 发布、删除文件限制以及非原子发布；区分工作台审批流程和开发用直接执行接口。

## 配图

按用户提供的参考图重写提示词，使用内置 imagegen，生成统一蓝白工程信息图。所有最终图片均保存于 `images/` 并通过飞书原生上传插入对应章节。

| 图号 | 内容 | 核对重点 |
|---|---|---|
| 1 | 总体架构 | 串行角色、可选 Worker、默认部署、索引与业务库分离 |
| 2 | 状态与迁移条件 | 七种状态、条件化取消／恢复／重试，采用迁移表避免连线歧义 |
| 3 | 本地任务时序 | API 启动线程以注释框标明；事件先落库，SSE 由 API 发出 |
| 4 | 纵深防御 | 本地写入与只读 Sandbox 分开，实际限制与权限边界 |
| 5 | 三层评测 | 12/12 是未修复基线失败，文件级裁判不是官方成绩 |
| 6 | 冻结结果 | 14/18、11/18、交集 10/18，以及正确样本与 Token |
| 7 | Hybrid RAG | 384 维、Top20、默认加权 RRF、本地三文件；无虚构评分 |
| 8 | 上下文压缩 | 6,000／10,000 字符、最近 12 条与配对边界、精确去重 |
| 9 | 背压与 fencing | 8／100／2 容量、30／300 秒租约、失租隔离、非恰好一次 |
| 10 | 存储与队列矩阵 | SQLite／MySQL × inline／SQL／Redis 六种真实组合 |

首版及未通过事实复核的生成稿未插入文档。最终图片逐张目视复核；飞书上传记录另保存在 `insert-*.json` 与 `verification.json`。

## 验证

- 后端：242 passed、27 skipped，耗时 46.23 秒；本次没有专用 MySQL／Redis 测试连接，因此跳过不算外部服务验收。JUnit 在 `backend-check.xml`。
- 前端：8 项测试通过，类型检查与生产构建通过。
- 飞书：重新加载后核对正文、标题结构、10 张图片的服务器标识、图注及占位清理；结果见 `verification.json`。
- 修改范围为本目录交付材料及用户指定飞书文档；现有运行代码与用户未提交改动保留。

## 材料

- `original-blocks.json`、`original-text.txt`：修改前完整备份，包含已被修正的历史错误，只用于回溯。
- `corrections.json`：最终 76 个正文段落。
- `changes.json`：原文、最终内容与对应块标识的差异清单。
- `design-v2.md`、`final-prompts.json`：参考风格、逐图事实约束和最终提示词。
- `revised.md`、`final-blocks.json`：最终图文与飞书结构快照。
- `images/`：10 张最终 PNG。
- `feishu-*.png`：最终页面目视复核截图。
- `insert-figure.ps1`：调用页面原生图片菜单的上传辅助脚本，不构造飞书私有请求接口。

## 复核依据

运行流程与协议：`backend/src/main.py`、`models/agent_protocol.py`、四个角色文件、`agents/base_tool_agent.py`、`agents/orchestrator.py`、`services/structured_output.py`。

执行与存储：`services/task_execution_service.py`、`services/task_queue.py`、`worker.py`、`database/connection.py`、`config.py`、`docker-compose.yml`。

工具、检索与发布：`tools/registry.py`、`tools/write_tool.py`、`sandbox/docker_runner.py`、`rag/code_index.py`、`rag/embedder.py`、`services/publish_service.py`、Repository／GitHub MCP 客户端。

当前界面与研究：`frontend/src/router/index.ts`、`WorkspaceView.vue`、`research/results/latest/`、`research/benchmarks/audit_report.json`、评测脚本参数定义。
