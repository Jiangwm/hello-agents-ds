# 第九章 DSExt：跨班次良率波动长程调查

本目录把原章的上下文工程、`ContextBuilder`、`NoteTool`、`TerminalTool` 和三日长程工作流映射为一个**离线、只读、可恢复**的良率调查示例。场景说明见[《第九章 DSExt：跨班次良率波动长程调查》](../../docs/chapter9/Chapter9-DSExt-LongHorizonYieldInvestigation.md)。实现只使用 Python 标准库，无外部依赖。

## 场景与原章映射

| 原章示例/主题 | 本章实现 |
| --- | --- |
| `01_context_builder_basic.py` | `InvestigationContextBuilder` 按目标、阶段、问题和笔记选择最小上下文，并去除原始大表字段。 |
| `02_context_builder_with_agent.py` | `LongHorizonYieldInvestigation` 在每个调查阶段装配上下文；本示例不调用在线模型。 |
| `03_note_tool_operations.py`、`04_note_tool_integration.py` | `InvestigationNoteStore` 保存事实、假设、决定、待办、风险和阶段摘要。 |
| `05_terminal_tool_examples.py` | `ReadOnlyWorkspace` 只读列目录、检查 JSON、统计质量 CSV，并为每次读取登记哈希证据。 |
| `06_three_day_workflow.py` | `day_1_scope()` → `day_2_hypotheses()` → `day_3_validation_plan()`；`week_later_review()` 演示跨会话复盘。 |
| `codebase_maintainer.py` | `LongHorizonYieldInvestigation` 维护状态、恢复笔记、压缩上下文和人工审批闸门。 |

## 实际目录

```text
chapter9/
├── context/
│   ├── __init__.py
│   └── builder.py                 # InvestigationContextBuilder / ContextBuildResult
├── notes/
│   ├── __init__.py
│   └── store.py                   # NoteRecord / InvestigationNoteStore
├── tools/
│   ├── __init__.py
│   └── read_only_workspace.py     # 只读文件访问、哈希证据和审计
├── tests/
│   └── test_chapter9.py           # 核心契约、HITL、恢复与失效测试
├── workflows/
│   ├── __init__.py
│   └── investigation.py           # InvestigationTarget / 长程工作流
├── workspace_sample/
│   ├── data_dictionary.json       # 脱敏字段字典
│   ├── quality_profile.csv        # 调查窗口样例
│   └── week_later_quality.csv     # 一周后复盘样例
└── run_long_horizon_investigation.py  # CLI 入口
```

## 组件职责与 API

- `InvestigationTarget`：校验调查 ID、目标、产品、产线和 ISO 日期范围。
- `LongHorizonYieldInvestigation(data_root, state_dir, target)`：加载/创建外置状态；提供 `day_1_scope()`、`day_2_hypotheses()`、`day_3_validation_plan(approver=None, approved=None, comment="")`、`week_later_review()`、`build_context(force_compression=False)` 和 `get_report()`。
- `InvestigationContextBuilder(max_notes=12, max_chars=6000)`：按笔记类型、当前问题相关度和更新时间选择当前有效事实、已确认决定及活动中的假设/待办/风险；只保留最近 4 条工具摘要，超过上限时继续压缩。
- `InvestigationNoteStore(storage_path)`：原子保存 `investigation_notes.json`，按场景、类型和状态查询并校验六类笔记契约。
- `ReadOnlyWorkspace(data_root, state_dir)`：允许 `.json`、`.jsonl`、`.csv`、`.md`、`.txt`；提供 `list_files()`、`inspect_json()`、`inspect_csv_metadata()`、`profile_quality_csv()`、`compare_quality_factors()`、`validate_evidence()` 和 `validate_all_evidence()`。质量画像和因子比较可按产品、产线及 ISO 日期范围筛选。

## 数据字段与脱敏声明

`workspace_sample/data_dictionary.json` 与两个 CSV 使用教学用脱敏标识。字段包括：`inspection_date`、`product_id`、`line_id`、`shift`、`material_code`、`equipment_id`、`program_version`、`temperature_c`、`quality_score`、`pass_rate`。代码也会识别同义字段（例如 `yield_rate`、`first_pass_yield`、`fpy`、`date` 等），但样例以 `pass_rate` 为良率字段。

