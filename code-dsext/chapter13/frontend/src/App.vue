<template>
  <main class="page-shell">
    <header class="hero">
      <div>
        <p class="eyebrow">HelloAgents · Chapter 13</p>
        <h1>工厂能源优化排产建议助手</h1>
        <p>面向生产计划的可审计能耗分析与方案比较。</p>
      </div>
      <div class="safety-badge">只读决策支持<br />不下发排产或设备控制</div>
    </header>

    <section class="panel" aria-labelledby="request-title">
      <div class="section-heading">
        <div>
          <p class="eyebrow">01 · 输入约束</p>
          <h2 id="request-title">生成能源排产建议</h2>
        </div>
        <span v-if="loading" class="loading">正在计算…</span>
      </div>
      <form class="request-form" @submit.prevent="submitPlan">
        <label>产线
          <input v-model.trim="form.line_id" required placeholder="LINE-A" />
        </label>
        <label>计划日期
          <input v-model="form.planning_date" type="date" required />
        </label>
        <label>产量目标（件）
          <input v-model.number="form.output_target_units" type="number" min="1" required />
        </label>
        <label>优化目标
          <select v-model="form.optimization_objective">
            <option value="balanced">平衡成本与峰值</option>
            <option value="cost">最低电费</option>
            <option value="peak">削峰优先</option>
          </select>
        </label>
        <label>电价档案
          <input v-model.trim="form.tariff_profile_id" required placeholder="CN-DEMO-SUMMER" />
        </label>
        <label>不可调整任务（逗号分隔）
          <input v-model.trim="lockedTaskText" placeholder="T-HEAT" />
        </label>
        <label>峰值上限（kW，可选）
          <input v-model.trim="peakLimitText" type="number" min="1" step="1" placeholder="例如：180" />
        </label>
        <label>附加工序顺序（可选）
          <input v-model.trim="precedenceText" placeholder="例如：T-MIX>T-HEAT" />
        </label>
        <fieldset class="shift-fieldset">
          <legend>班次</legend>
          <label v-for="shift in availableShifts" :key="shift" class="check-label">
            <input v-model="form.shifts" type="checkbox" :value="shift" />
            {{ shift === 'day' ? '白班' : '夜班' }}
          </label>
        </fieldset>
        <button class="primary-button" type="submit" :disabled="loading || form.shifts.length === 0">
          {{ loading ? '生成中…' : '生成建议方案' }}
        </button>
      </form>
      <p v-if="errorMessage" class="error-message" role="alert">{{ errorMessage }}</p>
    </section>

    <template v-if="plan">
      <section class="progress-strip" aria-label="方案生成进度">
        <div v-for="stage in stages" :key="stage.key" class="progress-step" :class="progressClass(stage.key)">
          <span class="step-dot"></span>
          <div><strong>{{ stage.label }}</strong><small>{{ progressText(stage.key) }}</small></div>
        </div>
      </section>

      <section class="notice">
        <strong>安全边界：</strong>本页面仅展示建议与复算结果；production_control={{ plan.production_control ?? 'prohibited' }}，不会写入 MES、PLC 或设备控制系统。
      </section>

      <section class="result-grid">
        <article class="panel option-panel">
          <div class="section-heading compact">
            <div>
              <p class="eyebrow">02 · 方案比较</p>
              <h2>基线与候选方案</h2>
            </div>
            <span class="status-pill" :class="approvalClass">{{ approvalStatus }}</span>
          </div>
          <div class="option-tabs">
            <button v-if="plan.baseline" :class="{ selected: selectedOptionId === baselineId }" @click="selectedOptionId = baselineId">
              基线
            </button>
            <button v-for="option in plan.candidates" :key="option.option_id" :class="{ selected: selectedOptionId === option.option_id }" @click="selectedOptionId = option.option_id">
              {{ option.name }}<span v-if="option.option_id === plan.recommended_option_id">推荐</span>
            </button>
          </div>
          <div v-if="selectedOption" class="metric-cards">
            <div><small>预计成本</small><strong>¥ {{ number(metric(selectedOption, 'cost')) }}</strong></div>
            <div><small>峰值负荷</small><strong>{{ number(metric(selectedOption, 'peak')) }} kW</strong></div>
            <div><small>单位能耗</small><strong>{{ number(metric(selectedOption, 'unit')) }} kWh/件</strong></div>
          </div>
          <p v-if="selectedOption" class="option-summary">
            {{ selectedOption.strategy }} · {{ selectedOption.feasible ? '约束可行' : '存在待处理约束' }} · {{ selectedOption.evidence?.reviewed ? '已审核' : '待审核' }}
          </p>
        </article>

        <article class="panel curve-panel">
          <div class="section-heading compact"><div><p class="eyebrow">30 分钟粒度</p><h2>能耗曲线</h2></div></div>
          <svg class="energy-chart" viewBox="0 0 720 250" role="img" aria-label="基线与当前候选方案能耗曲线">
            <line v-for="grid in [40, 90, 140, 190]" :key="grid" x1="36" :y1="grid" x2="700" :y2="grid" class="grid-line" />
            <polyline v-if="baselinePoints.length" :points="curvePoints(baselinePoints, chartMaximum)" class="baseline-line" />
            <polyline v-if="selectedCurvePoints.length" :points="curvePoints(selectedCurvePoints, chartMaximum)" class="selected-line" />
            <text x="36" y="232">00:00</text><text x="345" y="232">12:00</text><text x="650" y="232">24:00</text>
          </svg>
          <p class="chart-legend"><span class="baseline-key"></span>基线 <span class="selected-key"></span>{{ selectedOption?.name ?? '当前方案' }}</p>
          <p v-if="!baselinePoints.length && !selectedCurvePoints.length" class="empty-state">后端返回能耗曲线后将在此展示 30 分钟粒度对比。</p>
        </article>
      </section>

      <section class="panel" aria-labelledby="tasks-title">
        <div class="section-heading">
          <div><p class="eyebrow">03 · 局部调整</p><h2 id="tasks-title">任务时间轴与复算</h2></div>
          <span v-if="selectedOption" class="muted">当前：{{ selectedOption.name ?? selectedOption.option_id }}</span>
        </div>
        <div v-if="editableTasks.length" class="task-table-wrap">
          <table>
            <thead><tr><th>任务</th><th>产品</th><th>设备</th><th>交付</th><th>开始时间</th><th>时长</th><th>锁定</th><th>状态</th></tr></thead>
            <tbody>
              <tr v-for="task in editableTasks" :key="task.task_id">
                <td><strong>{{ task.product }}</strong><small>{{ task.task_id }}</small></td>
                <td>{{ task.product }}</td>
                <td>{{ task.device_id }}</td>
                <td>{{ minuteText(task.due_minute) }}</td>
                <td><input v-model.number="task.start_hour" type="number" min="0" max="23.5" step="0.5" :disabled="task.fixed || task.initiallyLocked" /></td>
                <td>{{ task.duration_hours }} 小时</td>
                <td><input v-model="task.locked" type="checkbox" :disabled="task.fixed || task.initiallyLocked" /></td>
                <td><span v-if="task.fixed" class="locked">固定任务</span><span v-else-if="task.initiallyLocked" class="locked">已锁定</span><span v-else>可调整</span></td>
              </tr>
            </tbody>
          </table>
        </div>
        <p v-else class="empty-state">当前方案未返回可展示的任务。</p>
        <form v-if="canEdit" class="action-form" @submit.prevent="submitChanges">
          <label>操作人<input v-model.trim="changeActor" required placeholder="例如：王工" /></label>
          <label class="wide">调整原因<input v-model.trim="changeReason" required placeholder="说明此次排产调整的业务依据" /></label>
          <button class="secondary-button" type="submit" :disabled="savingChanges">{{ savingChanges ? '复算中…' : '提交调整并局部复算' }}</button>
        </form>
        <p v-if="lastRecomputedTaskIds.length" class="success-message">已局部复算：{{ lastRecomputedTaskIds.join('、') }}</p>
        <div v-if="plan.change_history?.length" class="history">
          <h3>变更记录</h3>
          <ol><li v-for="(entry, index) in plan.change_history" :key="`${entry.changed_at ?? index}-${entry.actor ?? ''}`">{{ historyText(entry) }}</li></ol>
        </div>
      </section>

      <section class="result-grid">
        <article class="panel trace-panel">
          <div class="section-heading compact"><div><p class="eyebrow">04 · 可追溯性</p><h2>数据、模型与风险</h2></div></div>
          <dl class="trace-list">
            <dt>数据范围</dt><dd>{{ plan.data_summary?.source_window ?? '未返回' }}</dd>
            <dt>数据版本</dt><dd>{{ plan.data_summary?.data_version ?? '未返回' }}</dd>
            <dt>模型版本</dt><dd>{{ plan.data_summary?.model_version ?? '未返回' }}</dd>
            <dt>数据状态</dt><dd>{{ plan.data_summary?.status ?? '未返回' }}</dd>
            <dt>估算误差</dt><dd>{{ plan.estimation_error_percent === undefined ? '未返回' : `${plan.estimation_error_percent}%` }}</dd>
          </dl>
          <h3>假设与约束</h3>
          <ul><li v-for="item in plan.assumptions ?? []" :key="item">{{ item }}</li><li v-for="issue in plan.data_summary?.issues ?? []" :key="issue">风险：{{ issue }}</li></ul>
          <h3 v-if="plan.effective_constraints.length">全部生效约束</h3>
          <ul v-if="plan.effective_constraints.length"><li v-for="constraint in plan.effective_constraints" :key="constraint.constraint_id">{{ constraint.constraint_id }}：{{ constraint.kind }} / {{ constraint.task_id }} / {{ constraint.value }}</li></ul>
          <h3>审核证据</h3>
          <ul><li v-for="item in evidenceTexts" :key="item">{{ item }}</li><li v-if="!evidenceTexts.length">后端尚未返回证据。</li></ul>
          <h3 v-if="selectedOption?.violations?.length">约束检查</h3>
          <ul v-if="selectedOption?.violations?.length"><li v-for="violation in selectedOption.violations" :key="violation.message">{{ violation.severity ?? '提示' }}：{{ violation.message }}</li></ul>
        </article>

        <article class="panel approval-panel">
          <div class="section-heading compact"><div><p class="eyebrow">05 · 人工审批</p><h2>审批与导出</h2></div></div>
          <p>方案状态：<strong>{{ approvalStatus }}</strong></p>
          <form v-if="isPending" class="approval-form" @submit.prevent="submitApproval">
            <label>审批人<input v-model.trim="approver" required placeholder="例如：生产经理" /></label>
            <label>审批意见<textarea v-model.trim="approvalReason" required rows="3" placeholder="确认约束、风险与建议范围" /></label>
            <button class="primary-button" type="submit" :disabled="approving">{{ approving ? '审批中…' : '批准该方案' }}</button>
          </form>
          <p v-else-if="approvalStatus !== 'approved'" class="muted">仅待审批方案可在此提交审批。</p>
          <button v-if="approvalStatus === 'approved'" class="secondary-button export-button" type="button" :disabled="exporting" @click="exportPlan">
            {{ exporting ? '准备导出…' : '下载已审批方案 JSON' }}
          </button>
          <p v-if="actionMessage" class="success-message">{{ actionMessage }}</p>
        </article>
      </section>
    </template>
  </main>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ApiError, approvePlan, createPlan, downloadPlanExport, getPlanCurve, patchPlan } from './api'
