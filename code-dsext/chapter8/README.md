# 第八章：设备知识与维修记忆助手

本示例把原章的 Memory、RAG、高级检索与文档问答改造成一个可离线运行的工业
教学系统。它只读取本目录内的脱敏样例，回答必须带来源；模型推测只能进入工作
记忆，未经人工确认不会写入长期记忆。

## 目录

```text
chapter8/
├── ingestion/                       # Markdown、CSV、可选 PDF 摄取
├── retrieval/                       # 治理过滤、关键词/稀疏向量混合检索
├── memory/                          # 工作、情景与语义记忆
├── assistant/                       # 带引用回答和拒答策略
├── evaluations/                     # Recall@K、MRR 与治理指标
├── data/
│   ├── documents/                   # 当前版与过期版设备手册
│   ├── equipment_registry.csv       # 脱敏设备台账
│   ├── procedures.csv               # 表格化权威点检规程
│   ├── work_orders.csv              # 已关闭历史工单
│   ├── evaluation_cases.json
│   └── scenario.json                # 固定调查日期与数据分级
├── tests/
├── equipment_knowledge_assistant.py # CLI
├── governance.py                    # 敏感字段脱敏
└── schemas.py                       # 知识、引用、回答与审计契约
```

## 快速运行 🚀

在仓库根目录执行，无需 API Key：

```bash
python -X utf8 code-dsext/chapter8/equipment_knowledge_assistant.py demo
```

单独查询官方检查顺序：

```bash
python -X utf8 code-dsext/chapter8/equipment_knowledge_assistant.py ask \
  "查找 CNC-03 主轴过热的官方检查顺序" \
  --equipment-id CNC-03 --format json
```

低于相关性阈值、设备编号冲突、未知设备或无访问权限时，命令明确拒答并返回退出码
`2`；配置、数据或参数错误返回 `1`；正常回答返回 `0`。

## 四类典型任务

`demo` 在固定脱敏数据上运行场景文档中的四类任务：

1. 查询 CNC-03 主轴过热的当前官方检查顺序；
2. 汇总过去三个月相似告警与已验证原因；
3. 比较当前现象与最近两次工单的共同点；
4. 只列仍开放的假设，不复述已排除项。

回答使用 `[文档事实]`、`[历史经验]`、`[共同点]` 或
`[尚未确认的假设]` 标签。共同现象只用于排查排序，不写成根因结论。

## 摄取契约

### Markdown

Markdown 使用不依赖 PyYAML 的简单 frontmatter，必填字段如下：

```yaml
---
document_id: CNC-X3-SPINDLE-MANUAL
title: CNC-X3 主轴维护手册
version: 2.1
effective_date: 2026-06-01
status: effective
source_type: authoritative
equipment_models: CNC-X3
allowed_roles: equipment_engineer,maintenance_supervisor
license: internal-training
---
```

正文支持 `<!-- page: 12 -->` 与
`<!-- paragraph-id: official-check-sequence -->` 定位标记。标题层级写入
`title_path`；连续 Markdown 表格和警告框作为完整段落保留。

### CSV

`procedures.csv` 和 `work_orders.csv` 共用表格摄取契约。权威规程使用
`source_type=authoritative`，历史工单使用 `source_type=historical` 并提供
`event_date`。每行保留版本、状态、设备型号、设备编号、角色、许可与段落 ID。

### PDF

PDF 为可选能力：

```bash
python -m pip install pypdf
```

`IngestionPipeline.ingest_pdf()` 读取 PDF，并从同名
`<file>.pdf.meta.json` 获取上述治理元数据；页码来自 PDF 页。未安装 `pypdf` 时
会给出明确错误，默认演示和测试不依赖它。

所有路径必须位于配置的 `data_root` 内。邮箱和中国大陆手机号在建立索引前统一替换
为 `[REDACTED_EMAIL]` 与 `[REDACTED_PHONE]`。

## 检索与回答

检索顺序固定为：

