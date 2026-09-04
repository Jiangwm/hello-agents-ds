<template>
  <div class="app-shell">
    <SafetyHeader :run-id="run?.run_id ?? null" />
    <main class="product-content">
      <ResearchForm :disabled="run !== null || state.actionPending" @submit="start" />
      <template v-if="run">
        <SummaryStrip :completed="completedCount" :total="run.todos.length" :current-tool="run.current_tool" :evidence-count="run.evidence.length" :scope-version="run.scope_version" />
        <StatusBanner :status="run.status" :status-label="run.status" :headline="statusHeadline" :detail="run.blocked_reason ?? statusDetail" />
        <div class="workspace-grid">
          <TodoTree :todos="run.todos" :selected-id="selectedTodoId" @select="selectedTodoId = $event" />
          <div class="evidence-stack">
            <EvidencePartition :evidence="run.evidence" />
            <ConflictPanel :notes="run.notes" />
            <ReportSections :markdown="run.report_markdown" />
          </div>
          <div class="side-stack">
            <BudgetMeter :budget="run.budget" />
            <GatePanel :gates="run.gates" :pending="state.actionPending" @approve="openGate" />
            <NoteReview :notes="run.notes" :pending="state.actionPending" @review="openNote" />
          </div>
        </div>
        <ActionCluster :run="run" :pending="state.actionPending" @step="step" @run="runAll" @pause="pause" @revise="revise" @resume="resume" @export="exportResult" />
      </template>
      <StatePanel v-else-if="state.screen.kind === 'loading'" kind="loading" title="正在恢复研究" :message="state.screen.message" />
      <StatePanel v-else-if="state.screen.kind === 'error'" kind="error" title="请求失败" :message="state.screen.message" />
      <StatePanel v-else kind="empty" title="等待研究问题" message="填写脱敏范围并创建计划，系统不会连接生产控制系统。" />
      <DecisionDrawer :decision="decision" :pending="state.actionPending" @confirm="confirmDecision" @cancel="decision = null" />
      <p v-if="streamMessage" class="stream-message" role="status">{{ streamMessage }}</p>
    </main>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, shallowRef } from "vue"
import { z } from "zod"

import { initialState, reduceAppState, type AppAction, type AppState } from "../domain/state"
import type { Gate, ResearchNote, ResearchRun } from "../domain/schemas"
import { approveGate, createRun, downloadExport, getReport, getRun, pauseRun, resumeRun, reviseRun, reviewNote, runUntilStop, stepRun, type CreateRequest, type RunAccess } from "../services/api"
import { streamEvents } from "../services/events"
import ActionCluster from "./ActionCluster.vue"
import BudgetMeter from "./BudgetMeter.vue"
import ConflictPanel from "./ConflictPanel.vue"
import DecisionDrawer, { type Decision } from "./DecisionDrawer.vue"
import EvidencePartition from "./EvidencePartition.vue"
import GatePanel from "./GatePanel.vue"
import NoteReview from "./NoteReview.vue"
import ReportSections from "./ReportSections.vue"
import ResearchForm from "./ResearchForm.vue"
import SafetyHeader from "./SafetyHeader.vue"
import StatePanel from "./StatePanel.vue"
import StatusBanner from "./StatusBanner.vue"
import SummaryStrip from "./SummaryStrip.vue"
import TodoTree from "./TodoTree.vue"

const state = shallowRef<AppState>(initialState)
const selectedTodoId = ref<string | null>(null)
const decision = ref<Decision | null>(null)
const streamMessage = ref("")
const tenantId = ref("TENANT-DEMO")
let streamController: AbortController | null = null

const run = computed<ResearchRun | null>(() => state.value.screen.kind === "ready" ? state.value.screen.run : null)
const completedCount = computed(() => run.value?.todos.filter((todo) => todo.status === "completed").length ?? 0)
const statusHeadline = computed(() => run.value?.paused ? "研究已暂停，工具调用保持为零" : run.value?.status === "blocked" ? "研究已失败关闭" : "研究状态已持久化")
const statusDetail = computed(() => state.value.actionPending ? "正在执行只读操作" : "等待下一项受控操作")

function dispatch(action: AppAction): void {
  state.value = reduceAppState(state.value, action)
}

async function start(request: CreateRequest): Promise<void> {
  tenantId.value = request.tenant_id
  await perform("正在创建研究计划", () => createRun(request))
}