样例中的产品、产线、物料、设备和程序版本均为虚构/脱敏值；数据字典明确标注“脱敏离线教学样例”。请勿替换为生产数据、客户数据、凭据或密钥。质量分组比较只报告观察性相关性差异，不提供因果结论。

## 快速运行

以下命令从仓库根目录执行。`data_root` 指向样例目录；`state_dir` 必须使用样例目录之外的空目录（下例使用系统临时目录）。CLI 默认 `--stage demo`。

### 1. 无审批：返回待审批退出码 2

```powershell
$DATA_ROOT = (Resolve-Path .\code-dsext\chapter9\workspace_sample).Path
$WAIT_STATE = Join-Path $env:TEMP "hello-agents-ch9-wait"
python -X utf8 .\code-dsext\chapter9\run_long_horizon_investigation.py `
  --data-root $DATA_ROOT --state-dir $WAIT_STATE --stage demo
$LASTEXITCODE       # 2；JSON status=awaiting_human_approval
```

该演示依次运行第一天、第二天和第三天；第三天只生成离线验证计划，不执行计划，也不生成生产结论。

### 2. 显式审批：完整三日 + 一周复盘

使用另一个空的外置状态目录，避免把“待审批”演示的历史状态混入完整演示：

```powershell
$APPROVED_STATE = Join-Path $env:TEMP "hello-agents-ch9-approved"
python -X utf8 .\code-dsext\chapter9\run_long_horizon_investigation.py `
  --data-root $DATA_ROOT --state-dir $APPROVED_STATE --stage demo `
  --approve --approver "教学审核人" --comment "批准离线验证计划"
$LASTEXITCODE       # 0；JSON status=completed，包含 day_1/day_2/day_3/week_later
```

`--approve` 只批准或拒绝**离线验证计划的范围**；即使批准，也不会写入 PLC、MES、DCS 或生产参数。

## 分阶段恢复

每次重新创建 `LongHorizonYieldInvestigation` 都会校验 `schema_version` 与必填状态字段，从 `<state_dir>/<investigation-id>.json` 恢复并递增 `recovery_count`；恢复时会立即重新计算已登记证据的哈希并隔离失效事实/假设。在一个新的空状态目录中可逐阶段执行：

```powershell
$STATE = Join-Path $env:TEMP "hello-agents-ch9-staged"
python -X utf8 .\code-dsext\chapter9\run_long_horizon_investigation.py --data-root $DATA_ROOT --state-dir $STATE --stage day1
python -X utf8 .\code-dsext\chapter9\run_long_horizon_investigation.py --data-root $DATA_ROOT --state-dir $STATE --stage day2
python -X utf8 .\code-dsext\chapter9\run_long_horizon_investigation.py --data-root $DATA_ROOT --state-dir $STATE --stage day3 --approve --approver "教学审核人"
python -X utf8 .\code-dsext\chapter9\run_long_horizon_investigation.py --data-root $DATA_ROOT --state-dir $STATE --stage week
python -X utf8 .\code-dsext\chapter9\run_long_horizon_investigation.py --data-root $DATA_ROOT --state-dir $STATE --stage report
```

阶段参数只有 `day1`、`day2`、`day3`、`week`、`demo`、`report`。阶段机拒绝跳步：`day2` 需要第一天完成，`day3` 需要第二天完成，`week` 需要人工批准验证计划。`day3` 省略 `--approve` 时保持 `awaiting_human_approval`；`--approver` 在提供 `--approve` 或 `--no-approve` 的人工决定时必须是非空字符串。

## 状态、笔记、证据与审计产物

程序只向外置 `state_dir` 写入：

```text
<state_dir>/
├── <investigation-id>.json          # schema_version=1；阶段、事实、假设、待办、风险、决定、计数
├── notes/investigation_notes.json   # 机器可读的六类 NoteRecord
└── workspace/
    ├── evidence_index.json          # evidence_id → path/hash/status/summary
    └── audit.jsonl                  # 每次读取、校验的 path/time/hash/operation/summary