import type {
  ChangeRecord,
  EnergyCurve,
  EnergyPlan,
  EnergyPoint,
  OperationConstraint,
  PlanChangeRequest,
  PlanningRequest,
  ProductionTask,
  ScheduleOption,
  TaskScheduleEdit
} from './types'

interface EditableTask {
  task_id: string
  product: string
  start_hour: number
  original_start_hour: number
  duration_hours: number
  device_id: string
  due_minute: number
  locked: boolean
  originally_locked: boolean
  initiallyLocked: boolean
  fixed: boolean
}

const availableShifts = ['day', 'night'] as const
const stages = [
  { key: 'data', label: '取数' },
  { key: 'validation', label: '校验' },
  { key: 'forecast', label: '预测' },
  { key: 'optimization', label: '优化' },
  { key: 'review', label: '审核' }
] as const
const form = ref<PlanningRequest>({
  line_id: 'LINE-A', planning_date: '2026-08-01', output_target_units: 1000,
  shifts: ['day', 'night'], optimization_objective: 'balanced', tariff_profile_id: 'CN-DEMO-SUMMER', locked_task_ids: ['T-HEAT'], constraints: []
})
const lockedTaskText = ref('T-HEAT')
const peakLimitText = ref('')
const precedenceText = ref('')
const plan = ref<EnergyPlan | null>(null)
const selectedOptionId = ref('')
const loadedCurve = ref<EnergyCurve | null>(null)
const editableTasks = ref<EditableTask[]>([])
const loading = ref(false)
const savingChanges = ref(false)
const approving = ref(false)
const exporting = ref(false)
const errorMessage = ref('')
const actionMessage = ref('')
const changeActor = ref('')
const changeReason = ref('')
const approver = ref('')
const approvalReason = ref('')
const lastRecomputedTaskIds = ref<string[]>([])

