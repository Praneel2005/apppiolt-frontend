/**
 * Thin API client — all backend calls go through here.
 * The Vite dev server proxies /api and /ws to localhost:8000.
 */

import type {
  Application,
  DatePresetsResponse,
  DateRange,
  FilterValue,
  SessionResponse,
  Sort,
  WidgetDataResponse,
} from "../lib/types";

const BASE = ""; // proxied by Vite

async function json<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(BASE + url, init);
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`${res.status} ${res.statusText}: ${body}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  /** Full application metadata (100 pages). */
  application(): Promise<Application> {
    return json<Application>("/api/application");
  },

  /** Date preset ranges resolved from the dataset's as_of_date. */
  datePresets(): Promise<DatePresetsResponse> {
    return json<DatePresetsResponse>("/api/date-presets");
  },

  /** Create a new session; get initial UI state. */
  createSession(): Promise<SessionResponse> {
    return json<SessionResponse>("/api/session", { method: "POST" });
  },

  /** Fetch data for a single widget with the given filters. */
  widgetData(opts: {
    widget_id: string;
    filters: Record<string, FilterValue>;
    date_range?: DateRange | null;
    sort?: Sort | null;
  }): Promise<WidgetDataResponse> {
    return json<WidgetDataResponse>("/api/widget-data", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(opts),
    });
  },

  /** Send a chat message to the scripted agent. */
  sendMessage(
    sessionId: string,
    text: string,
    confirmMode: boolean
  ): Promise<{ accepted: boolean }> {
    return json(`/api/session/${sessionId}/message`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, config: { confirm_mode: confirmMode } }),
    });
  },

  /** Approve or decline a confirm_request from the agent. */
  confirm(
    sessionId: string,
    actionId: string,
    approve: boolean
  ): Promise<{ ok: boolean }> {
    return json(`/api/session/${sessionId}/confirm`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action_id: actionId, approve }),
    });
  },

  /** Revert to the previous state. */
  undo(sessionId: string): Promise<unknown> {
    return json(`/api/session/${sessionId}/undo`, { method: "POST" });
  },

  /** Debug: force the server to push a specific state and return verify result. */
  debugApplyState(
    sessionId: string,
    state: unknown
  ): Promise<{ ack: unknown; verify: { ok: boolean; mismatches: unknown[] } }> {
    return json("/api/debug/apply_state", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, state }),
    });
  },

  /** Debug: what the server expected for the current state. */
  debugExpected(sessionId: string): Promise<unknown> {
    return json(`/api/debug/expected/${sessionId}`);
  },

  /** Debug: set a fault mode. */
  debugFault(kind: "none" | "drop_filter" | "empty_widget" | "slow_widget"): Promise<unknown> {
    return json("/api/debug/fault", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind }),
    });
  },
};
