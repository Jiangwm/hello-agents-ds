<template>
  <header class="safety-header">
    <div class="brand-lockup">
      <span class="brand-mark" aria-hidden="true">QA</span>
      <div>
        <p class="overline">QUALITY DEEP RESEARCH</p>
        <h1>质量异常证据驾驶舱</h1>
      </div>
    </div>
    <div class="safety-copy" role="status">
      <strong>离线 · 只读 · 可审计</strong>
      <span>禁止连接或控制 MES / PLC / DCS</span>
    </div>
    <div v-if="runId" class="run-identity">
      <span>运行 ID</span>
      <code>{{ runId }}</code>
    </div>
  </header>
</template>

<script setup lang="ts">
defineProps<{ readonly runId: string | null }>()
</script>