const baselineId = computed(() => plan.value?.baseline?.option_id ?? '__baseline__')
const selectedOption = computed<ScheduleOption | undefined>(() => {
  if (!plan.value) return undefined
  if (selectedOptionId.value === baselineId.value) return plan.value.baseline
  return plan.value.candidates.find((option) => option.option_id === selectedOptionId.value)
})
const approvalStatus = computed(() => plan.value?.approval_status ?? 'pending_approval')
const approvalClass = computed(() => approvalStatus.value === 'approved' ? 'approved' : 'pending')
const isPending = computed(() => approvalStatus.value === 'pending_approval')
const canEdit = computed(() => selectedOption.value !== undefined && selectedOptionId.value !== baselineId.value)
const baselinePoints = computed(() => curveData(optionCurve(plan.value?.baseline)))
const selectedCurvePoints = computed(() => curveData(loadedCurve.value ?? optionCurve(selectedOption.value)))
const chartMaximum = computed(() => Math.max(
  ...baselinePoints.value.map(pointValue),
  ...selectedCurvePoints.value.map(pointValue),
  1
))
const evidenceTexts = computed(() => (plan.value?.evidence ?? []).map((item) => [
  `数据窗口：${item.source_window}`,
  `数据版本：${item.data_version}`,
  `模型版本：${item.model_version}`,
  `审核：${item.reviewed ? '已完成' : '待完成'}`,
  `工具调用：${item.tool_call_ids.join('、') || '未返回'}`,
  `误差说明：${item.estimation_error}`
].join('；')))

