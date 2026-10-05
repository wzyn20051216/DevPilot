# 独立验证与研究工具

此目录保存评测执行、样本和最终结果，不属于应用运行包。生产镜像和普通 API 启动不加载此目录。

| 目录 | 内容 |
|---|---|
| `evals` | 数据集、独立裁判、检索指标与配对统计 |
| `benchmarks` | 自动回归使用的受控仓库和标注 |
| `scripts` | RAG 参数、服务负载及配对运行工具 |
| `results/latest` | 唯一发布结果 |
| `artifacts` | 本地生成的验证文件，不提交 Git |

```powershell
uv sync --frozen --group research
uv run python -m research.evals.retrieval --top-k 5
uv run python -m pytest
```

真实仓库评测还需下载相应实例镜像和数据集；这些测试镜像不随应用保留。模型运行使用显式配置，普通回归不调用付费模型。