1. 校验问题中的设备编号与显式设备范围；
2. 按设备型号/编号、角色、文档许可、状态、生效日期和来源类型过滤；
3. 对同一文档段落只保留最新有效版本；
4. 计算关键词覆盖分与离线 TF-IDF 稀疏向量余弦分；
5. 对结果重排并应用最低相关性阈值；
6. 用命中的原文组织回答，并逐条返回文档、版本、页码或段落 ID。

系统不调用 LLM 自由生成设备数值。查询原文只以 SHA-256 写入助手与检索审计，
避免在审计记录中重复保存可能敏感的问题文本。

## 记忆分层与 HITL

| 层 | 本示例实现 | 写入规则 |
| --- | --- | --- |
| 权威知识 | 有版本的手册和点检规程 | 只由受管数据文件摄取 |
| 情景记忆 | 单次设备调查与历史工单 | 候选记忆须显式审批 |
| 语义记忆 | 多次已关闭工单沉淀的稳定经验 | 至少两份不同工单，维修负责人审批 |
| 工作记忆 | 当前告警、证据、开放/排除假设、下一步 | 仅当前进程，不自动持久化 |
| 程序性记忆 | 手册/SOP 中的排查顺序 | 不能替代安全作业规程 |

生成候选记忆但不写文件：

```bash
python -X utf8 code-dsext/chapter8/equipment_knowledge_assistant.py remember \
  --session-id CASE-CNC03-001 --equipment-id CNC-03 --alert "主轴过热" \
  --evidence-id WO-CNC03-20260718 --hypothesis "冷却滤网再次堵塞" \
  --store C:/tmp/chapter8-memories.json --format json
```

确认写入时必须增加：

```text
--confirm --approval-id APR-CNC03-001 --approver 设备工程师甲 \
--reason "确认保存本次调查上下文"
```

写入后的开放假设仍保持 `open`，长期记忆固定
`authoritative=false`。检索按设备和角色隔离，并忽略过期记忆。

删除请求同样需要治理审批：

```bash
python -X utf8 code-dsext/chapter8/equipment_knowledge_assistant.py forget \
  --equipment-id CNC-03 --store C:/tmp/chapter8-memories.json \
  --approval-id DEL-CNC03-001 --approver 数据管理员甲 \
  --reason "响应数据删除请求"
```

确认、语义整合与删除都会写入同目录的 `*.audit.jsonl` 审计文件。

## 评估与验证

运行固定离线评估：

```bash
python -X utf8 code-dsext/chapter8/equipment_knowledge_assistant.py \
  evaluate --format json
```

报告包含 `recall_at_k`、`mrr`、设备过滤准确率、引用正确率、版本正确率、无依据
断言率、回答/拒答状态准确率，以及记忆确认门禁、写入准确率、设备隔离准确率和
过期记忆暴露率。`scenario.json` 将样例调查日期固定为 `2026-07-30`，所以
“三个月”窗口可重复验证。

运行全部测试和编译检查：

```bash
python -X utf8 -m unittest discover \
  -s code-dsext/chapter8/tests -p "test_*.py" -v
python -X utf8 -m compileall -q code-dsext/chapter8
```

测试覆盖当前版本优先、引用定位、低相关拒答、设备与角色隔离、四类典型任务、
Markdown/CSV/PDF 摄取、敏感字段脱敏、候选记忆确认、语义记忆、过期过滤、删除
请求、审计记录和 CLI 退出码。

## 安全边界 🔒

- 默认离线、标准库优先；PDF 的 `pypdf` 是唯一可选依赖。
- 不连接 PLC、MES、DCS，不复位告警，不修改配方、参数或设备状态。
- 手册步骤和历史工单只提供决策支持；上锁挂牌、试运行和现场操作仍执行正式 SOP。
- 历史共同点是相关性证据，不等于因果关系或最终根因。
- 跨设备取数、知识授权、语义经验沉淀、删除请求和最终维修结论均保留人工审批。
- 数据均为人工构造的脱敏教学样例，评估结果不代表真实现场泛化能力。
