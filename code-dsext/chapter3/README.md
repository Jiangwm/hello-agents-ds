# 第三章：设备维修日志语义检索与归类

本示例对应
[`Chapter3-DSExt-MaintenanceLogSemantics.md`](../../docs/chapter3/Chapter3-DSExt-MaintenanceLogSemantics.md)，
把原章的 N-gram、BPE、词向量、Transformer 与 Qwen 教学内容映射到一个可离线运行、
可审计的维修日志实验。默认流程只使用 Python 标准库，不下载模型、不调用付费 API，
也不连接生产系统；可选的本地 Qwen 实验延续原章 `torch + transformers` 调用方式。

## 目录

```text
chapter3/
├── maintenance_log_system.py
├── ngram_log_baseline.py
├── tokenizer_inspection.py
├── embedding_retrieval.py
├── llm_event_extraction.py
├── generate_sample_data.py
├── data/
│   ├── maintenance_logs.jsonl
│   └── sample_model_response.json
└── tests/
    └── test_maintenance_log_system.py
```

## 快速运行

在仓库根目录查询一条维修描述。默认使用按时间切分后的 80 条训练日志构建索引，
返回 Top-5 案例、五类预归类结果和证据约束抽取：

```bash
python -X utf8 code-dsext/chapter3/maintenance_log_system.py query \
  "L-01伺服X轴跟随偏差，重新校准编码器后恢复"
```

输出便于其他程序处理的 JSON：

```bash
python -X utf8 code-dsext/chapter3/maintenance_log_system.py query \
  "CNC-01主轴发热，清理风道后恢复" --format json
```

对 20 条时间留出日志执行离线归类评估：

```bash
python -X utf8 code-dsext/chapter3/maintenance_log_system.py validate
```

生成 Top-5 人工相关性标注模板：

```bash
python -X utf8 code-dsext/chapter3/maintenance_log_system.py \
  annotation-template "主轴温度高，清理风道后恢复"
```

标注人应为每个结果填写：

- `relevance`：`相关`、`部分相关` 或 `不相关`；
- `error_type`：`文字相似但原因不同`、`措辞不同但故障相同`、
  `设备不一致` 或 `其他`；
- `annotator` 与 `notes`：记录复核人及判断依据。

## 分层实验

### 1. N-gram 基线

```bash
python -X utf8 code-dsext/chapter3/ngram_log_baseline.py --top 10
```

程序统计 unigram、bigram 和 trigram，并明确记录三个限制：组合稀疏、新型号或缩写
形成未登录词、局部窗口难以连接远距离的原因与措施。Transformer 的注意力可以建模
更远的关联，但模型输出仍不能脱离原文证据。

### 2. 工业分词检查

```bash
python -X utf8 code-dsext/chapter3/tokenizer_inspection.py \
  "CNC-01的X轴振动达到7.2 mm/s，PLC报警"
```

普通正则分词会把 `CNC-01`、`X轴` 和 `mm/s` 拆开。工业保护分词保留设备型号、
轴名称、数值、单位，以及 `I/O` 一类带斜杠的工业缩写，并把破坏情况写入检查报告。

### 3. 向量检索与归类

```bash
python -X utf8 code-dsext/chapter3/embedding_retrieval.py \
  "液压缸动作爬行，排气后恢复" --format json
```

系统执行以下确定性流程：

1. 归一化“发热/过温”“跟随偏差/跟随误差”“校准/标定”等教学同义表达；
2. 组合工业词元与中文二、三字特征；
3. 在训练日志上计算 TF-IDF 稀疏向量；
4. 使用余弦相似度返回 Top-K；
5. 对达到相似度阈值的五类近邻加权投票，输出机械、电气、液压、工艺或未知。

每条结果保留 `score`、`log_id`、`raw_text`、设备、类别、措施和时间。这里的
TF-IDF 是可解释的轻量语义近似，不等同于生产级向量模型。近邻投票置信度也不是故障
发生概率。

