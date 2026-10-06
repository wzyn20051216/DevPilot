# DevPilot 开发指南

运行代码、验证代码与研究工具采用独立目录。生产镜像只复制 `backend/src`、运行维护脚本和前端构建产物。

## 目录职责

| 目录 | 职责 |
|---|---|
| `backend/src/agents` | 模型执行内核、角色和策略 |
| `backend/src/tools` | 文件、检索、行为与隔离验证工具 |
| `backend/src/services` | 任务执行、事件、发布和已发布结果查询 |
| `backend/src/database` | 状态库和仓储 |
| `backend/src/rag` | 分块、索引和混合检索 |
| `backend/src/assets/evaluation` | 最终结果的运行快照 |
| `backend/scripts` | 数据迁移等运行维护 |
| `frontend/src` | 运行页面与客户端 |
| `tests/backend`、`frontend/tests` | 后端与前端验证 |
| `tests/support` | 隔离 API/Worker 进程探针 |
| `research` | 评测、用例与最后结果，运行应用不导入此包 |

## 安装和运行

```powershell
uv sync --frozen
uv run uvicorn backend.src.main:app --reload
```

```powershell
Set-Location frontend
npm ci
npm run dev
```

## 验证

研究统计与绘图依赖单独安装，不进入生产镜像：

```powershell
uv sync --frozen --group research
uv run python -m pytest
Set-Location frontend
npm test
npm run build
```

MySQL/Redis 集成只使用专用测试服务。配置 `TEST_MYSQL_URL`、`TEST_REDIS_URL` 后执行后端测试；未配置时相应服务用例跳过。测试用例会清理测试队列，不应连接业务存储。

测试覆盖任务状态、租约、fencing、背压、取消、检查点、鉴权、目录授权、发布预览、沙箱边界以及独立裁判。验证日志写入被忽略的 `research/artifacts`。

## 最终结果管理

最终实验集中在 `research/results/latest`。运行 API 读取 `backend/src/assets/evaluation/latest.json`，不访问研究工作区或按历史数据库累计统计。

修改代码后先运行相关回归；只有完成新的冻结实验并核对证据后，才更新结果包及运行快照。历史批次保留失败记录、参数和补丁指纹，不把后续代码修改改写成旧批次成绩。研究执行工具仍可用于新的独立批次，生成目录与运行数据分离。

## 学习与源码导航

[分章学习文档](learning/README.md)包含12个章节、源码链接和可执行自检。用户操作看[用户说明书](user-manual.md)，存储及维护看[部署指南](deployment.md)，严格角色契约看[多Agent协议](multi-agent-protocol.md)。

## 代码规范

沿用现有命名、错误处理和类型模型，新增公共函数使用中文 Doxygen 风格注释。运行代码不得依赖 `tests` 或 `research`；业务凭据通过配置注入，不写入源码、镜像或日志。提交前执行相关回归、前端构建与 `git diff --check`。
