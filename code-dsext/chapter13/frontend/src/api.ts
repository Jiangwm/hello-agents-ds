import type {
  ApprovalRequest,
  EnergyCurve,
  EnergyPlan,
  PlanChangeRequest,
  PlanningRequest
} from './types'

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000').replace(/\/$/, '')

export class ApiError extends Error {
  constructor(message: string, public readonly status?: number) {
    super(message)
    this.name = 'ApiError'
  }
}

async function requestJson<T>(path: string, init: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...init.headers }
  })
  const raw = await response.text()
  let payload: unknown = null
  if (raw) {
    try {
      payload = JSON.parse(raw) as unknown
    } catch {
      if (!response.ok) throw new ApiError(raw, response.status)
    }
  }
  if (!response.ok) {
    const detail = errorDetail(payload)
    throw new ApiError(detail ?? `请求失败（HTTP ${response.status}）`, response.status)
  }
  return payload as T
}

function errorDetail(value: unknown): string | undefined {
  if (!isRecord(value) || !('detail' in value)) return undefined
  const detail = value.detail
  if (typeof detail === 'string') return detail
  if (!Array.isArray(detail)) return undefined
  const messages = detail.map(validationMessage).filter((message): message is string => message !== undefined)
  return messages.length ? messages.join('；') : undefined
}

function validationMessage(value: unknown): string | undefined {
  if (!isRecord(value) || typeof value.msg !== 'string') return undefined
  const location = Array.isArray(value.loc)
    ? value.loc.filter((part): part is string | number => typeof part === 'string' || typeof part === 'number').join('.')
    : ''
  return location ? `${location}: ${value.msg}` : value.msg
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

export function createPlan(request: PlanningRequest): Promise<EnergyPlan> {
  return requestJson<EnergyPlan>('/api/plans', {
    method: 'POST',
    body: JSON.stringify(request)
  })
}

export function getPlanCurve(
  planId: string,
  optionId?: string
): Promise<EnergyCurve> {
  const query = optionId ? `?option_id=${encodeURIComponent(optionId)}` : ''
  return requestJson<EnergyCurve>(
    `/api/plans/${encodeURIComponent(planId)}/curve${query}`,
    { method: 'GET' }
  )
}

export function patchPlan(planId: string, change: PlanChangeRequest): Promise<EnergyPlan> {
  return requestJson<EnergyPlan>(`/api/plans/${encodeURIComponent(planId)}`, {
    method: 'PATCH',
    body: JSON.stringify(change)
  })
}

export function approvePlan(planId: string, approval: ApprovalRequest): Promise<EnergyPlan> {
  return requestJson<EnergyPlan>(`/api/plans/${encodeURIComponent(planId)}/approve`, {
    method: 'POST',
    body: JSON.stringify(approval)
  })
}

export async function downloadPlanExport(planId: string): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/api/plans/${encodeURIComponent(planId)}/export`)
  if (!response.ok) throw new ApiError(`导出失败（HTTP ${response.status}）`, response.status)
  const blob = await response.blob()
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `energy-plan-${planId}.json`
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  URL.revokeObjectURL(url)
}