async function perform(message: string, operation: () => Promise<ResearchRun>): Promise<void> {
  dispatch({ kind: "action_started" })
  streamMessage.value = message
  try {
    const next = await operation()
    dispatch({ kind: "loaded", run: next })
    selectedTodoId.value = selectedTodoId.value ?? next.todos[0]?.todo_id ?? null
    connect({ runId: next.run_id, tenantId: tenantId.value })
  } catch (error) {
    dispatch({ kind: "failed", message: error instanceof Error ? error.message : "未知请求错误" })
  } finally {
    dispatch({ kind: "action_finished" })
  }
}

async function step(): Promise<void> { const access = currentAccess(); if (access) await perform("执行一个 TODO", () => stepRun(access)) }
async function runAll(): Promise<void> { const access = currentAccess(); if (access) await perform("运行至下一人工关口", () => runUntilStop(access)) }
async function pause(): Promise<void> { const access = currentAccess(); if (access) await perform("暂停研究", () => pauseRun(access)) }
async function resume(): Promise<void> { const access = currentAccess(); if (access) await perform("恢复研究", () => resumeRun(access)) }
async function revise(): Promise<void> {
  const current = run.value
  const access = currentAccess()
  if (current === null || access === null) return
  const objective = window.prompt("请输入修订后的研究问题", current.scope.objective)
  if (objective !== null && objective.trim() !== "") await perform("修改范围并使下游审批失效", () => reviseRun(access, current, objective.trim()))
}

function openGate(gate: Gate): void { decision.value = { kind: "gate", gate } }
function openNote(note: ResearchNote, approved: boolean): void { decision.value = { kind: "note", note, approved } }

async function confirmDecision(reviewer: string, reason: string): Promise<void> {
  const current = run.value
  const pendingDecision = decision.value
  const access = currentAccess()
  if (current === null || pendingDecision === null || access === null) return
  dispatch({ kind: "action_started" })
  try {
    const next = pendingDecision.kind === "gate"
      ? await approveGate({ access, gateId: pendingDecision.gate.gate_id, approver: reviewer, reason })
      : await reviewNote({ access, reviewer, reason })
    decision.value = null
    dispatch({ kind: "loaded", run: await withReport(next, access) })
  } catch (error) {
    streamMessage.value = error instanceof Error ? error.message : "人工决策记录失败"
  } finally {
    dispatch({ kind: "action_finished" })
  }
}

async function exportResult(): Promise<void> {
  const current = run.value
  const access = currentAccess()
  if (current === null || access === null) return
  dispatch({ kind: "action_started" })
  try { await downloadExport(access) }
  catch (error) { streamMessage.value = error instanceof Error ? error.message : "导出失败" }
  finally { dispatch({ kind: "action_finished" }) }
}

function connect(access: RunAccess): void {
  streamController?.abort()
  streamController = new AbortController()
  void streamEvents({
    runId: access.runId, tenantId: access.tenantId,
    lastEventId: state.value.lastEventId, signal: streamController.signal,
    handlers: {
      onEvent: (event) => { dispatch({ kind: "event", event }); void refresh(access) },
      onError: (message) => { streamMessage.value = message },
    },
  })
}

async function refresh(access: RunAccess): Promise<void> {
  try { dispatch({ kind: "loaded", run: await withReport(await getRun(access), access) }) }
  catch (error) { streamMessage.value = error instanceof Error ? error.message : "状态刷新失败" }
}

onMounted(() => {
  const candidate: unknown = new URLSearchParams(window.location.search).get("run_id")
  const tenant: unknown = new URLSearchParams(window.location.search).get("tenant_id")
  const parsed = z.string().min(1).nullable().parse(candidate)
  tenantId.value = z.string().min(1).nullable().parse(tenant) ?? "TENANT-DEMO"
  if (parsed !== null) void perform("正在恢复持久化运行", () => getRun({ runId: parsed, tenantId: tenantId.value }))
})

onBeforeUnmount(() => streamController?.abort())

function currentAccess(): RunAccess | null {
  const current = run.value
  return current === null ? null : { runId: current.run_id, tenantId: tenantId.value }
}

async function withReport(current: ResearchRun, access: RunAccess): Promise<ResearchRun> {
  if (current.status !== "completed") return current
  return { ...current, report_markdown: await getReport(access) }
}
</script>
