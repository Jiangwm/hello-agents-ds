# 质量异常深度研究证据驾驶舱设计系统

## 1. 氛围与身份

这是面向质量工程师、工艺工程师与审批人的离线证据驾驶舱。界面应像一张经过校准的调查台：冷静、紧凑、可信，所有状态都能追溯到 TODO、证据、笔记或人工关口。签名视觉是左侧调查树与右侧证据分区形成的双轨阅读路径，用户始终能回答“正在查什么”和“凭什么得出结论”。设计读法为 operational dashboard，`DESIGN_VARIANCE: 3`、`MOTION_INTENSITY: 2`、`VISUAL_DENSITY: 7`。

### 参考提取记录

- `code/chapter14/helloagents-deepresearch/frontend`：提取研究输入、SSE 进度、任务列表与详情分栏的交互语法，不沿用装饰性极光或 emoji。
- `code-dsext/chapter13/frontend`：提取冷灰底、深青安全头部、白色工作面板、状态条与审批导出的工业教学风格。
- 领域规范：离线、只读、生产控制禁止；关键结论绑定审批笔记和证据；内部事实与外部通用知识必须分区。

## 2. 颜色

### 调色板

| 角色 | Token | 亮色 | 暗色 | 用途 |
|---|---|---|---|---|
| 画布 | `--surface-canvas` | `#eef3f2` | `#0d1718` | 页面背景 |
| 主表面 | `--surface-primary` | `#ffffff` | `#132123` | 面板、输入 |
| 次表面 | `--surface-secondary` | `#f5f8f7` | `#192a2c` | 列表行、摘要 |
| 提升表面 | `--surface-elevated` | `#ffffff` | `#203335` | 浮层、重点区 |
| 主文本 | `--text-primary` | `#183234` | `#eef8f7` | 标题与正文 |
| 次文本 | `--text-secondary` | `#536b69` | `#acc2bf` | 元数据、说明 |
| 弱文本 | `--text-tertiary` | `#748987` | `#7f9996` | 空态、禁用 |
| 默认边框 | `--border-default` | `#cbdad7` | `#365052` | 面板边界 |
| 弱边框 | `--border-subtle` | `#e2ebe9` | `#283f41` | 行分隔 |
| 唯一强调 | `--accent-primary` | `#087565` | `#39bba4` | 主操作、焦点 |
| 强调悬停 | `--accent-hover` | `#075f53` | `#58cdb7` | 悬停 |
| 成功 | `--status-success` | `#197048` | `#58c78d` | 完成、批准 |
| 警告 | `--status-warning` | `#9a6215` | `#e5ae55` | 待审批、预算 |
| 错误 | `--status-error` | `#a43f3f` | `#ef8484` | 冲突、拒绝 |
| 信息 | `--status-info` | `#2e6575` | `#68b6ca` | 工具、外部知识 |

### 规则

- 深青是唯一交互强调色；状态色只表达语义，不做装饰。
- 顶部安全栏使用 `--header-start` 到 `--header-end` 的深青渐变，其他区域不重复渐变。
- 原始色值只在全局 token 定义中出现；组件不得声明新颜色。
- 页面采用自动主题，同一时刻只呈现一套主题，不做段落反转。

## 3. 字体

### 字阶

| 层级 | Token | 尺寸 | 字重 | 行高 | 用途 |
|---|---|---|---|---|---|
| 页面标题 | `--font-page` | `clamp(1.5rem, 3vw, 2.1rem)` | 720 | 1.2 | 系统名称 |
| H2 | `--font-h2` | `1.125rem` | 700 | 1.35 | 面板标题 |
| H3 | `--font-h3` | `0.9375rem` | 700 | 1.4 | 分组标题 |
| 正文 | `--font-body` | `0.875rem` | 400 | 1.55 | 默认内容 |
| 小正文 | `--font-small` | `0.8125rem` | 450 | 1.45 | 元数据 |
| 标签 | `--font-label` | `0.75rem` | 680 | 1.35 | 状态、字段名 |
| 数字 | `--font-metric` | `1.375rem` | 720 | 1.15 | 摘要指标 |

### 字体栈

- 主字体：`"Segoe UI", "Microsoft YaHei UI", system-ui, sans-serif`
- 等宽：`"Cascadia Code", "SFMono-Regular", Consolas, monospace`
- 最多两套字体；正文不低于 13px。

## 4. 间距与布局

### 间距 Token

基础单位为 4px：`--space-1: 4px`、`--space-2: 8px`、`--space-3: 12px`、`--space-4: 16px`、`--space-5: 20px`、`--space-6: 24px`、`--space-8: 32px`、`--space-10: 40px`。

### 栅格与滚动所有权

- `AppShell` 为 `100dvb` 三行网格：安全头部、摘要条、可滚动工作区。
- 只有 `.workspace` 拥有页面级纵向滚动；宽屏 TODO 栏和证据详情各自拥有明确的面板滚动。
- 宽屏为 `minmax(17rem, 0.78fr) minmax(0, 1.8fr) minmax(18rem, 0.9fr)` 三列；中屏将审批栏移到底部；375px 单列文档流。
- 所有滚动网格子项必须 `min-block-size: 0`；内在网格使用 `minmax(min(16rem, 100%), 1fr)` 防止窄屏溢出。
- 内容最大宽度 1600px，页面内边距使用 `clamp(var(--space-3), 2vw, var(--space-6))`。