watch(selectedOption, (option) => { editableTasks.value = toEditableTasks(option?.tasks ?? []) }, { immediate: true })
watch(
  [() => plan.value?.plan_id, selectedOptionId],
  () => { void refreshSelectedCurve() },
  { immediate: true }
)

async function submitPlan(): Promise<void> {
  errorMessage.value = ''
  actionMessage.value = ''
  form.value.locked_task_ids = lockedTaskText.value.split(',').map((id) => id.trim()).filter(Boolean)
  if (!form.value.shifts.length) { errorMessage.value = '请至少选择一个班次。'; return }
  let constraints: OperationConstraint[]
  try { constraints = buildRequestConstraints() }
  catch (error: unknown) { errorMessage.value = errorText(error, '约束格式错误'); return }
  loading.value = true
  try {
    const result = await createPlan({ ...form.value, shifts: [...form.value.shifts], locked_task_ids: [...form.value.locked_task_ids], constraints })
    plan.value = result
    selectedOptionId.value = result.recommended_option_id || result.candidates[0]?.option_id || baselineId.value
    lastRecomputedTaskIds.value = []
  } catch (error: unknown) {
    errorMessage.value = errorText(error, '生成方案失败')
  } finally { loading.value = false }
}

async function submitChanges(): Promise<void> {
  if (!plan.value || !selectedOption.value || !changeActor.value || !changeReason.value) return
  const changes = editableTasks.value.filter((task) => !task.fixed && !task.initiallyLocked && (task.start_hour !== task.original_start_hour || task.locked !== task.originally_locked)).map<TaskScheduleEdit>((task) => ({
    task_id: task.task_id,
    ...(task.start_hour !== task.original_start_hour ? { start_minute: Math.round(task.start_hour * 2) * 30 } : {}),
    ...(task.locked !== task.originally_locked ? { locked: task.locked } : {})
  }))
  if (!changes.length) { errorMessage.value = '未检测到可提交的任务调整。'; return }
  savingChanges.value = true; errorMessage.value = ''; actionMessage.value = ''
  const payload: PlanChangeRequest = { option_id: selectedOption.value.option_id, actor: changeActor.value, reason: changeReason.value, changes }
  try {
    const result = await patchPlan(plan.value.plan_id, payload)
    plan.value = result
    await refreshSelectedCurve()
    lastRecomputedTaskIds.value = result.recomputed_task_ids
    actionMessage.value = '调整已提交，后端已返回最新复算结果。'
    changeReason.value = ''
  } catch (error: unknown) { errorMessage.value = errorText(error, '提交调整失败')
  } finally { savingChanges.value = false }
}

