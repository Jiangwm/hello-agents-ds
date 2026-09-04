<template>
  <form class="research-form" @submit.prevent="submit">
    <div class="field field-wide">
      <label for="objective">研究问题</label>
      <textarea id="objective" v-model="form.objective" rows="2" required :disabled="disabled" />
    </div>
    <div class="field"><label for="tenant">租户</label><input id="tenant" v-model="form.tenant_id" required :disabled="disabled" /></div>
    <div class="field"><label for="line">产线</label><input id="line" v-model="form.line_id" required :disabled="disabled" /></div>
    <div class="field"><label for="product">产品</label><input id="product" v-model="form.product" required :disabled="disabled" /></div>
    <div class="field"><label for="batch">批次</label><input id="batch" v-model="form.batch_id" required :disabled="disabled" /></div>
    <div class="field"><label for="start">开始时间</label><input id="start" v-model="form.start_at" type="datetime-local" required :disabled="disabled" /></div>
    <div class="field"><label for="end">结束时间</label><input id="end" v-model="form.end_at" type="datetime-local" required :disabled="disabled" /></div>
    <button class="button primary" type="submit" :disabled="disabled">{{ disabled ? "运行已创建" : "创建研究计划" }}</button>
  </form>
</template>

<script setup lang="ts">
import { reactive } from "vue"

import type { CreateRequest } from "../services/api"

const props = defineProps<{ readonly disabled: boolean }>()
const emit = defineEmits<{ submit: [request: CreateRequest] }>()

const form = reactive({
  tenant_id: "TENANT-DEMO",
  line_id: "LINE-A",
  product: "P-100",
  batch_id: "BATCH-DEMO-0715",
  start_at: "2026-07-01T00:00",
  end_at: "2026-07-21T23:59",
  objective: "定位 2026-07-15 起质量不良率上升的可证伪原因",
})

function submit(): void {
  if (props.disabled) return
  emit("submit", {
    ...form,
    start_at: `${form.start_at}:00+08:00`,
    end_at: `${form.end_at}:00+08:00`,
  })
}
</script>
