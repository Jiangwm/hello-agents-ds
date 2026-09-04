<template>
  <section class="panel-section evidence-panel" aria-labelledby="evidence-title">
    <div class="section-title"><h2 id="evidence-title">证据账本</h2><span>{{ evidence.length }} 条</span></div>
    <div class="partition-grid">
      <section v-for="partition in partitions" :key="partition.id" class="partition">
        <header><h3>{{ partition.title }}</h3><p>{{ partition.rule }}</p></header>
        <article v-for="item in partition.items" :key="item.evidence_id" class="evidence-row" :data-status="item.status">
          <div class="evidence-heading"><strong>{{ item.evidence_id }}</strong><span class="status-badge">{{ item.status }}</span></div>
          <p>{{ item.query }}</p>
          <dl>
            <div><dt>来源</dt><dd>{{ item.source_uri }}</dd></div>
            <div><dt>版本 / 窗口</dt><dd>{{ item.source_version }} / {{ item.data_window }}</dd></div>
            <div><dt>权限</dt><dd>{{ item.permission_level }}</dd></div>
            <div><dt>质量 / 置信</dt><dd>{{ item.source_quality }} / {{ percent(item.confidence) }}</dd></div>
            <div><dt>哈希</dt><dd><code :title="item.content_hash">{{ shortHash(item.content_hash) }}</code></dd></div>
          </dl>
        </article>
        <p v-if="partition.items.length === 0" class="empty-copy">该分区暂无证据。</p>
      </section>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed } from "vue"

import { percent, shortHash } from "../domain/presenters"
import type { Evidence } from "../domain/schemas"

const props = defineProps<{ readonly evidence: readonly Evidence[] }>()

const partitions = computed(() => [
  {
    id: "internal",
    title: "内部工厂事实",
    rule: "可支撑本产线事实，仍需审批笔记引用。",
    items: props.evidence.filter((item) => item.partition !== "external_general_knowledge"),
  },
  {
    id: "external",
    title: "外部通用知识",
    rule: "只能提供解释框架，不能单独形成工厂结论。",
    items: props.evidence.filter((item) => item.partition === "external_general_knowledge"),
  },
])
</script>
