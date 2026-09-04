<template>
  <section class="state-panel" :data-state="kind" role="status">
    <div class="state-mark" aria-hidden="true"></div>
    <div><h2>{{ title }}</h2><p>{{ message }}</p></div>
  </section>
</template>

<script setup lang="ts">
defineProps<{
  readonly kind: "empty" | "loading" | "error"
  readonly title: string
  readonly message: string
}>()
</script>
