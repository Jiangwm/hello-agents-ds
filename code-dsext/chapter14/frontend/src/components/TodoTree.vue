<template>
  <section class="panel-section todo-panel" aria-labelledby="todo-title">
    <div class="section-title"><h2 id="todo-title">依赖化 TODO</h2><span>{{ todos.length }} 项</span></div>
    <ol v-if="todos.length" class="todo-tree">
      <li v-for="todo in todos" :key="todo.todo_id" :data-status="todo.status">
        <button type="button" :aria-current="todo.todo_id === selectedId ? 'step' : undefined" @click="$emit('select', todo.todo_id)">
          <span class="todo-index">{{ todo.todo_id.replace("todo-", "") }}</span>
          <span class="todo-copy"><strong>{{ todo.title }}</strong><small>{{ todo.question }}</small></span>
          <span class="status-badge">{{ todoStatusLabel(todo.status) }}</span>
        </button>
        <div v-if="todo.todo_id === selectedId" class="todo-detail">
          <dl>
            <div><dt>数据范围</dt><dd>{{ todo.data_scope }}</dd></div>
            <div><dt>依赖</dt><dd>{{ todo.dependencies.join("、") || "无" }}</dd></div>
            <div><dt>工具</dt><dd><code>{{ todo.required_tools.join(" · ") }}</code></dd></div>
            <div><dt>来源质量</dt><dd>{{ todo.source_quality }}</dd></div>
            <div><dt>候选结论</dt><dd>{{ todo.candidate_conclusion ?? "尚未形成" }}</dd></div>
            <div><dt>置信度</dt><dd>{{ percent(todo.confidence) }}</dd></div>
            <div><dt>反证条件</dt><dd>{{ todo.falsification_conditions.join("；") }}</dd></div>
            <div><dt>失败或阻塞</dt><dd>{{ todo.failure_reason ?? "无" }}</dd></div>
          </dl>
        </div>
      </li>
    </ol>
    <p v-else class="empty-copy">计划审批后将生成依赖化调查任务。</p>
  </section>
</template>

<script setup lang="ts">
import { percent, todoStatusLabel } from "../domain/presenters"
import type { TodoItem } from "../domain/schemas"

defineProps<{ readonly todos: readonly TodoItem[]; readonly selectedId: string | null }>()
defineEmits<{ select: [todoId: string] }>()
</script>