文档中的“词袋、静态词向量、Transformer 三路表征比较”属于挑战任务，本核心系统
没有打包三套模型或宣称比较结论。扩展实验可分别复用原章 `Word_Embedding.py` 与
`Transformer.py`，在保持同一时间切分和 Top-5 人工标注的前提下替换编码器。

### 4. 证据约束结构化抽取

离线抽取：

```bash
python -X utf8 code-dsext/chapter3/llm_event_extraction.py \
  "CNC-01主轴发热，清理风道后恢复"
```

生成可交给模型的提示词：

```bash
python -X utf8 code-dsext/chapter3/llm_event_extraction.py \
  "CNC-01主轴发热，清理风道后恢复" --prompt
```

审计一个已保存的模型响应，不需要安装模型依赖：

```bash
python -X utf8 code-dsext/chapter3/llm_event_extraction.py \
  "CNC-01主轴发热，清理风道后恢复" \
  --model-response code-dsext/chapter3/data/sample_model_response.json
```

可选地运行与原章 `Qwen.py` 同类的本地 Hugging Face 模型：

```bash
python -m pip install torch transformers
python -X utf8 code-dsext/chapter3/llm_event_extraction.py \
  "CNC-01主轴发热，清理风道后恢复" \
  --run-local-model --model-id Qwen/Qwen1.5-0.5B-Chat
```

本地模型命令可能从 Hugging Face 下载权重，并占用较多内存或显存；它不是默认测试
路径。模型输出先解析为 JSON，再经过与离线流程相同的证据审计；审计未通过时退出码
为 `2`。

抽取字段为设备、现象、候选原因、处理措施和不确定项。每个事实字段同时保存
`value`、原文 `evidence` 与 `certainty`。`audit_extraction()` 会拒绝无法在原文中
定位的事实；推测原因的值必须与原文证据一致。模型提出但原文未给出的原因必须标为
`speculation`，且不能通过证据审计；离线抽取器将其保留为 `unknown`。

## 数据与切分

- `maintenance_logs.jsonl` 是 2026-07-26 由 25 个脱敏教学事件生成的 100 条合成
  日志；每个事件含 4 条近重复记录，五个类别各 20 条。它不来自真实企业、设备或
  公开数据集。
- 必填字段包括 `log_id`、`equipment_type`、`event_time`、`raw_text`、
  `fault_category`、`action_taken` 与 `downtime_minutes`；还提供关键词、故障码和
  `source_event_id`。
- 默认按时间切分：2026 年 1～4 月的 20 个事件、80 条日志用于训练，5 月的
  5 个事件、20 条日志用于验证。4 条同源近重复记录始终作为一组切分；
  `source_event_id` 和故障码均不会跨集合。
- 也可在主命令前使用 `--split equipment` 按设备类型留出，例如：
  `maintenance_log_system.py --split equipment validate`。
- 时间留出集当前可得到 20/20 类别命中，但这是同一组人工模板生成的教学数据，
  不是泛化能力证明，也不能替代人工 Top-5 相关性与错误类型审查。

如需重新生成完全相同的样例数据：

```bash
python -X utf8 code-dsext/chapter3/generate_sample_data.py
```

该命令会覆盖本章 `data/maintenance_logs.jsonl`。

## 测试

```bash
python -m unittest discover -s code-dsext/chapter3/tests -v
```

16 项测试覆盖工业符号保护、100 条数据校验、同源近重复切分、Top-5 可追溯结果、
五类近邻归类、真实日志 N-gram 概率与模式解释、结构化抽取幻觉审计和统一 CLI
JSON 输出。

## 安全边界

该系统用于离线教学、历史案例检索与故障类别预归类，不替代联锁系统、设备工程师、
工艺工程师或正式检修规程。系统没有 PLC、MES、DCS 或设备写控制能力，不给出生产
参数变更指令。相似案例、类别与候选原因都属于待人工复核的信息；最终根因结论必须由
具备权限的人员结合现场测量、规程和完整事件窗口确认。
