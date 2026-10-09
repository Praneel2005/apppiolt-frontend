/**
 * Central store. The BACKEND is authoritative for UI state: the browser applies what the server sends (apply_state),
 * reports what it actually rendered (render_ack), and tells the server when the user changed something
 * (user_state_change). Local state changes are applied optimistically.
 */
import { create } from 'zustand'
import type {
  AgentEvent, Application, CanvasSpec, CatalogEntry, Page, UiState, WidgetAck,
} from '../lib/types'
import type { PresetMap } from '../lib/url'

export interface Toast { id: number; kind: 'success' | 'error' | 'info'; text: string; undo?: () => void }
export interface AckRecord { key: string; ack: WidgetAck }

interface Store {
  boot: 'loading' | 'ready' | 'error'
  bootError: string | null
  app: Application | null
  presets: PresetMap
  catalog: Record<string, CatalogEntry>

  sessionId: string | null
  link: 'connecting' | 'open' | 'closed'
  state: UiState | null
  acks: Record<string, AckRecord>
  refreshTick: number
  lastVerify: { ok: boolean; mismatches: any[]; source?: string } | null
  apiCalls: number

  events: AgentEvent[]
  canvases: CanvasSpec[]
  toasts: Toast[]

  set: (patch: Partial<Store>) => void
}

export const useStore = create<Store>()((set) => ({
  boot: 'loading', bootError: null, app: null, presets: {}, catalog: {},
  sessionId: null, link: 'connecting', state: null, acks: {}, refreshTick: 0, lastVerify: null, apiCalls: 0,
  events: [], canvases: [], toasts: [],
  set: (patch) => set(patch),
}))

if (typeof window !== 'undefined') (window as any).__STORE__ = useStore

export const getStore = () => useStore.getState()

export function currentPage(): Page | undefined {
  const { app, state } = useStore.getState()
  return app?.pages.find((p) => p.page_id === state?.page_id)
}

export function reportAck(widgetId: string, key: string, ack: WidgetAck) {
  useStore.setState((s) => ({ acks: { ...s.acks, [widgetId]: { key, ack } } }))
}

export function refreshData() {
  useStore.setState((s) => ({ refreshTick: s.refreshTick + 1 }))
}

let toastId = 0
export function toast(kind: Toast['kind'], text: string, undo?: () => void, ms = 6000) {
  const id = ++toastId
  useStore.setState((s) => ({ toasts: [...s.toasts, { id, kind, text, undo }] }))
  setTimeout(() => dismissToast(id), ms)
}
export function dismissToast(id: number) {
  useStore.setState((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) }))
}
