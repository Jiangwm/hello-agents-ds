<template>
  <section class="panel-section" aria-labelledby="budget-title">
    <div class="section-title"><h2 id="budget-title">预算消耗</h2><span>四维限制</span></div>
    <div v-if="budget" class="budget-grid">
      <label v-for="item in items" :key="item.label">
        <span><strong>{{ item.label }}</strong><small>{{ item.text }}</small></span>
        <progress :value="item.consumed" :max="item.maximum">{{ item.text }}</progress>
      </label>
    </div>
    <p v-else class="empty-copy">本次运行未配置预算。</p>
  </section>
</template>

<script setup lang="ts">
import { computed } from "vue"

import type { ResearchRun } from "../domain/schemas"

const props = defineProps<{ readonly budget: ResearchRun["budget"] }>()

const items = computed(() => {
  const budget = props.budget
  if (budget === null) return []
  return [
    { label: "工具调用", consumed: budget.consumed_tool_calls, maximum: budget.max_tool_calls, text: `${budget.consumed_tool_calls} / ${budget.max_tool_calls}` },
    { label: "成本单位", consumed: budget.consumed_cost, maximum: budget.max_cost, text: `${budget.consumed_cost} / ${budget.max_cost}` },
    { label: "扫描行数", consumed: budget.consumed_rows_scanned, maximum: budget.max_rows_scanned, text: `${budget.consumed_rows_scanned} / ${budget.max_rows_scanned}` },
    { label: "耗时毫秒", consumed: budget.consumed_elapsed_ms, maximum: budget.max_elapsed_ms, text: `${budget.consumed_elapsed_ms} / ${budget.max_elapsed_ms}` },
  ]
})
</script>
