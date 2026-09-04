<template>
  <section class="status-banner" :data-status="status" aria-live="polite">
    <div>
      <span class="status-badge">{{ statusLabel }}</span>
      <strong>{{ headline }}</strong>
    </div>
    <p>{{ detail }}</p>
  </section>
</template>

<script setup lang="ts">
defineProps<{
  readonly status: string
  readonly statusLabel: string
  readonly headline: string
  readonly detail: string
}>()
</script>
