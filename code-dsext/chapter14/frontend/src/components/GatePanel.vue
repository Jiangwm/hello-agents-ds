<template>
  <section class="panel-section gate-panel" aria-labelledby="gate-title">
    <div class="section-title"><h2 id="gate-title">人工关口</h2><span>HITL</span></div>
    <ol class="gate-list">
      <li v-for="gate in gates" :key="gate.gate_id" :data-status="gate.status">
        <div><strong>{{ gateNameLabel(gate.name) }}</strong><span class="status-badge">{{ gate.status }}</span></div>
        <p>{{ gate.rationale ?? "尚未触发" }}</p>
        <code :title="gate.input_hash">{{ shortHash(gate.input_hash) }}</code>
        <button class="button compact" type="button" :disabled="gate.status !== 'awaiting_human' || pending" @click="$emit('approve', gate)">审批</button>
      </li>
    </ol>
  </section>
</template>

<script setup lang="ts">
import { gateNameLabel, shortHash } from "../domain/presenters"
import type { Gate } from "../domain/schemas"

defineProps<{ readonly gates: readonly Gate[]; readonly pending: boolean }>()
defineEmits<{ approve: [gate: Gate] }>()
</script>
