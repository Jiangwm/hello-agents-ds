# 第四章：生产异常根因分析智能体

本示例对应
[`Chapter4-DSExt-ProductionAnomalyRCA.md`](../../docs/chapter4/Chapter4-DSExt-ProductionAnomalyRCA.md)，
使用同一组脱敏教学数据实现 ReAct、Plan-and-Solve 与 Reflection 三种范式。
系统仅依赖 Python 标准库，所有数值由固定路径的只读工具确定性计算，不调用外部
LLM/API，也不连接生产系统。

## 目录

```text
chapter4/
├── industrial_tools.py
├── react_rca_agent.py
├── plan_solve_rca_agent.py
├── reflection_report_agent.py
├── compare_paradigms.py
├── data/
│   ├── batch_master.csv
│   ├── process_timeseries.csv
│   ├── quality_results.csv
│   ├── events.csv
│   └── process_specs.csv
└── tests/
    └── test_production_anomaly_rca.py
```

## 快速运行

在仓库根目录运行 ReAct 探索式取证，并查看每轮
`Thought—Action—Observation`：

```bash
python -X utf8 code-dsext/chapter4/react_rca_agent.py --show-trace
```

运行先规划、再逐步执行的结构化排查：

```bash
python -X utf8 code-dsext/chapter4/plan_solve_rca_agent.py --show-trace
```

运行报告复核，查看复核前后的统一差异：

```bash
python -X utf8 code-dsext/chapter4/reflection_report_agent.py \
  --show-original --show-diff
```

横向比较同一案例 `RCA-P100-001` 的工具调用次数、关键证据完整度和查询可审计率：

```bash
python -X utf8 code-dsext/chapter4/compare_paradigms.py
```

对比表把“本范式新增工具调用”和“复用证据”分开统计；Reflection 新增调用为
`0`，复用 Plan-and-Solve 的 5 条不可变工具证据。

三个智能体默认使用同一正常组 `BATCH-N001,BATCH-N002` 和异常组
`BATCH-A001,BATCH-A002`。也可向单个智能体同时传入
`--normal-batches`、`--abnormal-batches` 和 `--focus` 分析自定义批次。

## 失败与证据不足分支

不存在的批次会形成结构化失败 Observation；ReAct 会记录失败后回到有效批次，
不会静默吞掉错误：

```bash
python -X utf8 code-dsext/chapter4/react_rca_agent.py \
  --scenario tool-failure --show-trace
```

证据不足案例只有每组两条工艺采样，且没有 P-200 的适用规范。智能体会把参数漂移
标记为 `insufficient`，报告状态为 `incomplete`，退出码为 `2`：

```bash
python -X utf8 code-dsext/chapter4/react_rca_agent.py \
  --scenario insufficient --show-trace
```

正常完成退出码为 `0`；数据文件损坏或参数无效为 `1`；证据不足或流程未完成为
`2`。

## 数据契约

全部 CSV 均以批次编号作为关联键，时间戳按车间本地时间
`Asia/Shanghai` 解释，数值单位写入字段名或规范表。

| 文件 | 关键字段 | 用途 |
| --- | --- | --- |
| `batch_master.csv` | 批次、产品、产线、班次、物料、程序版本、起止时间 | 定义分析窗口与批次上下文 |
| `process_timeseries.csv` | 时间、批次、温度、压力、速度、流量 | 计算参数均值、离散度与漂移 |
| `quality_results.csv` | 检验数、一次合格数、主缺陷、测量均值 | 比较一次合格率和缺陷影响 |
| `events.csv` | 时间、批次、事件类型、事件编码、描述 | 查询告警、换料、换班与版本事件 |
| `process_specs.csv` | 产品、参数、上下限、单位、规范编号、来源 | 核对漂移参数是否越过教学规范 |

这些文件均为 2026-07-26 人工构造的 v1 脱敏教学数据，不来自真实企业或公开
数据集。`TEACHING-DEMO-PROCESS-SPEC-v1` 只是教学标识，不是生产规范、报警设定
或 EHS 依据。

## 工具与证据契约

`IndustrialToolExecutor` 只注册以下四个工具：

- `get_batch_profile(batch_id)`：读取单批次主数据、质量结果和相邻事件；
- `compare_time_windows(normal_batch_ids, abnormal_batch_ids)`：校验同产品同产线
  后比较两组一次合格率和批次上下文；
- `detect_parameter_shift(normal_batch_ids, abnormal_batch_ids)`：按参数计算两组
  样本数、均值、正常组总体标准差、均值差和“均值差占规范跨度比例”；
- `lookup_process_spec(product_id, parameter)`：返回适用上下限、单位、规范编号
  和来源。

每次调用统一产生 `ToolObservation`，包含稳定的运行内证据 ID、`status`、
`query`、`data_range`、`source_files`、`data` 与 `error`。其中
`data_range` 明确记录批次生产窗口、工艺采样起止时间或规范适用范围；报告中的
数值事实必须引用 `EV-xxx`，`--show-trace` 会在每轮 Observation 后显示
`Data Range`；失败调用同样保留输入、`未取得数据` 范围与错误。

这里的“参数漂移排序”是确定性筛查规则，不是统计显著性检验：按均值差占规范跨度
比例降序排列；无规范时使用正常均值绝对值作为参考尺度。任一组少于 6 条工艺采样
时返回 `insufficient`，不得把排序写成根因确认。

## 三种范式

- ReAct 根据已取得的 Evidence 动态选择下一个只读工具；未知批次失败后可恢复，
  最多执行 8 步。
- Plan-and-Solve 先生成批次画像、窗口比较、漂移筛查、规范核对和候选根因形成
  计划，再保留每步 `success`、`insufficient`、`error` 或 `blocked` 状态；
  失败步骤不会被跳过。
- Reflection 只复核并修改标签、措辞、遗漏项和建议，不重新计算或改写工具事实；
  它检查证据引用、基线、因果措辞、批次引用、安全边界，以及物料与程序版本同时
  变化形成的混杂因素，并保留前后 diff。

最终报告固定包含事件影响、分组定义、证据与查询条件、候选排序与反证、待补数据和
人工审批实验。候选顺序只是排查优先级，不是因果概率；输出使用“已确认事实 /
数据支持的假设 / 尚无证据”三级标签。

## 验证

```bash
python -X utf8 -m unittest discover \
  -s code-dsext/chapter4/tests -p "test_*.py" -v
```

测试覆盖同案例质量差异和参数排序、未注册写工具拒绝、失败调用恢复、结构化计划、
证据不足、Reflection 修订差异与证据对象不变性。

## 安全边界

本示例只读本目录内的脱敏 CSV，不接受任意文件路径、命令或动态工具。工具箱没有
PLC、MES、DCS、配方或工艺参数写入能力。所有验证实验只作为待工程师审批的建议；
最终根因必须由工艺、质量和设备工程师共同复核后签署。