## 5. 组件

### AppShell / SafetyHeader

- **结构**：`header + summary + main`；安全栏包含系统名、离线只读声明、运行 ID。
- **状态**：未创建、已连接、离线错误、暂停、阻塞、完成。
- **可访问性**：landmark 完整；状态更新用 `aria-live="polite"`。
- **布局**：`scroll-body-shell`，工作区是唯一主滚动所有者。

### ResearchForm / SummaryStrip

- **结构**：有显式标签的研究范围表单；四个关键指标平铺。
- **状态**：默认、焦点、禁用、提交中、字段错误。
- **交互**：开始按钮仅在必填项有效且无运行时可用。

### TodoTree / StatusBanner

- **结构**：有序 TODO 列表，显示依赖、问题、工具、结果与置信度；状态横幅解释当前工具或阻塞原因。
- **状态**：pending、running、awaiting_human、blocked、completed、failed。
- **可访问性**：当前 TODO 使用 `aria-current="step"`；状态不只依赖颜色。

### BudgetMeter

- **结构**：工具调用、成本、扫描行数、耗时四个原生 `progress`。
- **状态**：正常、接近上限、耗尽、未知。
- **规则**：数值同时以文本表达，不能只显示进度轨道。

### EvidencePartition / ConflictPanel

- **结构**：内部工厂事实与外部通用知识分栏；每条证据含来源、版本、窗口、权限、哈希、查询、质量与状态。
- **状态**：found、negative_result、missing_data、conflict、empty。
- **规则**：外部知识不可单独支撑工厂事实；冲突面板明确列出支持、反证、缺失与下一步。

### GatePanel / NoteReview

- **结构**：五类人工 Gate 和笔记审查列表；每项包含输入哈希、版本、审批人及理由。
- **状态**：pending、awaiting_human、approved、rejected、失效。
- **交互**：审批理由必填；按钮由后端状态约束并保留禁用原因。

### ReportSections / ActionCluster

- **结构**：固定七节报告；操作簇包括步进、运行、暂停、改范围、恢复、审批、导出。
- **状态**：报告未生成、待最终审批、可导出、导出错误。
- **可访问性**：按钮采用原生语义；破坏状态的操作不使用仅图标按钮。

### Empty / Loading / Error

- **空态**：说明如何开始研究或为何暂无证据。
- **加载态**：使用与最终区域同形的骨架，不使用无限旋转器。
- **错误态**：就地给出可恢复建议，不自动重试改变状态。

## 6. 动效与交互

| 类型 | Token | 时长 | 缓动 | 用途 |
|---|---|---|---|---|
| 微反馈 | `--motion-fast` | 120ms | ease-out | 按压、焦点 |
| 状态更新 | `--motion-standard` | 220ms | ease-in-out | 行高亮、面板切换 |

- 仅动画 `transform` 与 `opacity`；按钮按下 `translateY(1px)`。
- 新 SSE 事件只产生一次短暂背景淡入，禁止循环动画。
- `prefers-reduced-motion: reduce` 时关闭所有非必要过渡。
- 暂停、恢复、审批、导出必须有明确加载与结果反馈，操作期间防止重复提交。

## 7. 深度与表面

采用混合策略：层级主要依靠冷灰色调差与细边框，只有安全头部和浮起的审批/冲突面板使用两级柔和阴影。圆角规则固定为面板 12px、输入和按钮 8px、状态标签 999px。面板不可层层套卡片；长列表依靠分隔线和背景选中态组织。

| Token | 值 | 用途 |
|---|---|---|
| `--shadow-subtle` | `0 1px 2px rgb(9 51 48 / 0.06)` | 基础面板 |
| `--shadow-raised` | `0 12px 32px rgb(9 51 48 / 0.12)` | Gate、冲突 |
| `--border-panel` | `1px solid var(--border-default)` | 面板边界 |

## 8. 可访问性约束与已接受债务

### 约束

- 目标 WCAG 2.2 AA：正文对比度至少 4.5:1，大字至少 3:1。
- 所有交互可由键盘完成，焦点环至少 2px；触控目标最小 40px。
- 颜色不是唯一状态线索；状态标签始终包含中文文本。
- 表单标签位于控件上方；错误信息与字段关联。
- 375、768、1280px 无主内容横向滚动；长哈希与 URL 使用 `overflow-wrap: anywhere`。
- 屏幕阅读器按安全声明、研究范围、摘要、TODO、证据、审批、报告顺序阅读。

### 包容性角色

- 值班质量工程师：高信息密度下需快速定位阻塞和下一步。
- 审批人：只需看到输入哈希、版本、证据与影响范围即可决策。
- 色觉差异或低视力用户：依赖文字状态、高对比焦点与系统缩放。
- 仅键盘用户：可按文档顺序完成暂停、恢复、审批与导出。

### 已接受债务

| 项目 | 位置 | 原因 | 退出条件 |
|---|---|---|---|
| 浏览器原生 `EventSource` 不允许自定义 `Last-Event-ID` 请求头 | `src/services/events.ts` | 首次实现通过查询参数持久化 cursor，并依赖浏览器自动重连 header | 后端明确要求自定义 header 时改用 fetch stream |
| 图标仅使用少量内联 CSS 几何标记 | 全局状态标签 | 不增加图标库以控制教学示例体积；所有含义都有文字 | 若扩展为图标密集导航则引入单一 SVG 图标库 |

