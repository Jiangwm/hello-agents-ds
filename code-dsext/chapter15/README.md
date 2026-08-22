# 第十五章 DSExt：多智能体数字工厂分析沙箱

本目录实现了[第十五章工业场景设计](../../docs/chapter15/Chapter15-DSExt-DigitalFactorySandbox.md)中的离线教学系统。沙箱用离散事件状态机模拟设备、缓冲区、批次、质量门和能源状态，并让设备、调度、维护、质量、能源与观察六类角色围绕同一证据链协作。

系统只用于教学、离线实验和决策支持，不连接或写入 PLC、MES、DCS、APS、设备控制器或生产参数，也不替代专业离散事件仿真器。生产节拍、资源占用、质量状态、能源和 KPI 全部由确定性程序计算；模型文本只能作为解释或结构化建议的证据，不能直接修改世界状态。

## 核心能力

- 读取三个版本化、脱敏的本地 JSON 场景，并记录资源哈希、数据版本和工具调用 ID。
- 以固定仿真时钟推进状态；相同场景、随机种子、策略版本和命令流得到相同轨迹哈希。
- 提供固定规则、单智能体、多智能体三种策略，对比吞吐量、WIP、停机、缺陷、能耗、电费、工具调用与解释质量。
- 所有状态变化都由事件产生；事件包含来源、原因、仿真时间、状态差异、因果引用和哈希链。
- 支持单步推进、暂停、恢复、快照、隔离回放、事件流、事件因果链与指标查询。
- 智能体只提交结构化建议。确定性门禁返回 `allow`、`deny` 或 `hold`；硬拒绝不能由人工绕过，`hold` 必须经过显式人工复核。
- 最终证据包需人工审批后才能导出；审批绑定当前轨迹与证据哈希，后续推进会使审批失效。
- FastAPI、JSON CLI 和静态可视化页面复用同一个 `DigitalFactorySandbox` 深模块，不在适配层重复领域规则。

## 目录

```text
code-dsext/chapter15/
├── backend/
│   ├── factory_sandbox/
│   │   ├── models.py        # 严格 Pydantic 世界、事件与请求契约
│   │   ├── scenario_loader.py # 白名单场景读取与哈希证据
│   │   ├── engine.py        # 状态机、角色策略、门禁、回放与 KPI
│   │   ├── service.py       # 会话注册与三策略公平比较
│   │   └── api.py           # FastAPI 适配层
│   ├── tests/               # 行为与安全边界测试
│   └── requirements.txt
├── frontend/                # 无构建依赖的离线可视化页面
├── scenarios/               # 脱敏、版本化的声明式场景
├── run_factory_sandbox.py   # JSON CLI
└── README.md
```

## 预置场景

| 场景 ID | 工业问题 | 主要观察点 |
| --- | --- | --- |
| `upstream_slowdown` | 上游设备降速导致下游缓冲区饥饿 | 瓶颈传播、WIP、吞吐、停机 |
| `quality_isolation` | 质量异常触发批次隔离并形成交付压力 | 缺陷、隔离、返工、交期风险 |
| `peak_tariff_urgent_order` | 峰值电价与紧急订单争夺资源 | 能耗、电费、优先级与交付权衡 |

场景数据均为教学 fixture，不包含客户名称、真实设备地址、凭据或生产参数。

## 策略语义

- `fixed_rule`：只执行场景中的确定性规则，不生成智能体建议，作为可解释基线。
- `single_agent`：由一个协调角色基于局部摘要提出建议，工具调用和解释成本较低，但跨域信息有限。
- `multi_agent`：六类角色共享结构化事件证据，由与当前场景相关的角色提出建议并经确定性门禁处理；观察智能体只汇总 KPI 与因果链，不参与状态修改。

策略比较只说明当前脱敏 fixture 和教学假设下的结果，不代表真实工厂最优策略、节能收益或因果结论。

## 离线 CLI

从仓库根目录运行一个完整场景：

```powershell
python -X utf8 .\code-dsext\chapter15\run_factory_sandbox.py demo `
  --scenario upstream_slowdown `
  --strategy multi_agent `
  --seed 20260808 `
  --steps 12
```

默认完成分析后停在人工审批闸门，退出码为 `2`。只有显式提供审批人、理由和输出路径，才会写出本地 JSON 证据包：

```powershell
$OUT = Join-Path $env:TEMP "chapter15-factory-evidence.json"
python -X utf8 .\code-dsext\chapter15\run_factory_sandbox.py demo `
  --scenario quality_isolation `
  --strategy multi_agent `
  --seed 20260808 `
  --steps 12 `
  --approve `
  --approver "教学审核员-A" `
  --reason "已核对场景假设、事件链与指标口径" `
  --output $OUT
```

比较同一场景、同一种子下的三种策略：

```powershell
python -X utf8 .\code-dsext\chapter15\run_factory_sandbox.py compare `
  --scenario peak_tariff_urgent_order `
  --seed 20260808 `
  --steps 12
