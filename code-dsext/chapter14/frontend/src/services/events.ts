import ky from "ky"

import { ApiEventSchema, type ApiEvent } from "../domain/schemas"

const API_BASE = (import.meta.env["VITE_API_BASE_URL"] ?? "http://127.0.0.1:8014").replace(/\/$/, "")

type EventHandlers = {
  readonly onEvent: (event: ApiEvent) => void
  readonly onError: (message: string) => void
}

export type EventStreamRequest = {
  readonly runId: string
  readonly tenantId: string
  readonly lastEventId: string | null
  readonly signal: AbortSignal
  readonly handlers: EventHandlers
}

export async function streamEvents(request: EventStreamRequest): Promise<void> {
  try {
    const url = `${API_BASE}/runs/${encodeURIComponent(request.runId)}/events`
    const shared = {
      signal: request.signal,
      timeout: false,
      retry: { limit: 0 },
      headers: { "X-Tenant-ID": request.tenantId },
    } as const
    const response = request.lastEventId === null
      ? await ky.get(url, shared)
      : await ky.get(url, { ...shared, headers: { ...shared.headers, "Last-Event-ID": request.lastEventId } })
    const body = response.body
    if (body === null) {
      request.handlers.onError("SSE 响应没有可读数据流")
      return
    }
    await readFrames(body, request.handlers, request.signal)
  } catch (error) {
    if (request.signal.aborted) return
    if (error instanceof Error) {
      request.handlers.onError(error.message)
      return
    }
    throw error
  }
}

async function readFrames(
  body: ReadableStream<Uint8Array>,
  handlers: EventHandlers,
  signal: AbortSignal,
): Promise<void> {
  const reader = body.getReader()
  const decoder = new TextDecoder()
  let pending = ""
  while (!signal.aborted) {
    const chunk = await reader.read()
    if (chunk.done) break
    pending += decoder.decode(chunk.value, { stream: true }).replace(/\r\n/g, "\n")
    const frames = pending.split("\n\n")
    pending = frames.pop() ?? ""
    for (const frame of frames) parseSseFrame(frame, handlers)
  }
}

export function parseSseFrame(frame: string, handlers: EventHandlers): void {
  const lines = frame.split("\n")
  let id = ""
  let eventType = "message"
  const data: string[] = []
  for (const line of lines) {
    if (line.startsWith("id:")) id = line.slice(3).trim()
    else if (line.startsWith("event:")) eventType = line.slice(6).trim()
    else if (line.startsWith("data:")) data.push(line.slice(5).trimStart())
  }
  if (data.length === 0) return
  try {
    const payload: unknown = JSON.parse(data.join("\n"))
    const direct = ApiEventSchema.safeParse(payload)
    if (direct.success) {
      handlers.onEvent(direct.data)
      return
    }
    const fallback = ApiEventSchema.safeParse({
      event_id: id,
      event_type: eventType,
      occurred_at: new Date().toISOString(),
      payload,
    })
    if (fallback.success) handlers.onEvent(fallback.data)
    else handlers.onError("忽略了不符合事件契约的 SSE 数据")
  } catch (error) {
    if (error instanceof SyntaxError) {
      handlers.onError("忽略了无法解析的 SSE 数据")
      return
    }
    throw error
  }
}
