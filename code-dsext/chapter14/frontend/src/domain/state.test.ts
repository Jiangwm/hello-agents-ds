import { describe, expect, it } from "vitest"

import type { ApiEvent } from "./schemas"
import { initialState, reduceAppState } from "./state"

const event: ApiEvent = {
  event_id: "2",
  event_type: "todo.updated",
  occurred_at: "2026-07-15T08:00:00+00:00",
  payload: { todo_id: "todo-2" },
}

describe("reduceAppState", () => {
  it("忽略重放的重复 SSE 事件", () => {
    const once = reduceAppState(initialState, { kind: "event", event })
    const twice = reduceAppState(once, { kind: "event", event })
    expect(twice.events).toHaveLength(1)
    expect(twice.lastEventId).toBe("2")
  })

  it("重置时清理重连游标", () => {
    const once = reduceAppState(initialState, { kind: "event", event })
    const reset = reduceAppState(once, { kind: "reset" })
    expect(reset.lastEventId).toBeNull()
    expect(reset.events).toHaveLength(0)
  })
})
