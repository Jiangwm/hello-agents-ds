<template>
  <section class="panel-section" aria-labelledby="note-title">
    <div class="section-title"><h2 id="note-title">证据笔记</h2><span>仅批准笔记进入报告</span></div>
    <div v-if="notes.length" class="note-list">
      <article v-for="note in notes" :key="note.note_id" :data-status="note.status">
        <header><strong>{{ note.note_type }} · {{ note.note_id }}</strong><span class="status-badge">{{ note.status }}</span></header>
        <p>{{ note.body }}</p>
        <small>证据：{{ allEvidence(note).join("、") || "无" }}</small>
        <small v-if="note.next_action">下一步：{{ note.next_action }}</small>
        <div v-if="note.status === 'draft'" class="inline-actions">
          <button class="button compact" type="button" :disabled="pending" @click="$emit('review', note, true)">批准</button>
          <button class="button compact secondary" type="button" :disabled="pending" @click="$emit('review', note, false)">拒绝</button>
        </div>
      </article>
    </div>
    <p v-else class="empty-copy">工具结果尚未整理为可审查笔记。</p>
  </section>
</template>

<script setup lang="ts">
import type { ResearchNote } from "../domain/schemas"

defineProps<{ readonly notes: readonly ResearchNote[]; readonly pending: boolean }>()
defineEmits<{ review: [note: ResearchNote, approved: boolean] }>()

function allEvidence(note: ResearchNote): readonly string[] {
  return [
    ...note.evidence_ids,
    ...note.supporting_evidence_ids,
    ...note.counter_evidence_ids,
    ...note.missing_evidence_ids,
  ]
}
</script>