async function submitApproval(): Promise<void> {
  if (!plan.value || !approver.value || !approvalReason.value) return
  approving.value = true; errorMessage.value = ''; actionMessage.value = ''
  try {
    plan.value = await approvePlan(plan.value.plan_id, { approver: approver.value, reason: approvalReason.value })
    actionMessage.value = '审批已记录；可下载后端生成的 JSON 方案包。'
  } catch (error: unknown) { errorMessage.value = errorText(error, '审批失败')
  } finally { approving.value = false }
}

async function exportPlan(): Promise<void> {
  if (!plan.value) return
  exporting.value = true; errorMessage.value = ''
  try { await downloadPlanExport(plan.value.plan_id); actionMessage.value = '已开始下载后端导出的 JSON 方案包。' }
  catch (error: unknown) { errorMessage.value = errorText(error, '导出失败')
  } finally { exporting.value = false }
}

async function refreshSelectedCurve(): Promise<void> {
  const requestedPlanId = plan.value?.plan_id
  const requestedOptionId = selectedOptionId.value
  loadedCurve.value = optionCurve(selectedOption.value) ?? null
  if (!requestedPlanId || !requestedOptionId) return
  try {
    const curve = await getPlanCurve(requestedPlanId, requestedOptionId)
    if (
      plan.value?.plan_id === requestedPlanId
      && selectedOptionId.value === requestedOptionId
    ) loadedCurve.value = curve
  } catch (error: unknown) {
    if (
      plan.value?.plan_id === requestedPlanId
      && selectedOptionId.value === requestedOptionId
    ) errorMessage.value = errorText(error, '查询能耗曲线失败')
  }
}

function toEditableTasks(tasks: ProductionTask[]): EditableTask[] {
  return tasks.map((task) => ({
    task_id: task.task_id, product: task.product,
    start_hour: task.start_minute / 60, original_start_hour: task.start_minute / 60, duration_hours: task.duration_minutes / 60,
    device_id: task.selected_device_id, due_minute: task.due_minute,
    locked: task.locked, originally_locked: task.locked, initiallyLocked: task.locked,
    fixed: !task.movable
  }))
}

function buildRequestConstraints(): OperationConstraint[] {
  const constraints: OperationConstraint[] = []
  if (peakLimitText.value) {
    const value = Number(peakLimitText.value)
    if (!Number.isFinite(value) || value <= 0) throw new Error('峰值上限必须为正数。')
    constraints.push({ constraint_id: 'C-USER-PEAK', task_id: '*', kind: 'peak_limit', value })
  }
  const rules = precedenceText.value.split(/[,，]/).map((rule) => rule.trim()).filter(Boolean)
  rules.forEach((rule, index) => {
    const [predecessor, successor, extra] = rule.split('>').map((item) => item.trim())
    if (!predecessor || !successor || extra) throw new Error(`工序顺序格式错误：${rule}`)
    constraints.push({
      constraint_id: `C-USER-PRECEDENCE-${index + 1}`,
      task_id: successor,
      kind: 'precedence',
      value: 'after',
      related_task_id: predecessor
    })
  })
  return constraints
}

function curveData(curve: EnergyCurve | undefined): EnergyPoint[] {
  return curve?.points ?? []
}

function optionCurve(option: ScheduleOption | undefined): EnergyCurve | undefined {
  return option?.curve
}

function curvePoints(points: EnergyPoint[], maximum: number): string {
  if (!points.length) return ''
  return points.map((point) => (
    `${36 + (664 * point.start_minute) / 1440},${210 - (160 * pointValue(point)) / maximum}`
  )).join(' ')
}

