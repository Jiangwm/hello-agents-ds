# 第七章：工业数据分析智能体框架

本章把前几章的零散工业案例抽象为轻量 `industrial_agents` 教学框架。它面向
脱敏的离线 CSV/Parquet 数据，通过稳定接口组合模型适配器、Agent 范式和确定性
工具；默认不联网、不连接 PLC/MES/DCS，也不提供生产参数写入能力。

## 目录

```text
chapter7/
├── industrial_agents/
│   ├── agents/          # Summary、ReAct、Plan-Solve、Reflection、Function Call
│   ├── llms/            # mock 与 OpenAI 兼容模型适配器
│   ├── schemas/         # 消息、配置、任务、证据、审计和分析结果
│   └── tools/           # 工具契约、注册表和五类内置工具
├── data/                # 固定脱敏时序、规范、数据字典和历史案例
├── examples/
│   └── run_framework_demo.py
├── tests/
└── README.md
```

## 快速运行 🚀

在仓库根目录执行，无需 API Key：

```bash
python -X utf8 code-dsext/chapter7/examples/run_framework_demo.py \
  --mode all --show-audit
```

也可单独观察一种范式：

```bash
python -X utf8 code-dsext/chapter7/examples/run_framework_demo.py --mode react
python -X utf8 code-dsext/chapter7/examples/run_framework_demo.py --mode plan
python -X utf8 code-dsext/chapter7/examples/run_framework_demo.py --mode function
```

完整完成返回退出码 `0`，证据不足或达到有界执行上限返回 `2`，配置或执行错误返回
`1`。

## 核心契约

| 契约 | 职责 |
| --- | --- |
| `IndustrialMessage` | 保存角色、内容、UTC 时间、设备范围、证据 ID 和结构化工具调用 |
| `IndustrialAgentConfig` | 固定模型、最大轮次、工具/字段白名单、数据根目录和结果上限 |
| `IndustrialAgent` | 提供统一 `run(task) -> AnalysisResult` 生命周期 |
| `IndustrialTool` | 声明用途、输入/输出模式、风险等级和人工审批要求 |
| `ToolRegistry` | 完成注册、发现、参数校验、白名单执行和成功/失败审计 |
| `AnalysisResult` | 输出结论、证据、置信度、限制、下一步和完整审计记录 |

`LLMAdapter` 隐藏模型提供商差异。`MockLLMAdapter` 支持离线、可重复的脚本测试；
`OpenAICompatibleLLMAdapter` 可连接云端兼容接口或本地兼容服务，统一超时，并在
异常中保留 provider、model、调用元数据和原始异常链。

CSV 路径只使用 Python 标准库。Parquet 和真实兼容模型均为可选能力：

```bash
python -m pip install pandas pyarrow   # 仅在读取 Parquet 时需要
python -m pip install openai           # 仅在调用兼容模型服务时需要
```

## 五类工具

| 工具名 | 确定性输出 | 风险与边界 |
| --- | --- | --- |
| `dataset_profile` | 字段、推断类型、总行数/采样行数、缺失率、数值摘要和时间范围 | 只读、结果截断 |
| `trend_analysis` | 按原始/小时/天重采样，支持滚动统计和字段分组趋势 | 只读、字段白名单 |
| `anomaly_detection` | 总体 Z 分数异常候选及检测参数 | 只读、结果是候选而非根因 |
| `specification_lookup` | 跨本地规范、数据字典和案例的多源检索 | 只读、本地 JSON |
| `evidence_export` | 本地只读 JSON 分析包 | 高风险、必须人工批准、拒绝覆盖 |

每次调用都生成 `AuditRecord`。成功记录证据 ID，失败记录异常类型与信息；高风险
调用还记录审批 ID、审批人、审批时间和理由。重复输入产生相同证据 ID，但每次调用
拥有不同审计 ID。新增工具只需构造 `IndustrialTool` 并注册到 `ToolRegistry`，
白名单 Schema 会自动提供给模型，无需修改 Agent 核心流程。

配置采用安全默认值：空 `tool_allowlist` 表示不允许任何工具。示例显式开放四个
只读工具；`evidence_export` 即使进入白名单，也必须在调用时传入包含审批 ID、
审批人、时间和理由的 `ApprovalContext`。

## 五种 Agent 范式

1. `SummaryAgent`：先取得确定性数据画像，再用一次模型调用生成摘要。
2. `ReActIndustrialAgent`：在最大轮次内逐次选择白名单工具并收集证据。
3. `PlanSolveIndustrialAgent`：先生成 JSON 调查计划，再维护每一步的
   `running/completed/failed/blocked` 状态。
4. `ReflectionIndustrialAgent`：复用草稿证据，检查证据引用、因果措辞和未知项。
5. `FunctionCallIndustrialAgent`：消费结构化 `tool_calls`，避免解析自由文本动作。

模型在未调用工具时直接给出的结论会标记为 `partial`，不能伪装成已完成的分析。
统一证据校验会检查未知证据 ID、无来源数字和无保留因果措辞；校验失败时降级为
`partial`。最终 Markdown 固定列出证据 ID、来源和数据范围。

## 样例数据

- `industrial_timeseries.csv`：E-101、E-102 的 12 条脱敏教学时序；E-101 在
  13:00—14:00 含可解释异常。
- `process_specs.json`：压力、温度和振动的教学规范。
- `data_dictionary.json`：字段类型、单位与含义。
- `cases.json`：时间共现案例及“不能直接确认根因”的限制。

所有内容均为人工构造的教学标识，不来自真实企业，不能用于生产控制或工艺放行。

## 验证

```bash
pytest -q code-dsext/chapter7/tests
python -X utf8 -m unittest discover -s code-dsext/chapter7/tests -p "test_*.py"
python -X utf8 -m compileall -q code-dsext/chapter7/industrial_agents
```

测试使用固定小数据和 mock LLM，不访问付费 API。覆盖模型适配器契约、五种 Agent
行为、证据 ID、结构化工具调用，以及参数错误、缺失/空文件、字段拒绝、路径越界、
输出截断、CSV 总行数、空 Parquet、工具 Schema 下发、工具白名单、证据校验和
未审批导出等失败路径。

## 安全边界 🔒

- 数据路径必须是 `data_root` 内的相对路径；导出目录也必须位于该根目录。
- 模型只能选择注册且显式允许的工具，不能拼接任意 Python、SQL 或 Shell 执行。
- 除经人工批准的本地证据包外，框架没有写工具；证据包不覆盖已有文件。
- 异常检测、趋势和历史共现只形成排查候选，不等同于因果结论。
- 跨域取数、生产试验、工艺变更和最终根因必须由相应领域负责人复核、批准并签署。