```

CLI 的 stdout 为 JSON，错误写入 stderr。

| 退出码 | 含义 |
| ---: | --- |
| `0` | 比较完成，或已审批并成功导出本地证据包。 |
| `1` | 运行时或数据处理错误。 |
| `2` | 分析完成，等待人工审批；不是运行失败。 |
| `4` | 命令参数或审批参数错误。 |

## 启动 FastAPI 与可视化页面

建议在独立虚拟环境安装后端依赖：

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r .\code-dsext\chapter15\backend\requirements.txt
python -m uvicorn backend.factory_sandbox.api:app `
  --app-dir .\code-dsext\chapter15 `
  --host 127.0.0.1 `
  --port 8015
```

访问 `http://127.0.0.1:8015/`。页面只展示后端返回的状态、指标、事件和比较结果，不在浏览器端重算生产数值。

## API

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| `GET` | `/health` | 返回离线、只读和禁止生产控制状态。 |
| `GET` | `/api/scenarios` | 查询白名单场景及其版本和内容哈希。 |
| `POST` | `/api/simulations` | 初始化场景、种子和策略。 |
| `GET` | `/api/simulations/{id}` | 查询当前世界状态、消息与审批状态。 |
| `POST` | `/api/simulations/{id}/step` | 推进一个或多个仿真步。 |
| `POST` | `/api/simulations/{id}/pause` | 暂停仿真。 |
| `POST` | `/api/simulations/{id}/resume` | 恢复仿真。 |
| `POST` | `/api/simulations/{id}/snapshot` | 创建不可变快照。 |
| `POST` | `/api/simulations/{id}/replay` | 隔离回放并校验轨迹，不覆盖当前状态。 |
| `GET` | `/api/simulations/{id}/events` | 查询事件流。 |
| `GET` | `/api/simulations/{id}/events/{event_id}/chain` | 追踪事件因果链。 |
| `GET` | `/api/simulations/{id}/metrics` | 查询确定性 KPI 与成本。 |
| `POST` | `/api/simulations/{id}/suggestions` | 提交结构化建议并执行规则门禁。 |
| `POST` | `/api/simulations/{id}/decisions/{decision_id}/review` | 审批或驳回待复核建议。 |
| `POST` | `/api/simulations/{id}/approve-export` | 审批当前最终证据包。 |
| `GET` | `/api/simulations/{id}/export` | 返回仍然有效的已审批证据包。 |
| `POST` | `/api/comparisons` | 用共同初态、种子和时域比较三种策略。 |

建议示例：

```json
{
  "role": "energy",
  "action_type": "shift_energy_load",
  "parameters": {"equipment_id": "EQ-HEAT"},
  "narrative": "建议在峰价窗口内临时降速。",
  "reason_codes": ["peak_tariff_signal"]
}
```

自由文本 `narrative` 会被明确标记为已忽略，不进入状态、指标或轨迹哈希。门禁只解释已注册的结构化动作；在文本中伪造 `world_state`、PLC 指令或指标不会形成状态变更。

## 确定性与审计语义

- `simulation_time` 是从零开始的离散分钟计数，不使用墙钟推进仿真。
- 场景、实体和事件采用稳定顺序；教学扰动从场景 ID、随机种子和离散分钟派生。
- 轨迹哈希不包含进程时间、随机 UUID 或说明文本。
- 事件哈希链记录前一事件哈希、规范化状态差异、状态哈希和决策证据引用。
- 快照绑定事件链头；回放在新状态中重建并逐步校验，不修改源仿真。
- 审批绑定当前事件链、状态和证据哈希；推进或证据变化会使旧审批失效。

## 验证

```powershell
python -X utf8 -m unittest discover `
  -s .\code-dsext\chapter15\backend\tests `
  -p "test_*.py" `
  -v
python -m compileall -q .\code-dsext\chapter15
```

测试覆盖可复现轨迹、非法建议失败关闭、文本与状态隔离、暂停/恢复、快照与回放、事件链、三策略公平比较、审批失效、受控导出、API 和 CLI。

## 安全边界与限制

- 只处理仓库内脱敏 fixture；场景加载器拒绝路径穿越、未知文件和 schema 外字段。
- 没有网络、LLM、数据库、消息队列、现场总线或生产控制依赖。
- API 会话只保存在当前进程内存中，重启后不会恢复；证据包只能显式写到用户指定的本地文件。
- 沙箱的产能、缺陷、能耗和电价模型是简化教学模型，不覆盖真实换线、设备启停曲线、人员班次、计量误差或复杂约束。
- 智能体建议必须由专业人员结合真实数据和业务制度复核；不得把相关性、策略比较或解释文本作为生产根因结论。
- 若未来接入大模型，只能作为解释或结构化提案 adapter；状态机、规则门禁、KPI、回放校验和人工审批不可被替换。
