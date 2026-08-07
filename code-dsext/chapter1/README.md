# 第一章：设备告警分诊智能体

本示例对应
[`Chapter1-DSExt-EquipmentAlarmTriage.md`](../../docs/chapter1/Chapter1-DSExt-EquipmentAlarmTriage.md)，
实现一个离线、只读、可追溯的最小工业智能体。它不依赖外部 API，仅使用 Python
标准库，通过可注入 Planner 和显式 `Thought—Action—Observation` 循环，直接展示
“感知—决策—行动—观察—回答”闭环。

## 目录

```text
chapter1/
├── first_industrial_agent.py
├── data/
│   ├── sensor_sample.csv
│   └── alarm_rules.csv
└── tests/
    └── test_first_industrial_agent.py
```

## 运行

在仓库根目录执行：

```bash
python -X utf8 code-dsext/chapter1/first_industrial_agent.py \
  "请分析 PRESS-A 在 2025-04-18 09:20 至 2025-04-18 09:40 的 ALM-VIB-002 告警" \
  --show-trace
```

若问题未给出告警码，智能体会先读取数据，再根据时间窗内唯一的告警码查询规则：

```bash
python -X utf8 code-dsext/chapter1/first_industrial_agent.py \
  "请分析冲压机 A 在 2025-04-18 09:20 至 2025-04-18 09:40 的告警" \
  --show-trace
```

若设备或明确起止时间缺失，程序不会猜测，也不会调用工具，而是返回需要补充的字段。
样例时间戳按车间本地时间（Asia/Shanghai）解释，查询使用包含起止时刻的闭区间。

## 数据来源

- `sensor_sample.csv` 和 `alarm_rules.csv` 均为本章于 2026-07-26 人工构造的 v1
  脱敏教学样例，不来自真实企业、设备或公开数据集，因此无外部数据许可要求。
- `TEACHING-DEMO-RULESET-v1#...` 是教学演示标识，不代表真实 EHS、维修规程或
  生产规则编号；阈值与原因仅用于演示工具调用和证据引用，不可用于现场处置。

## 设计说明

- `query_sensor_window(...)` 只读传感器 CSV，按设备与闭区间筛选，计算最小值、最大值和均值，并保留原始告警标记点。
- `lookup_alarm_rule(...)` 只读规则 CSV，返回阈值、规则来源、可能原因、人工检查项和仍缺少的证据。
- `available_tools` 是显式工具注册表，只有以上两个工具；默认最多调用 3 次。
- `OfflinePlanner` 根据当前证据逐轮选择下一动作：已知告警码时先查规则，未知告警码时先取数据；也可注入其他 Planner，但动作仍受白名单和次数上限约束。
- `Thought` 只记录简短决策依据，`Action` 与 `Observation` 形成可审计轨迹；所有阈值命中点和最终报告均由确定性代码生成。
- 数据报告保留 CSV 路径、设备、时间窗、样本数和越限时间戳，规则报告保留规则编号、来源和完整阈值条件。
- 报告把传感器事实、规则假设、不确定性和风险声明分开，不提供任何 PLC、MES、DCS 或设备写控制能力。

## 测试

```bash
python -m unittest discover -s code-dsext/chapter1/tests -v
```
