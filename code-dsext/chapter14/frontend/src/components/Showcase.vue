<template>
  <div class="showcase-page">
    <SafetyHeader :run-id="run.run_id" />
    <main class="showcase-content">
      <header class="showcase-intro">
        <p class="overline">PRIMITIVE SHOWCASE</p>
        <h2>原语、状态与响应式校验</h2>
        <p>在产品页面组合前，集中验证默认、加载、空、错误、禁用与人工审批状态。</p>
      </header>
      <ResearchForm :disabled="true" />
      <SummaryStrip :completed="2" :total="3" :current-tool="null" :evidence-count="run.evidence.length" :scope-version="run.scope_version" />
      <StatusBanner status="blocked" status-label="已阻塞" headline="冲突证据需要人工核验" :detail="run.blocked_reason ?? '等待处理'" />
      <div class="showcase-grid">
        <TodoTree :todos="run.todos" selected-id="todo-2" />
        <EvidencePartition :evidence="run.evidence" />
        <div class="side-stack">
          <BudgetMeter :budget="run.budget" />
          <GatePanel :gates="run.gates" :pending="false" />
          <NoteReview :notes="run.notes" :pending="false" />
        </div>
      </div>
      <ConflictPanel :notes="run.notes" />
      <ReportSections :markdown="run.report_markdown" />
      <ActionCluster :run="run" :pending="false" />
      <div class="state-grid">
        <StatePanel kind="empty" title="空状态" message="创建研究计划后显示调查任务。" />
        <StatePanel kind="loading" title="加载状态" message="正在恢复持久化运行状态。" />
        <StatePanel kind="error" title="错误状态" message="无法连接离线 API，请确认本机服务。" />
      </div>
    </main>
  </div>
</template>

<script setup lang="ts">
import { showcaseRun as run } from "../domain/mock"
import ActionCluster from "./ActionCluster.vue"
import BudgetMeter from "./BudgetMeter.vue"
import ConflictPanel from "./ConflictPanel.vue"
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
</script>