function pointValue(point: EnergyPoint): number { return point.power_kw }
function minuteText(minute: number): string {
  const hour = Math.floor(minute / 60)
  const remainder = minute % 60
  return `${String(hour).padStart(2, '0')}:${String(remainder).padStart(2, '0')}`
}
function metric(option: ScheduleOption, kind: 'cost' | 'peak' | 'unit'): number | undefined {
  if (kind === 'cost') return option.cost.total_cost
  if (kind === 'peak') return option.peak_kw
  return option.unit_energy_kwh
}
function number(value: number | undefined): string { return value === undefined ? '—' : new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 2 }).format(value) }
function progressValue(key: string): string {
  return plan.value?.progress.includes(key) ? 'completed' : 'pending'
}
function progressText(key: string): string { return progressValue(key) === 'completed' ? '已完成' : progressValue(key) === 'running' ? '进行中' : progressValue(key) === 'failed' ? '异常' : '等待中' }
function progressClass(key: string): string { const value = progressValue(key); return value === 'completed' ? 'done' : value === 'running' ? 'active' : value === 'failed' ? 'failed' : '' }
function historyText(entry: ChangeRecord): string { return `${entry.changed_at} · ${entry.actor}：${entry.reason}（任务 ${entry.task_id}）` }
function errorText(error: unknown, fallback: string): string { return error instanceof ApiError || error instanceof Error ? error.message : fallback }
</script>

