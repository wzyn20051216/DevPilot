# pydicom-1139 诊断重跑（2026-10-04）

- run_id：`2c94b208d8494c65a444afc8141284f3`，`deepseek-v4-flash`，`single_no_rag`，14 轮，173,837 Token，98.7 秒
- 配置：当时的工作树版本，只把迭代器探针要求改写成逐条清单；没有新增工具
- 性质：已知失败题上的单次诊断，**不是**改进证据，不计入任何成功率

## 结果

仍失败，非预期失败节点 `TestPersonName::test_next` 与第四、第五轮**相同**。
（此前文档曾写「失败点从 not iterable 变为 not an iterator，说明有进步」，这是误读：
`not iterable` 出自未修改代码的基线校准输出，不是此前候选补丁的失败信息。已撤回。）

候选补丁只新增 `__contains__` 与 `__iter__`（返回 `iter(str(self))`）。隐藏测试末尾要求：

```python
pn4 = PersonName("SomeName")
with pytest.raises(AttributeError):
    next(pn4)
```

即对未先调用 `iter()` 的对象直接 `next()` 必须抛 `AttributeError`。这要求实现成
「`__iter__` 保存内部迭代器并返回 self、`__next__` 读取该属性」这种特定写法。

## 归因

1. Agent 工作区中不存在该测试（测试来自 SWE-bench 的 test_patch，不暴露给 Agent），
   Issue 文本也没有提到 `next()` 的行为。模型读到的 `test_valuerep.py` 是旧版本。
2. 探针清单确实提到「旧式 next() 兼容」，模型的探针只验证了 `next(iter(obj))`，
   没有验证直接 `next(obj)`；即使验证了，也无法从 Issue 推断出「应抛 AttributeError」。
3. 因此这题更接近「隐藏测试要求了 Issue 未说明的行为」。OpenAI 在建立 SWE-bench Verified
   时把这类题目作为人工剔除对象之一。继续针对它调提示词属于对答案过拟合。

后续改进转为在未参与调参的 SWE-bench Verified 新题上做配对 A/B，见
[能力改进计划](capability-improvement-plan.md)。
