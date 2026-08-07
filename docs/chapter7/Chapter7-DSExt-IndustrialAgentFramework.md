# 第七章 DSExt：工业数据分析智能体框架

> 难度：进阶 ｜ 对应主题：LLM 适配、Agent 抽象、范式实现、工具注册与测试

## 1. 场景定位

将前几章的零散工业案例抽象为一个轻量的 `IndustrialDataAgent` 教学框架，使不同模型、分析范式和数据工具能够通过稳定接口组合。框架面向离线 CSV/Parquet 样例，默认只读，不连接真实生产系统。

本场景强调“框架边界和可扩展性”，不重复实现 pandas、异常检测算法或完整数据平台。

## 2. 与原章内容的静态映射

| 原章知识或代码证据 | 工业扩展映射 |
| --- | --- |
| `my_llm.py` 的模型扩展 | 适配云端兼容接口与本地模型，并统一超时和错误 |
| `my_simple_agent.py`、`my_react_agent.py` | 提供摘要型和工具推理型工业智能体 |
| `my_calculator_tool.py` 的注册表 | 注册确定性统计工具及参数模式 |
| `my_advanced_search.py` | 改造成跨数据字典、规范和案例的多源检索 |
| `test_*.py` | 用 mock 模型和固定数据验证行为及失败路径 |

## 3. 核心抽象

- `IndustrialMessage`：角色、内容、时间、设备范围、证据引用；
- `IndustrialAgentConfig`：模型、最大轮次、工具白名单、数据根目录；
- `IndustrialAgent`：统一的 `run(task)` 生命周期；
- `IndustrialTool`：名称、用途、输入模式、输出模式和风险等级；
- `ToolRegistry`：注册、发现、校验、调用和审计；
- `AnalysisResult`：结论、证据、置信度、限制和建议下一步。

接口应隐藏模型提供商差异，同时保留原始错误类型和调用元数据，避免所有失败都变成模糊字符串。

## 4. 首批工具规划

- `DatasetProfileTool`：字段、类型、行数、缺失率和时间范围；
- `TrendAnalysisTool`：重采样、滚动统计和分组趋势；
- `AnomalyDetectionTool`：调用可解释的统计检测器并返回参数；
- `SpecificationLookupTool`：查询本地工艺规范和数据字典；
- `EvidenceExportTool`：将选定证据导出为只读分析包。

工具必须进行路径约束、字段白名单、参数校验和结果截断。模型不得拼接任意 Python、SQL 或 Shell 后直接执行。

## 5. 框架化范式

1. `SummaryAgent`：一次调用完成数据摘要，适合确定性输入；
2. `ReActIndustrialAgent`：在白名单工具中迭代取证；
3. `PlanSolveIndustrialAgent`：显式维护调查计划和步骤状态；
4. `ReflectionIndustrialAgent`：复核报告中的证据与风险措辞；
5. `FunctionCallIndustrialAgent`：使用结构化工具调用降低解析脆弱性。

## 6. 测试策略

- 使用固定小数据和 mock LLM， routine tests 不调用付费 API；
- 验证工具参数错误、缺失文件、空数据和超大输出等失败路径；
- 验证工具白名单和数据目录越界保护；
- 用黄金样例检查报告必须携带证据 ID；
- 将模型适配器契约测试与具体 Agent 行为测试分开。

## 7. 计划代码结构

后续代码放置在 `code-dsext/chapter7/`：

```text
chapter7/
├── industrial_agents/
│   ├── agents/
│   ├── llms/
│   ├── tools/
│   └── schemas/
├── tests/
├── examples/
└── README.md
```

本阶段仅创建 `chapter7` 目录。

## 8. 验收标准

- 新增模型或工具无需修改 Agent 核心流程；
- 工具调用具有类型校验、白名单和审计记录；
- 测试覆盖一条成功路径和至少三类失败路径；
- 生产系统写操作不在框架默认能力范围内。
