# 第五章：低代码质量日报与异常闭环

本示例对应
[`Chapter5-DSExt-LowCodeQualityWorkflow.md`](../../docs/chapter5/Chapter5-DSExt-LowCodeQualityWorkflow.md)，
实现“数据接入—确定性统计—知识检索—报告生成—人工审批—消息分发—异常关闭”的
离线工作流。程序仅使用 Python 标准库，不调用外部模型/API，不连接生产 MES，也不向
PLC、MES、DCS 或企业协作系统写入数据。

## 目录

```text
chapter5/
├── quality_daily_workflow.py
├── quality_metrics.py
├── workflows/
│   └── quality_daily_workflow.json
├── knowledge/
│   ├── quality_standards.json
│   └── defect_cases.json
├── prompts/
│   └── quality_daily_report.md
├── data/
│   ├── batches.csv
│   ├── inspections.csv
│   └── defects.csv
└── tests/
    └── test_quality_daily_workflow.py
```

`quality_daily_workflow.json` 是可执行参考系统读取的低代码节点契约，不是某个平台的
原生导出文件。每个节点给出 n8n 映射，迁移时可以逐节点替换为 n8n Trigger、Code、
IF、Data Store、LLM Chain、Wait/Form 和通知连接器，同时保留 Python 统计服务作为
唯一数值来源。

## 快速运行

在仓库根目录生成 `2026-07-25` 的待审批日报：

```bash
python -X utf8 code-dsext/chapter5/quality_daily_workflow.py run \
  --date 2026-07-25 \
  --output-dir .tmp/chapter5-quality-run
```

查看状态。此时应为 `pending_approval`，且不会出现批准后的通知文件：

```bash
python -X utf8 code-dsext/chapter5/quality_daily_workflow.py show \
  --run-dir .tmp/chapter5-quality-run
```

质量工程师批准后，系统生成 `report_v2.md` 和离线分发证据
`approved_notification.md`：

```bash
python -X utf8 code-dsext/chapter5/quality_daily_workflow.py review \
  --run-dir .tmp/chapter5-quality-run \
  --decision approve \
  --reviewer "质量工程师" \
  --comment "同意发布并启动异常跟踪"
```

人工可通过 `--edited-summary-file` 修订定性摘要。修订内容不得包含数字或百分号；
产量、一次合格率、Top 缺陷和异常阈值等数值区块始终由统计节点重新装配。

日报发布后，逐个登记异常单的处置结论与外部证据编号：

```bash
python -X utf8 code-dsext/chapter5/quality_daily_workflow.py close \
  --run-dir .tmp/chapter5-quality-run \
  --exception-id EXC-20260725-001 \
  --reviewer "质量工程师" \
  --resolution "已复核原始记录并完成纠正措施验证" \
  --evidence-ref "QMS-CAR-001"
```

若审批决定为 `reject`，系统保存驳回版本和审批记录，但不会生成发布通知。`run`
正常生成待审批日报时退出码为 `0`；数据校验阻断时为 `2`；文件损坏、参数无效或非法
状态转换时为 `1`。

## 数据与指标契约

三张 CSV 以 `batch_id` 关联，时间戳按车间本地时间 `Asia/Shanghai` 解释：

| 文件 | 关键字段 | 用途 |
| --- | --- | --- |
| `batches.csv` | 日期、产品、产线、班次、产量、MES 接收时间 | 定义日报批次与分组 |
| `inspections.csv` | 检验数、一次合格数、记录时间 | 计算一次合格率与迟到记录 |
| `defects.csv` | 缺陷编码、名称、数量 | 计算分组缺陷率和 Top-5 |

核心口径如下：

- 一次合格数为 `first_pass_qualified_qty` 合计；一次合格率为一次合格数除以检验数；
- 分组缺陷率为分组缺陷数除以分组产量，分别按产线、产品和班次计算；
- Top-5 按当日缺陷数量降序，环比为当日与前一自然日缺陷率之差；
- 异常批次同时核对产品最低一次合格率和最高缺陷率，阈值来自
  `quality_standards.json`；
- 迟到记录定义为日报次日 `08:00` 后才完成的检验记录。

当前工作流要求目标日至少有 3 个批次、前一自然日至少有 1 个环比批次、必填单元格
缺失率不高于 `2%`、迟到记录不超过 2 条。它还校验表头、日期与时间格式、非负数量、
批次关联、检验数与产量一致性，以及日报/环比批次的缺陷明细合计与一次不合格数
一致性。全部批次一次合格时，`defects.csv` 可以只有表头。任一校验失败都会生成
`data_owner_notification.md` 并显式终止，不生成指标或“正常”结论。

样例包含前一日和当日共 10 个批次，是 2026-07-28 人工构造的 v1 脱敏教学数据，
不来自真实企业或公开数据集。质量标准、历史案例、阈值和证据编号均为教学标识，不能
作为生产放行、工艺调整或现场处置依据。

## 输出与审计

一次正常运行会保存：

- `data_quality.json`：输入校验结果、缺失率、迟到记录和文件摘要；
- `metrics.json`：确定性指标、分组结果、Top-5、异常批次和 `metric_hash`；
- `knowledge_context.json`：异常批次匹配的标准与历史案例；
- `exceptions.json`：异常单、责任角色、期限、状态和关闭证据；
- `report_v1.md` / `report_v2.md`：待审批与审批后的版本；
- `state.json`：工作流状态转换与异常闭环状态；
- `audit.json`：数据日期、输入文件摘要、工作流/提示词版本、节点轨迹和审批人；
- `approved_notification.md`：只有人工批准后才生成的离线分发证据；
- `exception_closure_summary.md`：逐项关闭异常后的闭环台账。

提示词节点只生成不含数字的定性摘要。表格、排名、阈值命中和通知中的指标均从
`metrics.json` 装配，并用 `metric_hash` 绑定输入版本；审批前会重新计算摘要并与
`audit.json` 比对，指标文件被改动时拒绝发布。

## 平台选型

本教学实现选择 n8n 作为主编排器：

- n8n 适合自托管定时任务、脚本/内网服务调用、条件分支、审批等待、通知连接器和
  逐节点运行记录；
- Dify 适合知识库与模型节点，可作为受控的叙述服务，但本场景的数据校验、长等待
  审批和多系统编排仍由 n8n 统一管理；
- FastGPT 适合质量标准与缺陷案例问答，本示例只需要受限定的批次上下文检索，不把
  它作为全流程状态机；
- Coze 适合一线人员的对话入口，可读取已批准日报，但不承担本示例的私有数据处理、
  确定性统计和审批审计主链。

真实部署还需由企业复核数据驻留、连接器权限、私有化方式、可观测性、成本和版本迁移
策略；平台连接器应使用最小权限，分发前必须保留不可绕过的人工审批。

## 验证

```bash
python -X utf8 -m unittest discover \
  -s code-dsext/chapter5/tests -p "test_*.py" -v
```

测试覆盖正常指标、Top-5 与异常批次、输入字段失败、敏感字段阻断、审批驳回、
批准后分发、非数值修订、异常逐项关闭、源文件只读和版本审计。
