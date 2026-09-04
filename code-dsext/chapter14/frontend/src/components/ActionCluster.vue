<template>
  <section class="action-cluster" aria-label="研究操作">
    <button class="button" type="button" :disabled="!canStep || pending" @click="$emit('step')">执行一步</button>
    <button class="button primary" type="button" :disabled="!canRun || pending" @click="$emit('run')">运行至下一关口</button>
    <button class="button secondary" type="button" :disabled="!canPause || pending" @click="$emit('pause')">暂停</button>
    <button class="button secondary" type="button" :disabled="!canRevise || pending" @click="$emit('revise')">修改范围</button>
    <button class="button secondary" type="button" :disabled="!canResume || pending" @click="$emit('resume')">恢复</button>
    <button class="button" type="button" :disabled="!canExport || pending" @click="$emit('export')">审批后导出</button>
  </section>
</template>

<script setup lang="ts">
import { computed } from "vue"

import type { ResearchRun } from "../domain/schemas"

const props = defineProps<{ readonly run: ResearchRun; readonly pending: boolean }>()
defineEmits<{ step: []; run: []; pause: []; revise: []; resume: []; export: [] }>()

const canStep = computed(() => ["planned", "running", "blocked"].includes(props.run.status) && !props.run.paused)
const canRun = computed(() => ["planned", "running", "blocked"].includes(props.run.status) && !props.run.paused)
const canPause = computed(() => props.run.status === "running")
const canRevise = computed(() => props.run.status === "paused")
const canResume = computed(() => props.run.status === "paused")
const canExport = computed(() => props.run.status === "completed" && props.run.gates.some((gate) => gate.name === "final_conclusion" && gate.status === "approved"))
</script>
