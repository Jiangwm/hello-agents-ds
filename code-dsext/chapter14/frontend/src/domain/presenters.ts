import type { Gate, TodoItem } from "./schemas"

const TODO_LABELS = {
  pending: "待执行",
  running: "执行中",
  blocked: "已阻塞",
  awaiting_human: "待人工",
  completed: "已完成",
} as const

const GATE_LABELS = {
  research_plan: "研究计划",
  cross_domain_access: "跨域访问",
  budget_extension: "预算扩展",
  validation_experiment: "验证实验",
  final_conclusion: "最终结论",
} as const

export function todoStatusLabel(status: TodoItem["status"]): string {
  return TODO_LABELS[status]
}

export function gateNameLabel(name: Gate["name"]): string {
  return GATE_LABELS[name]
}

export function percent(value: number | null): string {
  return value === null ? "未知" : `${Math.round(value * 100)}%`
}

export function shortHash(value: string): string {
  return `${value.slice(0, 10)}…${value.slice(-6)}`
}

export function budgetRatio(consumed: number, maximum: number): number {
  if (maximum === 0) return 0
  return Math.min(1, consumed / maximum)
}
