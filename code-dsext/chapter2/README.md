# 第二章：规则驱动的泵组故障问诊助手

本示例对应
[`Chapter2-DSExt-RuleBasedFaultDiagnosis.md`](../../docs/chapter2/Chapter2-DSExt-RuleBasedFaultDiagnosis.md)，
把原章 `ELIZA.py` 的“顺序匹配 + 响应模板”扩展为透明、确定、可追溯的泵组故障
问诊系统。程序仅使用 Python 标准库，不调用大模型，不训练分类器，也不连接生产系统。

## 目录

```text
chapter2/
├── rule_based_diagnosis.py
├── rules/
│   └── pump_fault_rules.json
├── data/
│   └── dialogue_cases.json
└── tests/
    └── test_rule_based_diagnosis.py
```

## 运行

在仓库根目录执行一次问诊：

```bash
python -X utf8 code-dsext/chapter2/rule_based_diagnosis.py \
  "循环泵入口压力80 kPa，振得厉害，流量忽高忽低"
```

进入交互模式。证据不足时，每个问题最多追问一次：

```bash
python -X utf8 code-dsext/chapter2/rule_based_diagnosis.py --interactive
```

输出便于其他程序处理的 JSON：

```bash
python -X utf8 code-dsext/chapter2/rule_based_diagnosis.py \
  "轴承温度 86 ℃，高频振动 8.0 mm/s" --format json
```

运行 20 条人工编写的离线用例：

```bash
python -X utf8 code-dsext/chapter2/rule_based_diagnosis.py --validate
```

单次问诊找到候选或命中安全规则时退出码为 `0`；需要追问或转人工时为 `2`；
规则或样例数据无法加载时为 `1`。

## 规则与流程

系统依次执行以下路径：

1. 归一化设备/测点别名、常见口语和单位，如“水泵/泵组”转换为“循环泵”、
   “进口压力”转换为“入口压力”、“振得厉害”转换为“振动升高”，`80 kPa`
   转换为 `0.08 MPa`；
2. 先匹配安全规则，再匹配强特征规则、弱特征规则，最后进入兜底规则；
3. 多条强规则命中时，按必要条件满足率、风险等级和 `rule_id` 确定性排序；
4. 多条规则仅部分命中时，记录 `PUMP-CONFLICT-001`，只追问最高排序候选
   所缺少的一项关键证据；
5. 报告分开列出观察事实、规则推断、下一步检查和限制。

`pump_fault_rules.json` 覆盖：

| 规则 | 候选或路径 | 主要必要条件 |
| --- | --- | --- |
| `SAFE-PUMP-001` | 重大安全风险 | 冒烟/起火、大量泄漏或人员伤害之一 |
| `PUMP-CAV-001` | 汽蚀 | 入口压力偏低 + 异响/振动升高 + 流量波动 |
| `PUMP-BRG-001` | 轴承异常 | 轴承温度升高 + 高频振动升高 |
| `PUMP-FLT-001` | 过滤器堵塞 | 过滤器压差增大 + 流量持续下降/偏低 |
| `PUMP-SEAL-001` | 密封异常 | 泄漏量或密封腔温度异常 |
| `PUMP-CONFLICT-001` | 证据冲突 | 至少两条强规则部分命中 |
| `PUMP-WEAK-001` | 弱特征追问 | 单个或组合弱特征 |
| `PUMP-FALLBACK-001` | 转人工 | 未命中已知规则 |

代码中的数值阈值仅服务于教学演示：入口压力 `≤ 0.12 MPa`、轴承温度
`≥ 80 °C`、高频振动 `≥ 5 mm/s`、过滤器压差 `≥ 50 kPa`、密封泄漏量
`≥ 50 mL/min`。这些值不是设备报警设定或正式检修依据。

## 数据与验证

- `pump_fault_rules.json` 与 `dialogue_cases.json` 均为 2026-07-26 人工编写的
  v1 脱敏教学数据，不来自真实企业、设备或公开数据集。
- 离线数据固定包含 20 条问句，覆盖正确候选、安全短路、冲突、追问、兜底、
  口语归一化、单位换算和否定症状。
- 验证命令统计正确命中、错误命中、兜底和追问数量及比例；相同输入与相同
  规则文件始终产生相同结果。

运行单元测试：

```bash
python -m unittest discover -s code-dsext/chapter2/tests -v
```

## 安全边界

该助手用于离线教学和班组预检，不替代联锁系统、设备工程师或正式检修规程。
它没有 PLC、MES、DCS 或设备写控制能力。命中的故障名称始终是待验证候选；
火情、重大泄漏和人员伤害会短路普通规则，只提示按现场应急规程升级。规则系统
难以完整覆盖模糊表述、组合故障和新型故障，证据不足时必须追问或转人工。
