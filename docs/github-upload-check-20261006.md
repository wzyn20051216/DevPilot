# GitHub 上传检查记录

检查日期：2026-10-06。目标仓库：[wzyn20051216/DevPilot](https://github.com/wzyn20051216/DevPilot)，分支 `main`。

## 文档入口

[技术知识库（飞书同步版）](technical-knowledge-base.md)保存完整正文和 10 张配图。README 已增加入口，图片采用仓库内相对路径；原始飞书快照和生成提示词保留于 `feishu-revision-20261006/`。

## 发现并处理的问题

1. 本地有 47 个提交尚未推送，前端工作台改动、评测页删除和 `tests/conftest.py` 也未完整提交。已补齐当前工作区版本，避免 GitHub 代码与知识库描述不一致。
2. README 与用户说明书仍介绍已移除的前端评测页面。已更新 Markdown／HTML，说明当前工作台模式和后端结果 API。
3. 发布清单中 8 个 JSON／CSV 的 SHA-256 使用 Windows CRLF 原始字节，Git 保存为 LF 后校验不一致。已限定发布数据和基线 fixture 的 LF 换行，以 Git 实际字节核对清单；原始工作站指纹另存，实验数值未改写。
4. `source-map-js@1.2.1` 存在一项高危依赖漏洞，已通过锁文件升级至 `1.2.2`，没有扩大直接依赖范围。[GitHub 安全公告](https://github.com/advisories/GHSA-68fv-2mgg-jv7q)
5. 飞书根目录调试截图和 Playwright 日志目录未明确排除。已加入 Git／Docker 忽略规则，正式文档配图仍保留。

## 本地验证

| 检查 | 结果 |
|---|---|
| 远端访问 | 当前账户具有仓库管理员与推送权限；仓库为 public |
| 分支关系 | 获取远端后无分叉，可正常 fast-forward 推送 |
| 待推送历史扫描 | 初始 47 个提交、334 个新 blob；未命中凭据模式，未跟踪 `.env`／业务数据库／私钥，无 Git 子模块漏上传 |
| 文件体积 | 初始新增 blob 总计约 25 MB，最大单文件约 1.76 MB |
| 图文完整性 | GitHub 阅读版有 10 张本地图片，主要文档相对链接无缺失 |
| 发布结果指纹 | 9 个公开发布文件按 Git blob 字节校验通过；基线数据集与 Git 中 56 个 fixture／case 文件指纹一致 |
| 后端回归 | 242 passed、27 skipped；本机未配置专用 MySQL／Redis 测试连接 |
| 干净前端快照 | `npm ci`、8 项测试、类型检查和生产构建通过 |
| 依赖审计 | 安全补丁后 `npm audit` 为 0 项已知报告漏洞 |

前端安装在独立 Git 归档快照中验证，避免本机正在运行的 Vite 锁定原生模块影响结果。工作目录的依赖也已恢复。

## 复现与边界

```powershell
uv sync --frozen --group research
uv run python -m pytest
Set-Location frontend
npm ci
npm test
npm run build
npm audit
```

本次后端的 27 个跳过用例不能视为外部服务验收；GitHub CI 配置了专用 MySQL／Redis 服务，远端状态以 Actions 对应提交的结果为准。

`research/results/latest/evidence/` 的 201 个本机证据文件仍按项目已有规则不纳入 Git，清单保留冻结指纹并已明确标注。公开结果包可以查看指标与逐次数据；完整重跑还需要实例镜像、数据集、模型配置及相应本机证据。
