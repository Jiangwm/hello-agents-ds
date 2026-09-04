import { z } from "zod"

export type JsonValue =
  | string
  | number
  | boolean
  | null
  | readonly JsonValue[]
  | { readonly [key: string]: JsonValue }

export const JsonValueSchema: z.ZodType<JsonValue> = z.lazy(() => z.union([
  z.string(),
  z.number().finite(),
  z.boolean(),
  z.null(),
  z.array(JsonValueSchema),
  z.record(JsonValueSchema),
]))

export const TodoStatusSchema = z.enum([
  "pending",
  "running",
  "blocked",
  "awaiting_human",
  "completed",
])

export const TodoOutcomeSchema = z.enum([
  "evidence",
  "negative_result",
  "missing_data",
  "conflict",
  "budget_exhausted",
  "failed",
])

export const GateStatusSchema = z.enum([
  "pending",
  "awaiting_human",
  "approved",
  "rejected",
])

export const EvidenceSchema = z.object({
  evidence_id: z.string().min(1),
  source_uri: z.string().min(1),
  source_version: z.string().min(1),
  retrieved_at: z.string().min(1),
  permission_level: z.string().min(1),
  source_quality: z.string().min(1),
  tool_call_id: z.string().min(1),
  query: z.string().min(1),
  partition: z.string().min(1),
  status: z.enum(["found", "negative_result", "missing_data", "conflict"]),
  negative_result: z.boolean(),
  content_hash: z.string().length(64),
  confidence: z.number().finite().min(0).max(1),
  data_window: z.string().min(1),
}).readonly()

export const NoteSchema = z.object({
  note_id: z.string().min(1),
  note_type: z.enum([
    "fact",
    "negative_result",
    "hypothesis",
    "conflict",
    "unknown",
    "decision",
    "validation_plan",
    "summary",
  ]),
  status: z.enum(["draft", "approved", "rejected", "superseded"]),
  todo_id: z.string().min(1),
  body: z.string().min(1),
  input_hash: z.string().length(64),
  evidence_ids: z.array(z.string()).readonly(),
  reviewer: z.string().nullable(),
  supporting_evidence_ids: z.array(z.string()).readonly(),
  counter_evidence_ids: z.array(z.string()).readonly(),
  missing_evidence_ids: z.array(z.string()).readonly(),
  next_action: z.string().nullable(),
}).readonly()

export const TodoSchema = z.object({
  todo_id: z.string().min(1),
  title: z.string().min(1),
  question: z.string().min(1),
  data_scope: z.string().min(1),
  required_tools: z.array(z.string()).readonly(),
  status: TodoStatusSchema,
  outcome: TodoOutcomeSchema.nullable(),
  dependencies: z.array(z.string()).readonly(),
  evidence_ids: z.array(z.string()).readonly(),
  negative_results: z.array(z.string()).readonly(),
  source_quality: z.string().min(1),
  candidate_conclusion: z.string().nullable(),
  confidence: z.number().finite().min(0).max(1).nullable(),
  falsification_conditions: z.array(z.string()).readonly(),
  cost_units: z.number().finite().nonnegative(),
  failure_reason: z.string().nullable(),
}).readonly()

export const BudgetSchema = z.object({
  max_tool_calls: z.number().finite().nonnegative(),
  max_cost: z.number().finite().nonnegative(),
  max_rows_scanned: z.number().finite().nonnegative(),
  max_elapsed_ms: z.number().finite().nonnegative(),
  consumed_tool_calls: z.number().finite().nonnegative(),
  consumed_cost: z.number().finite().nonnegative(),
  consumed_rows_scanned: z.number().finite().nonnegative(),
  consumed_elapsed_ms: z.number().finite().nonnegative(),
}).readonly()

export const GateSchema = z.object({
  gate_id: z.string().min(1),
  name: z.enum([
    "research_plan",
    "cross_domain_access",
    "budget_extension",
    "validation_experiment",
    "final_conclusion",
  ]),
  input_hash: z.string().length(64),
  plan_version: z.string().min(1),
  scope_version: z.string().min(1),
  status: GateStatusSchema,
  rationale: z.string().nullable(),
}).readonly()

export const RunSchema = z.object({
  run_id: z.string().min(1),
  status: z.enum(["planned", "running", "paused", "blocked", "completed", "failed"]),
  scope: z.object({
    scope_id: z.string().min(1),
    objective: z.string().min(1),
    asset_ids: z.array(z.string()).readonly(),
    data_domains: z.array(z.string()).readonly(),
  }).readonly(),
  plan_version: z.string().min(1),
  scope_version: z.string().min(1),
  todos: z.array(TodoSchema).readonly(),
  evidence: z.array(EvidenceSchema).readonly(),
  notes: z.array(NoteSchema).readonly(),
  budget: BudgetSchema.nullable(),
  gates: z.array(GateSchema).readonly(),
  paused: z.boolean(),
  current_tool: z.string().nullable(),
  blocked_reason: z.string().nullable(),
  report_markdown: z.string().nullable(),
}).readonly()

export const HealthSchema = z.object({
  status: z.string(),
  offline: z.literal(true),
  read_only: z.literal(true),
  production_control: z.literal("prohibited"),
}).readonly()

export const ApiEventSchema = z.object({
  event_id: z.string().min(1),
  event_type: z.string().min(1),
  occurred_at: z.string().min(1),
  payload: z.record(JsonValueSchema).readonly(),
}).readonly()

export type ResearchRun = z.infer<typeof RunSchema>
export type TodoItem = z.infer<typeof TodoSchema>
export type Evidence = z.infer<typeof EvidenceSchema>
export type ResearchNote = z.infer<typeof NoteSchema>
export type Gate = z.infer<typeof GateSchema>
export type ApiEvent = z.infer<typeof ApiEventSchema>
