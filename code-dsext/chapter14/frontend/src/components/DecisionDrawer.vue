<template>
  <section v-if="decision" class="decision-drawer" aria-labelledby="decision-title">
    <header>
      <div><p class="overline">HUMAN IN THE LOOP</p><h2 id="decision-title">{{ title }}</h2></div>
      <button class="button compact secondary" type="button" @click="$emit('cancel')">取消</button>
    </header>
    <p>{{ description }}</p>
    <form @submit.prevent="confirm">
      <div class="field"><label for="reviewer">审批人</label><input id="reviewer" v-model="reviewer" required autocomplete="name" /></div>
      <div class="field"><label for="reason">审批理由</label><textarea id="reason" v-model="reason" rows="3" required /></div>
      <button class="button primary" type="submit" :disabled="pending">{{ pending ? "正在记录" : "提交人工决策" }}</button>
    </form>
  </section>
</template>

<script setup lang="ts">
import { computed, ref } from "vue"

import type { Gate, ResearchNote } from "../domain/schemas"

export type Decision =
  | { readonly kind: "gate"; readonly gate: Gate }
  | { readonly kind: "note"; readonly note: ResearchNote; readonly approved: boolean }

const props = defineProps<{ readonly decision: Decision | null; readonly pending: boolean }>()
const emit = defineEmits<{ confirm: [reviewer: string, reason: string]; cancel: [] }>()
const reviewer = ref("QA Reviewer")
const reason = ref("已核对输入哈希、范围版本与证据边界")

const title = computed(() => {
  const decision = props.decision
  if (decision === null) return "人工决策"
  return decision.kind === "gate" ? `审批 ${decision.gate.name}` : `${decision.approved ? "批准" : "拒绝"}笔记 ${decision.note.note_id}`
})

const description = computed(() => {
  const decision = props.decision
  if (decision === null) return ""
  return decision.kind === "gate"
    ? `输入哈希 ${decision.gate.input_hash}，范围 ${decision.gate.scope_version}`
    : decision.note.body
})

function confirm(): void {
  if (reviewer.value.trim() === "" || reason.value.trim() === "") return
  emit("confirm", reviewer.value.trim(), reason.value.trim())
}
</script>
