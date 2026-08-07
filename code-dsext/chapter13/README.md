# 第十三章 DSExt：工厂能源分析与排产建议助手

本目录实现了[第十三章工业场景设计](../../docs/chapter13/Chapter13-DSExt-EnergyOptimizationAssistant.md)中的离线教学系统。系统读取脱敏能耗基线、生产任务、工序约束和峰平谷电价，生成并复核多种候选排产，提供 FastAPI 接口和 Vue 页面，并保留编辑、审批与导出审计。

系统只提供决策支持，不连接或写入 MES、APS、PLC、DCS、设备控制器或生产参数。成本、峰值、单位能耗、置信区间和约束检查均由确定性程序计算；解释层不得改写这些数值。

## 功能

- Pydantic 模型约束计划请求、用户附加约束、设备候选、交付时间、电价、能耗基线、候选方案和证据。
- 只读 `ReadOnlyEnergyMCPClient` 从 `sample_data/` 白名单资源取数，并记录工具调用 ID、数据版本和 SHA-256。
- 数据、预测、优化、审核和报告五类 Agent 分工编排；实现不需要网络、LLM、GPU 或付费 API。
- 以 30 分钟为粒度复算全天功率曲线、峰平谷电费、峰值负荷、单位产品能耗和功率基线置信区间。
- 生成当前基线、成本优先、削峰优先和均衡方案；所有候选均经过独立约束检查与指标复算。
- 产量目标按标称批量线性缩放任务功率及置信区间；白班、夜班会实际限制候选时间窗。
- 页面可提交峰值上限和附加工序顺序，后端与只读 fixture 约束合并、哈希留痕并失败关闭校验。
- 用户可移动可调任务或锁定任务；只更新被编辑的候选和任务，其他候选保持不变，并以哈希记录变更。
- 方案编辑会使已有审批失效；只有显式人工审批后才能导出本地 JSON 证据包。
- 前端展示阶段进度、方案指标、全天曲线、任务时间轴、数据版本、估算误差、基础与用户合并后的全部生效约束、证据和审批状态；切换方案时通过曲线查询接口刷新 30 分钟能耗曲线。

## 目录

```text
code-dsext/chapter13/
├── backend/
│   ├── app/
│   │   ├── agents.py          # 五类 Agent 职责
│   │   ├── engine.py          # 确定性成本、曲线和约束引擎
│   │   ├── main.py            # FastAPI 应用
│   │   ├── mcp.py             # 离线只读 MCP 风格数据客户端
│   │   ├── models.py          # Pydantic 请求、响应和审计模型
│   │   └── service.py         # 计划、编辑、审批和导出编排
│   ├── tests/
│   │   └── test_energy_system.py
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── App.vue
│   │   ├── api.ts
│   │   ├── main.ts
│   │   └── types.ts
│   └── package.json
├── sample_data/               # 脱敏教学数据与版本元数据
├── run_energy_assistant.py    # JSON CLI
└── README.md
```

## 样例数据与确定性结果

`sample_data/` 仅包含 `LINE-A` 在 `2026-08-01` 的脱敏教学任务。元数据声明 30 分钟采样、`Asia/Shanghai` 时区、历史数据窗口和计量点完整性；缺点或异常会显示在 `data_summary.issues` 中，完全无数据时 fail-closed。

当前 fixture 的基线同时执行混合和加热任务，峰值为 150 kW、电费估算为 456 元，并违反“混合先于加热”的顺序约束。成本候选将任务移动到满足顺序的低价时段，电费估算为 286 元、峰值为 90 kW。数值只用于展示计算过程，不代表真实工厂节能收益。

### 班次、产量与约束语义

- 白班为 `06:00-18:00`；夜班在单个计划日中表示 `00:00-06:00` 与 `18:00-24:00`。任务必须完整落在一个已选班次片段内，不允许跨班次边界。
- 固定或锁定任务若不在已选班次内，请求直接失败；可移动任务只在已选班次、交付时间、资源与前后序约束共同允许的位置中搜索。
- 样例基线以 1000 件标称批量建模。小于等于标称批量的目标按比例缩放功率、能耗、成本、峰值和置信区间，并在证据假设中明确记录；超过可用批量时拒绝生成方案。
- `PlanningRequest.constraints` 支持固定时间、窗口、前后序、持续时间、设备资源和峰值上限。自定义窗口值使用 `[start_minute, end_minute]`，两端按 30 分钟对齐且必须容纳完整任务；前端提供峰值上限与 `前置任务>后续任务` 顺序输入，其他类型可通过 API 提交。

