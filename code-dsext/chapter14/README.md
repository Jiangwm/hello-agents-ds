# 第十四章：质量异常自动化深度研究智能体

本目录实现一个面向教学的离线质量异常调查系统。系统只读取脱敏样例数据，不连接 MES、PLC、DCS 或外部搜索服务，不写入生产参数；关键数值由确定性工具计算，最终根因结论与证据包导出必须经过人工审批。

## 功能概览

- 7 项依赖化 TODO：异常范围、正常对照、工艺漂移、设备维护、物料变化、历史案例、候选根因验证。
- 四维预算：工具调用、成本单位、扫描行数、耗时；耗尽后失败关闭并请求追加预算。
- 只读工具：质量批次、工艺时序、设备和维护事件、物料批次、内部规范和历史 8D、外部通用机理。
- 证据治理：来源路径、版本、检索时间、数据窗口、权限、查询、哈希、负面结果和来源质量均可追溯。
- 人工关口：研究计划、跨域访问、追加预算、验证实验、最终结论；审批绑定计划、范围、证据和报告哈希。
- 可恢复工作流：SQLite 持久化、乐观并发、暂停、修改范围、审批失效、重开和继续。
- 固定七段报告：问题定义、影响范围、数据证据、候选根因、反证、结论、验证计划。
- 三个使用表面：JSON CLI、FastAPI/SSE、Vue 3 证据驾驶舱。

## 目录结构

```text
chapter14/
├── backend/
│   ├── agents/       # TODO 分发与研究编排
│   ├── models/       # 严格、冻结的领域模型与状态机
│   ├── services/     # 仓储、授权、预算、闸门、笔记、综合、报告、导出
│   ├── tools/        # 只读工作区与确定性领域工具
│   ├── tests/        # unittest 单元、集成、API 与 CLI E2E
│   └── main.py       # FastAPI 入口
├── frontend/         # Vue 3 + TypeScript + Vite
├── sample_workspace/ # 脱敏样例、资源清单及 SHA-256
└── run_quality_deep_research.py
```

## 样例数据

`sample_workspace/manifest.json` 是唯一资源白名单，记录时区、数据窗口、租户/产线、权限、只读策略和每个资源的 SHA-256。样例仅使用 `TENANT-DEMO`、`LINE-A` 等虚构标识。

确定性示例结果：

- 质量异常起点：`2026-07-15T08:00:00+08:00`。
- 基线：`5 / 3000 = 0.001666...`。
- 异常窗口：`81 / 6000 = 0.0135`，相对基线 `8.1` 倍。
- 工艺温度均值漂移约 `5.95 °C`。
- 设备维护、物料切换和文档资料同时包含支持证据与反证，工具输出始终标记 `correlation_only=true`。

外部通用知识位于 `external_knowledge/`，只能解释一般机理，不能替代内部批次证据。

## 环境

- Python 3.12+
- Node.js 20+
- 后端依赖：FastAPI、Pydantic v2、Uvicorn、HTTPX（仅用于本地 API 测试）
- 前端依赖：Vue 3、Vite、TypeScript、Zod、Ky、Vitest

从仓库根目录安装：

```bash
python -m venv .venv
.venv\Scripts\python -m pip install -r code-dsext\chapter14\backend\requirements.txt
npm --prefix code-dsext/chapter14/frontend install
```

常规运行不需要网络、LLM、GPU 或 API 密钥。

## CLI

状态目录必须位于样例工作区之外。下面的一键命令将显式模拟教学演示中的人工计划复核、跨域/验证检查点、笔记复核和最终结论审批，并导出本地证据包：

```bash
python -X utf8 code-dsext/chapter14/run_quality_deep_research.py demo \
  --workspace code-dsext/chapter14/sample_workspace \
  --state-dir C:/tmp/ch14-state \
  --approve-plan --plan-approver QA \
  --approve-conclusion --conclusion-approver QA \
  --output C:/tmp/ch14-evidence.json
```

真实人工流程可分进程执行：

