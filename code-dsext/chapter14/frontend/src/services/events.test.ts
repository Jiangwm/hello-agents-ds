import { describe, expect, it } from "vitest"

import type { ApiEvent } from "../domain/schemas"
import { parseSseFrame } from "./events"

describe("parseSseFrame", () => {
  it("将后端裸对象 data 与 SSE id/event 合成为类型化事件", () => {
    const received: ApiEvent[] = []
    const errors: string[] = []

    parseSseFrame(
      "id: 3\nevent: status\ndata: {\"message\":\"正在执行\",\"count\":1}\n",
      {
        onEvent: (event) => received.push(event),
        onError: (message) => errors.push(message),
      },
    )

    expect(errors).toEqual([])
    expect(received).toHaveLength(1)
    expect(received[0]?.event_id).toBe("3")
    expect(received[0]?.event_type).toBe("status")
    expect(received[0]?.payload).toEqual({ message: "正在执行", count: 1 })
  })
})