```

证据初始状态为 `valid`；`validate_evidence()`/`validate_all_evidence()` 重新计算 SHA-256，文件变化变为 `invalidated`，文件缺失或被策略拒绝变为 `missing`。每次跨会话恢复及一周后复盘都会校验证据，并将依赖失效证据的事实标记为 `invalidated`、假设标记为 `needs_review`，对应 NoteRecord 置为 `superseded`，避免继续进入活动上下文。`--stage report` 以 JSON 输出当前报告及 `state_path`、`audit_log_path`。

## 六类笔记契约

所有记录共有 `id`、`scenario_id`、`note_type`、`status`、`title`、`content`、`created_at`、`updated_at`；`status` 为 `active`、`resolved`、`superseded` 或 `archived` 之一。类型专属字段如下：

| `note_type` | 必需字段与约束 |
| --- | --- |
| `fact` | `evidence_ids`：非空字符串列表。 |
| `hypothesis` | `supporting_evidence_ids`、`counter_evidence_ids`（可为空列表）；`missing_data`、`next_action`：非空字符串。 |
| `decision` | `human_confirmed` 必须为 `true`；`approver` 非空。 |
| `todo` | `owner`、`due_date` 非空；`dependencies` 为字符串列表（可为空）。 |
| `risk` | `severity` 为 `low`/`medium`/`high`/`critical`；`mitigation` 非空。 |
| `summary` | `stage` 非空；`retained_note_ids` 可为空列表；`compressed_count` 为非负整数。 |

## 验收对应

- **中断/恢复**：分阶段命令启动新会话，状态文件和笔记恢复调查目标、事实、假设、待办；报告中的 `recovery_count`、`note_counts` 可核对。
- **最小上下文**：`InvestigationContextBuilder` 去除 `raw_data`、`rows`、`records` 等原始字段，按 `max_notes`/`max_chars` 选择笔记；第三天强制压缩并记录 `summary`。
- **机器可读笔记**：`InvestigationNoteStore` 对六类笔记及状态做字段校验，JSON 可直接读取。
- **文件变更失效**：证据保存路径和 SHA-256；每次恢复和一周后复盘都会批量校验，报告失效证据并更新相关笔记状态。
- **长程闭环**：第一天界定异常、第二天形成假设、第三天生成并等待人工批准的验证计划、一周后读取 `week_later_quality.csv` 复盘；审批决定与压缩摘要均留在状态/笔记/审计产物中。

## 验证命令

从仓库根目录运行：

```powershell
python -X utf8 -m unittest discover -s code-dsext\chapter9\tests -p "test_*.py" -v
python -m compileall -q code-dsext\chapter9
```

测试不需要网络或付费 API；运行前请确保使用独立的 `state_dir`，避免把验证产物写入 `workspace_sample`。

## 退出码

对已输出 JSON 的正常 CLI 调用，语义退出码为：

| 码 | 常量 | 含义 |
| ---: | --- | --- |
| `0` | `EXIT_SUCCESS` | 阶段完成、批准的离线计划或报告。 |
| `2` | `EXIT_AWAITING_APPROVAL` | 尚未提供人工决定，状态为 `awaiting_human_approval`。 |
| `3` | `EXIT_BLOCKED` | 数据不足、非法阶段跳转、`validation_plan_rejected` 或 `*_blocked`。 |
| `4` | `EXIT_ERROR` | 文件、参数值、JSON 或类型错误。 |

参数解析失败时 `argparse` 也可能使用退出码 `2`；应以 JSON 中的 `status` 和命令输出区分该情况。

## 安全边界

- `data_root` 必须是存在的、非符号链接目录；所有数据访问均为**绝对只读**，拒绝绝对路径、`..` 越界、符号链接、敏感标记（`.env`、`secret`、`credential`、`.key`、`.pem`）和未允许后缀。
- `state_dir` 必须位于 `data_root` 外部；状态、笔记、证据索引和审计日志只能写入该外置目录，原始数据根目录不会被工作流写入。
- CSV 统计前先通过 `inspect_csv_metadata()` 流式检查表头、行数和最多 5 行的派生采样指标；样本原始值不会进入状态或上下文。
- 产品、产线和调查日期会同时约束质量画像、因子比较及工作流事实；缺少目标筛选字段时拒绝生成混合对象结论。
- 工具不执行 shell/任意命令，不访问 PLC、MES、DCS、生产参数或密钥；本章没有网络模型调用。
- 分组差异和一周后结果仅是观察性相关性，不是因果证明；`minimum_next_action` 只提出离线受控核查。
- 第三天输出的是待人工批准的验证**计划**，不是已执行的实验，更不是确认结论；人工闸门只决定离线计划范围。