```bash
python -X utf8 code-dsext/chapter14/run_quality_deep_research.py start --state-dir C:/tmp/ch14-state --run-id run-001
python -X utf8 code-dsext/chapter14/run_quality_deep_research.py status --state-dir C:/tmp/ch14-state --run-id run-001
python -X utf8 code-dsext/chapter14/run_quality_deep_research.py approve --state-dir C:/tmp/ch14-state --run-id run-001 --gate research_plan --approver quality-lead --reason "批准脱敏调查计划"
python -X utf8 code-dsext/chapter14/run_quality_deep_research.py run --state-dir C:/tmp/ch14-state --run-id run-001
```

其余命令包括 `pause`、`revise`、`resume`、`review-notes`、`report` 和 `export`。标准输出始终为单个 JSON；退出码：`0` 成功、`2` 等待人工、`3` 阻塞、`4` 输入错误、`1` 运行错误。

低预算演示会显式失败关闭：

```bash
python -X utf8 code-dsext/chapter14/run_quality_deep_research.py demo \
  --workspace code-dsext/chapter14/sample_workspace \
  --state-dir C:/tmp/ch14-budget-state --max-tool-calls 1
```

## API 与 SSE

```bash
$env:CH14_WORKSPACE = "E:\path\to\hello-agents-ds\code-dsext\chapter14\sample_workspace"
$env:CH14_STATE_DIR = "C:\tmp\ch14-api-state"
python -X utf8 -m uvicorn main:app --app-dir code-dsext/chapter14/backend --host 127.0.0.1 --port 8014
```

主要端点：

- `GET /health`
- `POST /runs`、`POST /research/stream`
- `GET /runs/{run_id}`、`GET /runs/{run_id}/todos`、`GET /runs/{run_id}/notes`、`GET /runs/{run_id}/report`
- `POST /runs/{run_id}/step`、`/run`、`/pause`、`/revise`、`/resume`
- `POST /runs/{run_id}/notes/review`、`POST /runs/{run_id}/gates/{gate_id}/approve`、`POST /runs/{run_id}/export`
- `GET /runs/{run_id}/events`，支持 `Last-Event-ID` 或 `after` 游标

除创建操作外，运行端点要求 `X-Tenant-ID`。SSE 帧包含递增 `id`、`event` 和 JSON `data`；重连只重放游标后的持久化事件。

## 前端

```bash
npm --prefix code-dsext/chapter14/frontend run dev
```

浏览 `http://127.0.0.1:5173`。页面展示 TODO 树、当前工具、四维预算、证据数量、阻塞原因、人工关口、笔记复核、冲突以及内部/外部来源分区。`?showcase=1` 显示设计原语和状态变体。

## 验证

```bash
python -X utf8 -m unittest discover -s code-dsext/chapter14/backend/tests -p "test_*.py" -v
python -X utf8 -m compileall -q code-dsext/chapter14
npm --prefix code-dsext/chapter14/frontend run typecheck
npm --prefix code-dsext/chapter14/frontend run test
npm --prefix code-dsext/chapter14/frontend run build
git diff --check -- code-dsext/chapter14
```

测试覆盖正常报告、无结果、缺失数据、证据冲突、预算耗尽、越权、非有限输入、暂停改范围恢复、审批失效、SSE 重放和审批后导出。测试不访问网络或付费模型。

## 安全边界

- `offline=true`、`read_only=true`、`production_control=prohibited` 是不可放宽的运行契约。
- 工具只能读取 manifest 白名单资源；路径穿越、符号链接、敏感后缀和哈希篡改均失败关闭。
- 租户、产线、域、计划版本、范围版本和输入哈希共同绑定授权。
- 无结果是有效负面证据；关键数据缺失或冲突必须阻塞，禁止以常识补全。
- 报告只能消费当前范围内已批准且哈希有效的笔记；外部资料不会被提升为内部事实。
- 本项目仅用于教学和决策支持，不能作为生产控制、自动处置或最终质量责任结论。