<style>
:root { color: #203038; background: #f4f7f6; font-family: Inter, "Microsoft YaHei", system-ui, sans-serif; }
* { box-sizing: border-box; }
body { margin: 0; }
button, input, select, textarea { font: inherit; }
.page-shell { max-width: 1240px; margin: 0 auto; padding: 32px 20px 64px; }
.hero { color: #fff; background: linear-gradient(125deg, #063f47, #147567); padding: 30px; border-radius: 18px; display: flex; justify-content: space-between; gap: 24px; align-items: center; box-shadow: 0 16px 36px #063f4726; }
h1, h2, h3, p { margin-top: 0; } h1 { margin-bottom: 8px; font-size: clamp(1.7rem, 4vw, 2.35rem); } h2 { margin-bottom: 0; font-size: 1.18rem; } h3 { font-size: 1rem; margin: 20px 0 8px; }
.eyebrow { color: #4ba990; font-size: .75rem; font-weight: 750; letter-spacing: .12em; margin-bottom: 6px; text-transform: uppercase; }.hero .eyebrow { color: #b7ecd8; }
.safety-badge { border: 1px solid #a8e0cc; border-radius: 10px; padding: 10px 14px; background: #ffffff18; font-size: .88rem; text-align: center; white-space: nowrap; }
.panel { margin-top: 20px; padding: 24px; background: #fff; border: 1px solid #dce6e3; border-radius: 15px; box-shadow: 0 4px 16px #102e2a0a; }.section-heading { display: flex; justify-content: space-between; gap: 16px; align-items: center; margin-bottom: 18px; }.section-heading.compact { margin-bottom: 12px; }
.request-form { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 16px; }.request-form label, .action-form label, .approval-form label { display: grid; gap: 6px; color: #3e5352; font-size: .87rem; font-weight: 650; }
input, select, textarea { width: 100%; border: 1px solid #b8cbc7; border-radius: 8px; padding: 9px 10px; color: #203038; background: #fff; } input:focus, select:focus, textarea:focus { outline: 2px solid #37a48866; border-color: #249379; } input:disabled { background: #edf1f0; color: #71807d; }
.shift-fieldset { border: 0; padding: 0; margin: 0; display: flex; align-items: end; gap: 16px; }.shift-fieldset legend { font-size: .87rem; color: #3e5352; font-weight: 650; margin-bottom: 6px; }.check-label { display: flex !important; align-items: center; gap: 6px; font-size: .88rem !important; }.check-label input { width: auto; }
.primary-button, .secondary-button, .option-tabs button { cursor: pointer; border: 0; border-radius: 8px; font-weight: 700; }.primary-button { color: #fff; padding: 11px 18px; background: #087a64; align-self: end; }.primary-button:hover { background: #056653; }.secondary-button { color: #075848; background: #dff3ec; padding: 10px 15px; }.primary-button:disabled, .secondary-button:disabled { opacity: .55; cursor: wait; }.error-message { margin: 16px 0 0; color: #ae2f2f; background: #fff1f1; padding: 10px 12px; border-radius: 8px; }.success-message { color: #087a64; margin: 14px 0 0; }.loading, .muted { color: #687a77; font-size: .9rem; }
.progress-strip { display: grid; grid-template-columns: repeat(5, 1fr); gap: 9px; margin: 20px 0; }.progress-step { display: flex; gap: 8px; align-items: center; color: #74817f; font-size: .85rem; }.progress-step small { display: block; font-size: .73rem; margin-top: 2px; }.step-dot { width: 11px; height: 11px; flex: 0 0 11px; background: #ccd6d3; border-radius: 100%; }.progress-step.done { color: #096752; }.progress-step.done .step-dot { background: #12a77e; }.progress-step.active .step-dot { background: #edaa32; box-shadow: 0 0 0 4px #edaa3333; }.progress-step.failed { color: #b63636; }.progress-step.failed .step-dot { background: #d64c4c; }
.notice { border-left: 4px solid #0d8b6e; padding: 12px 14px; color: #31504a; background: #eaf7f2; border-radius: 6px; }.notice-warning { border-left-color: #d48824; background: #fff7e9; }.result-grid { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 20px; }.result-grid .panel { min-width: 0; }
.status-pill { padding: 5px 10px; border-radius: 20px; font-size: .8rem; font-weight: 700; }.status-pill.pending { color: #855a12; background: #fff1cf; }.status-pill.approved { color: #086149; background: #dff4ea; }.status-pill.other { color: #4d5f5b; background: #e6ecea; }.option-tabs { overflow-x: auto; display: flex; gap: 8px; padding-bottom: 8px; }.option-tabs button { color: #4d615d; background: #edf3f1; padding: 8px 10px; white-space: nowrap; }.option-tabs button.selected { color: #fff; background: #087a64; }.option-tabs span { margin-left: 6px; font-size: .7rem; opacity: .85; }
.metric-cards { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; margin-top: 13px; }.metric-cards div { padding: 12px; border-radius: 8px; background: #f5f8f7; }.metric-cards small, table small { display: block; color: #70827d; margin-bottom: 4px; }.metric-cards strong { font-size: 1.03rem; }.option-summary { margin: 13px 0 0; color: #56706a; font-size: .88rem; }
.energy-chart { width: 100%; min-height: 180px; overflow: visible; }.grid-line { stroke: #dce6e3; stroke-width: 1; }.energy-chart text { fill: #7a8a87; font-size: 12px; }.baseline-line, .selected-line { fill: none; stroke-linecap: round; stroke-linejoin: round; stroke-width: 3.5; }.baseline-line { stroke: #8fa6a0; }.selected-line { stroke: #0b9675; }.chart-legend { margin: 0; color: #60746f; font-size: .82rem; }.baseline-key, .selected-key { display: inline-block; width: 18px; height: 3px; vertical-align: middle; margin: 0 5px 0 12px; }.baseline-key { background: #8fa6a0; }.selected-key { background: #0b9675; }
.task-table-wrap { overflow-x: auto; } table { border-collapse: collapse; min-width: 730px; width: 100%; } th, td { padding: 10px; text-align: left; border-bottom: 1px solid #e2eae8; font-size: .88rem; } th { color: #60726e; font-weight: 700; background: #f6f9f8; } td input[type='number'] { min-width: 86px; } td input[type='checkbox'] { width: auto; }.locked { color: #8b5d12; font-weight: 700; }.action-form { display: grid; grid-template-columns: 180px minmax(220px, 1fr) auto; gap: 12px; align-items: end; margin-top: 18px; }.action-form .wide { min-width: 0; }.history { border-top: 1px solid #e2eae8; margin-top: 18px; }.history ol { padding-left: 20px; color: #536963; font-size: .88rem; }.history li { margin: 7px 0; }
.trace-list { display: grid; grid-template-columns: 100px 1fr; gap: 7px 14px; font-size: .9rem; margin: 0; }.trace-list dt { color: #657773; }.trace-list dd { margin: 0; word-break: break-word; }.trace-panel ul { padding-left: 20px; color: #4b625c; font-size: .9rem; }.trace-panel li { margin: 5px 0; }.approval-form { display: grid; gap: 14px; }.export-button { margin-top: 12px; }.empty-state { color: #71817e; font-size: .9rem; }
@media (max-width: 760px) { .page-shell { padding: 18px 12px 40px; }.hero { align-items: flex-start; flex-direction: column; padding: 24px; }.request-form { grid-template-columns: 1fr; }.shift-fieldset { align-items: center; }.result-grid { grid-template-columns: 1fr; }.progress-strip { grid-template-columns: 1fr; gap: 7px; }.progress-step { min-height: 28px; }.action-form { grid-template-columns: 1fr; }.metric-cards { grid-template-columns: 1fr; }.panel { padding: 18px; } }
</style>