## 离线 CLI

从仓库根目录运行。默认命令完成取数、校验、预测、优化、审核和一次安全编辑后，停在人工审批闸门，退出码为 `2`：

```powershell
python -X utf8 .\code-dsext\chapter13\run_energy_assistant.py demo
```

只有显式提供审批人和理由后，才会记录审批并导出本地证据包：

```powershell
$OUT = Join-Path $env:TEMP "chapter13-energy-evidence.json"
python -X utf8 .\code-dsext\chapter13\run_energy_assistant.py demo `
  --approve `
  --approver "生产计划负责人" `
  --reason "已核对工序约束、数据范围与估算误差" `
  --output $OUT
```

CLI 的 stdout 为 JSON，错误写入 stderr。

| 退出码 | 含义 |
| ---: | --- |
| `0` | 离线分析已完成，审批已显式记录，可导出本地证据包。 |
| `1` | 运行时或数据处理错误。 |
| `2` | 分析完成，等待人工审批；不是失败，也不允许导出。 |
| `4` | 命令参数或审批参数错误。 |

## 启动 API 与前端

建议在独立虚拟环境安装后端依赖：

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r .\code-dsext\chapter13\backend\requirements.txt
.\.venv\Scripts\python -m uvicorn app.main:app `
  --app-dir .\code-dsext\chapter13\backend `
  --host 127.0.0.1 `
  --port 8000
```

另开终端启动前端：

```powershell
Set-Location .\code-dsext\chapter13\frontend
npm install
npm run dev
```

访问 `http://localhost:5173`。前端默认连接 `http://localhost:8000`；也可用 `VITE_API_BASE_URL` 指向其他本地教学服务。

## API

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| `GET` | `/health` | 返回只读和禁止生产控制状态。 |
| `POST` | `/api/plans` | 校验请求并生成基线与候选方案。 |
| `GET` | `/api/plans/{plan_id}` | 读取当前内存会话。 |
| `GET` | `/api/plans/{plan_id}/curve?option_id=...` | 查询指定方案或当前推荐方案的 30 分钟能耗曲线。 |
| `PATCH` | `/api/plans/{plan_id}` | 批量移动或锁定所选候选中的任务并复算。 |
| `POST` | `/api/plans/{plan_id}/approve` | 记录审批人、理由、时间和输入哈希。 |
| `GET` | `/api/plans/{plan_id}/export` | 仅在审批后返回 JSON 证据包。 |

编辑请求示例：

```json
{
  "option_id": "option-balanced",
  "actor": "计划员-A",
  "reason": "避开高价时段并锁定确认后的任务",
  "changes": [
    {"task_id": "T-MIX", "start_minute": 330},
    {"task_id": "T-PACK", "locked": true}
  ]
}
```

`start_minute` 以当天 `00:00` 为零点，必须按 30 分钟对齐。固定或已锁定任务不能移动或解锁，移动后仍须落在已选班次、交付窗口和资源约束内。编辑后的候选会再次全量执行安全约束审核，同时 `recomputed_task_ids` 只列出本次受影响任务。

## 验证

后端测试覆盖模型边界、产量缩放、班次窗口、未知约束引用失败关闭、固定与资源约束、峰谷计价、完整曲线、并发峰值、曲线查询、数据质量降级、只读 MCP、局部编辑、审批失效、哈希审计、API/CORS、导出和 CLI 退出码：

```powershell
python -X utf8 -m unittest discover `
  -s .\code-dsext\chapter13\backend\tests `
  -p "test_*.py" `
  -v
python -m compileall -q .\code-dsext\chapter13
```

前端验证：

```powershell
Set-Location .\code-dsext\chapter13\frontend
npm install
npm run build
npm audit
```

## 安全边界与限制

- 只处理脱敏教学 fixture；不要放入真实客户数据、凭据或生产地址。
- 没有排产下发、设备控制或生产写路由；API 会话仅保存在当前进程内存中。
- 当前优化器是可复现的教学启发式算法，不声称得到全局最优解。
- 当前班次窗口与产量线性缩放是脱敏教学假设；真实落地必须替换为企业班次日历、设备启停曲线和批次能耗模型。
- 基线功率是历史统计估计，必须同时查看数据窗口、置信区间和 `estimation_error_percent`。
- 基线违反约束时会明确显示违规，不会伪装为可行方案；候选无可行解时拒绝给出推荐。
- 大模型如在后续教学中接入，只能负责补充问题和组织说明，不能替代确定性计算、审核或人工批准。
