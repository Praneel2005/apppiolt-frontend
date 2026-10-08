/**
 * WebSocket manager.
 *
 * Responsibilities:
 *  1. Open WS /ws/{session_id}
 *  2. Handle apply_state → update store + URL, wait for all widget acks, send render_ack
 *  3. Handle agent_event → push to store slice
 *  4. Re-expose wsSend so the store and chat panel can send user_state_change / etc.
 */

import type {
  ApplyStateMsg,
  RenderAckMsg,
  WidgetAck,
} from "../lib/types";
import {
  clearPendingWidgetAcks,
  registerPendingWidget,
  useAppStore,
} from "../state/store";

const ACK_TIMEOUT_MS = 5000;

let socket: WebSocket | null = null;

function send(msg: unknown) {
  if (socket && socket.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify(msg));
  }
}

async function collectWidgetAcks(widgetIds: string[]): Promise<WidgetAck[]> {
  const promises: Promise<WidgetAck>[] = widgetIds.map(
    (id) =>
      new Promise<WidgetAck>((resolve, reject) => {
        registerPendingWidget(id, resolve, reject);
      })
  );

  return Promise.all(
    promises.map((p, i) =>
      Promise.race([
        p,
        new Promise<WidgetAck>((resolve) =>
          setTimeout(() => {
            resolve({
              widget_id: widgetIds[i],
              applied_filters: {},
              row_count: 0,
              series_hash: "",
              render_ms: ACK_TIMEOUT_MS,
              error: "timeout",
            });
          }, ACK_TIMEOUT_MS)
        ),
      ])
    )
  );
}

async function handleApplyState(msg: ApplyStateMsg) {
  const { setUiState, setLastAck, application } = useAppStore.getState();

  clearPendingWidgetAcks();
  setUiState(msg.state);

  const page = application?.pages.find((p) => p.page_id === msg.state.page_id);
  const widgetIds = page?.widgets ?? [];

  const widgetAcks = await collectWidgetAcks(widgetIds);

  const ack: RenderAckMsg = {
    type: "render_ack",
    version: msg.version,
    nonce: msg.nonce,
    route: msg.state.route,
    widgets: widgetAcks,
  };

  setLastAck(ack);
  send(ack);
}

export function openWebSocket(sessionId: string) {
  const { setSession, appendAgentEvent, setLastVerify } =
    useAppStore.getState();

  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  const url = `${proto}//${window.location.host}/ws/${sessionId}`;

  socket = new WebSocket(url);

  socket.onopen = () => {
    setSession(sessionId, send);
    console.log("[WS] connected");
  };

  socket.onmessage = (ev) => {
    let msg: Record<string, unknown>;
    try {
      msg = JSON.parse(ev.data as string) as Record<string, unknown>;
    } catch {
      return;
    }

    if (msg.type === "apply_state") {
      void handleApplyState(msg as unknown as ApplyStateMsg);
    } else if (msg.type === "agent_event") {
      const event = (msg.event as { seq: number; type: string; data: Record<string, unknown> });
      appendAgentEvent(event);
      if (event.type === "verify") {
        setLastVerify(
          event.data as { ok: boolean; mismatches: unknown[] }
        );
      }
    }
  };

  socket.onclose = () => {
    console.log("[WS] disconnected");
    socket = null;
  };

  socket.onerror = (e) => {
    console.error("[WS] error", e);
  };
}
