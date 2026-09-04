<template>
  <section class="panel-section report-panel" aria-labelledby="report-title">
    <div class="section-title"><h2 id="report-title">研究报告</h2><span>固定七节</span></div>
    <div v-if="sections.length" class="report-sections">
      <article v-for="section in sections" :key="section.title">
        <h3>{{ section.title }}</h3>
        <p>{{ section.body || "本节等待批准笔记。" }}</p>
      </article>
    </div>
    <p v-else class="empty-copy">最终报告将在证据笔记通过审查后生成。</p>
  </section>
</template>

<script setup lang="ts">
import { computed } from "vue"

const props = defineProps<{ readonly markdown: string | null }>()

const sections = computed(() => {
  if (props.markdown === null) return []
  return props.markdown
    .split(/\n(?=##\s)/)
    .map((block) => {
      const lines = block.trim().split("\n")
      const title = lines[0] ?? "未命名章节"
      return { title: title.replace(/^##\s*/, ""), body: lines.slice(1).join(" ").trim() }
    })
    .filter((section) => section.title.length > 0)
})
</script>
