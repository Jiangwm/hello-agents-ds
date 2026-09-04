<template>
  <section class="summary-strip" aria-label="研究摘要">
    <div><span>TODO</span><strong>{{ completed }}/{{ total }}</strong></div>
    <div><span>当前工具</span><strong>{{ currentTool ?? "等待调度" }}</strong></div>
    <div><span>证据</span><strong>{{ evidenceCount }}</strong></div>
    <div><span>范围版本</span><strong>{{ scopeVersion }}</strong></div>
  </section>
</template>

<script setup lang="ts">
defineProps<{
  readonly completed: number
  readonly total: number
  readonly currentTool: string | null
  readonly evidenceCount: number
  readonly scopeVersion: string
}>()
</script>
