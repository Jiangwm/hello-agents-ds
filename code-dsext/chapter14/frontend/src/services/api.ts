import ky from "ky"
import { z } from "zod"

import {
  HealthSchema,
  RunSchema,
  type ResearchRun,
} from "../domain/schemas"

const API_BASE = (import.meta.env["VITE_API_BASE_URL"] ?? "http://127.0.0.1:8014").replace(/\/$/, "")

const client = ky.create({
  prefixUrl: API_BASE,
  timeout: 15_000,
  retry: { limit: 0 },
})

const RunEnvelopeSchema = z.object({ run: RunSchema }).readonly()

const ReportSchema = z.object({
  markdown: z.string(),
}).readonly()

const CreateRequestSchema = z.object({
  tenant_id: z.string().min(1),
  line_id: z.string().min(1),
  product: z.string().min(1),
  batch_id: z.string().min(1),
  start_at: z.string().datetime({ offset: true }),
  end_at: z.string().datetime({ offset: true }),
  objective: z.string().min(1),
}).readonly()

export type CreateRequest = z.infer<typeof CreateRequestSchema>

export type RunAccess = {
  readonly runId: string
  readonly tenantId: string
}

export type GateDecision = {
  readonly access: RunAccess
  readonly gateId: string
  readonly approver: string
  readonly reason: string
}

export type NoteDecision = {
  readonly access: RunAccess
  readonly reviewer: string
  readonly reason: string
}

type RunRequest = {
  readonly path: string
  readonly method: "get" | "post"
  readonly tenantId: string | null
  readonly body: unknown | null
}

export class ApiError extends Error {
  readonly name = "ApiError"

  constructor(
    message: string,
    readonly status: number | null,
    options?: ErrorOptions,
  ) {
    super(message, options)
  }
}

async function parseResponse<T>(response: Response, schema: z.ZodType<T>): Promise<T> {
  const payload: unknown = await response.json()
  const parsed = schema.safeParse(payload)
  if (!parsed.success) {
    throw new ApiError("后端响应不符合质量研究契约", response.status, { cause: parsed.error })
  }
  return parsed.data
}

async function requestRun(request: RunRequest): Promise<ResearchRun> {
  try {
    const headers = request.tenantId === null ? {} : { "X-Tenant-ID": request.tenantId }
    const response = request.method === "get"
      ? await client.get(request.path, { headers })
      : await client.post(request.path, request.body === null ? { headers } : { headers, json: request.body })
    const payload: unknown = await response.json()
    const direct = RunSchema.safeParse(payload)
    if (direct.success) return direct.data
    const envelope = RunEnvelopeSchema.safeParse(payload)
    if (envelope.success) return envelope.data.run
    throw new ApiError("后端运行响应不符合质量研究契约", response.status, { cause: direct.error })
  } catch (error) {
    if (error instanceof ApiError) throw error
    if (error instanceof Error) throw new ApiError(error.message, null, { cause: error })
    throw error
  }
}

export async function getHealth(): Promise<z.infer<typeof HealthSchema>> {
  return parseResponse(await client.get("health"), HealthSchema)
}

export function createRun(request: CreateRequest): Promise<ResearchRun> {
  const input = CreateRequestSchema.parse(request)
  const runId = `run-ui-${crypto.randomUUID()}`
  return requestRun({ path: "runs", method: "post", tenantId: null, body: {
    run_id: runId,
    actor: {
      actor_id: "ui-analyst",
      tenant_id: input.tenant_id,
      line_id: input.line_id,
      role: "analyst",
      domains: ["quality", "process", "internal_docs"],
    },
    context: {
      tenant_id: input.tenant_id,
      line_id: input.line_id,
      product: input.product,
      batch_id: input.batch_id,
      start_at: input.start_at,
      end_at: input.end_at,
    },
    scope: {
      scope_id: `scope-${crypto.randomUUID()}`,
      objective: input.objective,
      asset_ids: [input.line_id, input.batch_id],
      data_domains: ["quality", "process", "equipment", "material", "documents"],
    },
    budget: {
      max_tool_calls: 20,
      max_cost: 20,
      max_rows_scanned: 20_000,
      max_elapsed_ms: 120_000,
    },
  } })
}

export function getRun(access: RunAccess): Promise<ResearchRun> {
  return requestRun({ path: `runs/${encodeURIComponent(access.runId)}`, method: "get", tenantId: access.tenantId, body: null })
}

export function stepRun(access: RunAccess): Promise<ResearchRun> {
  return requestRun({ path: `runs/${encodeURIComponent(access.runId)}/step`, method: "post", tenantId: access.tenantId, body: null })
}

export function runUntilStop(access: RunAccess): Promise<ResearchRun> {
  return requestRun({ path: `runs/${encodeURIComponent(access.runId)}/run`, method: "post", tenantId: access.tenantId, body: null })
}

export function pauseRun(access: RunAccess): Promise<ResearchRun> {
  return requestRun({ path: `runs/${encodeURIComponent(access.runId)}/pause`, method: "post", tenantId: access.tenantId, body: null })
}

export function reviseRun(access: RunAccess, run: ResearchRun, objective: string): Promise<ResearchRun> {
  return requestRun({
    path: `runs/${encodeURIComponent(access.runId)}/revise`, method: "post",
    tenantId: access.tenantId, body: { scope: { ...run.scope, objective } },
  })
}

export function resumeRun(access: RunAccess): Promise<ResearchRun> {
  return requestRun({ path: `runs/${encodeURIComponent(access.runId)}/resume`, method: "post", tenantId: access.tenantId, body: null })
}

export function approveGate(decision: GateDecision): Promise<ResearchRun> {
  const { access } = decision
  return requestRun({
    path: `runs/${encodeURIComponent(access.runId)}/gates/${encodeURIComponent(decision.gateId)}/approve`,
    method: "post", tenantId: access.tenantId,
    body: { approver: decision.approver, reason: decision.reason },
  })
}

export function reviewNote(decision: NoteDecision): Promise<ResearchRun> {
  const { access } = decision
  return requestRun({
    path: `runs/${encodeURIComponent(access.runId)}/notes/review`, method: "post",
    tenantId: access.tenantId, body: { reviewer: decision.reviewer, reason: decision.reason },
  })
}

export async function getReport(access: RunAccess): Promise<string> {
  const response = await client.get(`runs/${encodeURIComponent(access.runId)}/report`, {
    headers: { "X-Tenant-ID": access.tenantId },
  })
  return (await parseResponse(response, ReportSchema)).markdown
}

export async function downloadExport(access: RunAccess): Promise<void> {
  const response = await client.post(`runs/${encodeURIComponent(access.runId)}/export`, {
    headers: { "X-Tenant-ID": access.tenantId },
  })
  const blob = await response.blob()
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement("a")
  anchor.href = url
  anchor.download = `quality-research-${access.runId}.json`
  anchor.click()
  URL.revokeObjectURL(url)
}
