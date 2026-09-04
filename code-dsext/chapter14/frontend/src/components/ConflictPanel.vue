<template>
  <section v-if="conflicts.length" class="panel-section conflict-panel" aria-labelledby="conflict-title">
    <div class="section-title"><h2 id="conflict-title">冲突与未知</h2><span>{{ conflicts.length }} 项</span></div>
    <article v-for="item in conflicts" :key="item.note_id">
      <strong>{{ item.body }}</strong>
      <dl>
        <div><dt>支持证据</dt><dd>{{ item.supporting_evidence_ids.join("、") || "无" }}</dd></div>
        <div><dt>反证</dt><dd>{{ item.counter_evidence_ids.join("、") || "无" }}</dd></div>
        <div><dt>缺失</dt><dd>{{ item.missing_evidence_ids.join("、") || "无" }}</dd></div>
        <div><dt>下一步</dt><dd>{{ item.next_action ?? "等待人工决策" }}</dd></div>
      </dl>
    </article>
  </section>
</template>

<script setup lang="ts">
import { computed } from "vue"

import type { ResearchNote } from "../domain/schemas"

const props = defineProps<{ readonly notes: readonly ResearchNote[] }>()
const conflicts = computed(() => props.notes.filter((note) =>
  note.note_type === "conflict" || note.counter_evidence_ids.length > 0 || note.missing_evidence_ids.length > 0,
))
</script>
