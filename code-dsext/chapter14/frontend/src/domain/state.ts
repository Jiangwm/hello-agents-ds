import type { ApiEvent, ResearchRun } from "./schemas"

export type ScreenState =
  | { readonly kind: "empty" }
  | { readonly kind: "loading"; readonly message: string }
  | { readonly kind: "ready"; readonly run: ResearchRun }
  | { readonly kind: "error"; readonly message: string }

export type AppState = {
  readonly screen: ScreenState
  readonly events: readonly ApiEvent[]
  readonly lastEventId: string | null
  readonly actionPending: boolean
}

export type AppAction =
  | { readonly kind: "load"; readonly message: string }
  | { readonly kind: "loaded"; readonly run: ResearchRun }
  | { readonly kind: "failed"; readonly message: string }
  | { readonly kind: "event"; readonly event: ApiEvent }
  | { readonly kind: "action_started" }
  | { readonly kind: "action_finished" }
  | { readonly kind: "reset" }

export const initialState: AppState = {
  screen: { kind: "empty" },
  events: [],
  lastEventId: null,
  actionPending: false,
}

export function reduceAppState(state: AppState, action: AppAction): AppState {
  switch (action.kind) {
    case "load":
      return { ...state, screen: { kind: "loading", message: action.message } }
    case "loaded":
      return { ...state, screen: { kind: "ready", run: action.run } }
    case "failed":
      return { ...state, screen: { kind: "error", message: action.message }, actionPending: false }
    case "event":
      if (state.events.some((item) => item.event_id === action.event.event_id)) return state
      return {
        ...state,
        events: [...state.events, action.event],
        lastEventId: action.event.event_id,
      }
    case "action_started":
      return { ...state, actionPending: true }
    case "action_finished":
      return { ...state, actionPending: false }
    case "reset":
      return initialState
    default:
      return assertNever(action)
  }
}

function assertNever(value: never): never {
  throw new TypeError(`未处理的状态操作: ${JSON.stringify(value)}`)
}
