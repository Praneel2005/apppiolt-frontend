/**
 * Central Zustand store.
 *
 * Exports (shared contract with Shreeniketh — do NOT rename):
 *   useUiState()     → current UiState + mutators
 *   useSession()     → session_id, wsSend fn
 *   useAgentEvents() → live agent event stream
 *   lastAck          → last RenderAck the browser sent (read via useAppStore)
 */

import { create } from "zustand";
import { subscribeWithSelector } from "zustand/middleware";
import type {
  AgentEvent,
  Application,
  DatePresetsResponse,
  RenderAckMsg,
  UiState,
  WidgetAck,
} from "../lib/types";

// ─── Pending ack registry ────────────────────────────────────────────────────
// Widgets call resolveWidgetAck() when their data arrives.
// The WS layer calls registerPendingWidget() during apply_state handling.

const pendingResolvers = new Map<string, (ack: WidgetAck) => void>();
const completedAcks = new Map<string, WidgetAck>();

export function registerPendingWidget(
  widgetId: string,
  resolve: (ack: WidgetAck) => void
) {
  const existing = completedAcks.get(widgetId);
  if (existing) {
    resolve(existing);
  } else {
    pendingResolvers.set(widgetId, resolve);
  }
}

export function resolveWidgetAck(widgetId: string, ack: WidgetAck) {
  completedAcks.set(widgetId, ack);
  const resolver = pendingResolvers.get(widgetId);
  if (resolver) {
    pendingResolvers.delete(widgetId);
    resolver(ack);
  }
}

export function clearPendingWidgetAcks() {
  pendingResolvers.clear();
  completedAcks.clear();
}

// ─── Store shape ─────────────────────────────────────────────────────────────

interface AppStore {
  // Application metadata
  application: Application | null;
  datePresets: DatePresetsResponse;
  setApplication: (app: Application) => void;
  setDatePresets: (p: DatePresetsResponse) => void;

  // Session
  sessionId: string | null;
  wsSend: ((msg: unknown) => void) | null;
  setSession: (sessionId: string, wsSend: (msg: unknown) => void) => void;

  // UI state (the backend is authoritative)
  uiState: UiState | null;
  setUiState: (state: UiState) => void;

  // Last render ack sent by the browser
  lastAck: RenderAckMsg | null;
  setLastAck: (ack: RenderAckMsg) => void;

  // Agent events
  agentEvents: AgentEvent[];
  appendAgentEvent: (event: AgentEvent) => void;
  clearAgentEvents: () => void;

  // Last verify result (from agent "verify" event or debug)
  lastVerify: { ok: boolean; mismatches: unknown[] } | null;
  setLastVerify: (v: { ok: boolean; mismatches: unknown[] }) => void;
}

export const useAppStore = create<AppStore>()(
  subscribeWithSelector((set) => ({
    application: null,
    datePresets: {},
    setApplication: (app) => set({ application: app }),
    setDatePresets: (p) => set({ datePresets: p }),

    sessionId: null,
    wsSend: null,
    setSession: (sessionId, wsSend) => set({ sessionId, wsSend }),

    uiState: null,
    setUiState: (uiState) => set({ uiState }),

    lastAck: null,
    setLastAck: (lastAck) => set({ lastAck }),

    agentEvents: [],
    appendAgentEvent: (event) =>
      set((s) => ({ agentEvents: [...s.agentEvents, event] })),
    clearAgentEvents: () => set({ agentEvents: [] }),

    lastVerify: null,
    setLastVerify: (v) => set({ lastVerify: v }),
  }))
);

if (typeof window !== "undefined") {
  (window as unknown as { __APP_STORE__: typeof useAppStore }).__APP_STORE__ = useAppStore;
}

// ─── Named hook exports (shared contract names) ───────────────────────────────

/** Current UI state and the action to update it from user interaction. */
export function useUiState() {
  const uiState = useAppStore((s) => s.uiState);
  const wsSend = useAppStore((s) => s.wsSend);
  const sessionId = useAppStore((s) => s.sessionId);
  const setUiState = useAppStore((s) => s.setUiState);

  function userStateChange(newState: UiState) {
    // Optimistic local update
    setUiState(newState);
    // Notify the backend
    if (wsSend) {
      wsSend({ type: "user_state_change", state: newState });
    }
  }

  return { uiState, userStateChange, sessionId };
}

/** Session id and wsSend function. */
export function useSession() {
  const sessionId = useAppStore((s) => s.sessionId);
  const wsSend = useAppStore((s) => s.wsSend);
  return { sessionId, wsSend };
}

/** Live agent event stream from the WebSocket. */
export function useAgentEvents() {
  const agentEvents = useAppStore((s) => s.agentEvents);
  const clearAgentEvents = useAppStore((s) => s.clearAgentEvents);
  return { agentEvents, clearAgentEvents };
}
