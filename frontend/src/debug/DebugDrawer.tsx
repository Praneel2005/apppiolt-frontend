/**
 * DebugDrawer — inspect live UI state, cryptographic hash verification, and fault injection.
 *
 * Implements the Shreeniketh spec (docs/SHREENIKETH.md §4).
 * Slides up from the bottom when [Debug] is clicked in the TopBar.
 */

import { useState } from "react";
import { api } from "../net/api";
import { useAppStore } from "../state/store";

interface DebugDrawerProps {
  isOpen: boolean;
  onClose: () => void;
}

export function DebugDrawer({ isOpen, onClose }: DebugDrawerProps) {
  const uiState = useAppStore((s) => s.uiState);
  const lastAck = useAppStore((s) => s.lastAck);
  const lastVerify = useAppStore((s) => s.lastVerify);
  const sessionId = useAppStore((s) => s.sessionId);

  const [activeTab, setActiveTab] = useState<"state" | "ack" | "faults">("state");
  const [activeFault, setActiveFault] = useState<string>("none");
  const [faultLoading, setFaultLoading] = useState(false);

  if (!isOpen) return null;

  async function handleSetFault(
    kind: "none" | "drop_filter" | "empty_widget" | "slow_widget"
  ) {
    setFaultLoading(true);
    try {
      await api.debugFault(kind);
      setActiveFault(kind);
    } catch (err) {
      console.error("Failed to set fault", err);
    } finally {
      setFaultLoading(false);
    }
  }

  return (
    <div className="fixed inset-x-0 bottom-0 z-50 bg-white border-t border-slate-200 shadow-2xl transition-all duration-300 max-h-[460px] flex flex-col font-[Inter,sans-serif]">
      {/* Header */}
      <div className="h-12 px-6 border-b border-slate-200 flex items-center justify-between bg-slate-50 select-none">
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-2">
            <span className="text-sm font-bold text-slate-800">🛠️ Debug & Verifier Inspector</span>
            <span className="text-xs px-2 py-0.5 rounded-full bg-slate-200 text-slate-700 font-mono">
              Session: {sessionId ?? "none"}
            </span>
          </div>

          {/* Navigation tabs */}
          <div className="flex items-center gap-1">
            <button
              onClick={() => setActiveTab("state")}
              className={`px-3 py-1 text-xs font-medium rounded-lg transition-colors ${
                activeTab === "state"
                  ? "bg-white text-indigo-600 shadow-xs"
                  : "text-slate-500 hover:text-slate-900"
              }`}
            >
              Live UI State
            </button>
            <button
              onClick={() => setActiveTab("ack")}
              className={`px-3 py-1 text-xs font-medium rounded-lg transition-colors ${
                activeTab === "ack"
                  ? "bg-white text-indigo-600 shadow-xs"
                  : "text-slate-500 hover:text-slate-900"
              }`}
            >
              Render Ack & Hash
            </button>
            <button
              onClick={() => setActiveTab("faults")}
              className={`px-3 py-1 text-xs font-medium rounded-lg transition-colors ${
                activeTab === "faults"
                  ? "bg-white text-indigo-600 shadow-xs"
                  : "text-slate-500 hover:text-slate-900"
              }`}
            >
              Fault Injection Test
            </button>
          </div>
        </div>

        <button
          onClick={onClose}
          className="p-1 text-slate-400 hover:text-slate-700 rounded-md transition-colors"
        >
          ✕
        </button>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-4 bg-slate-900 text-slate-100 font-mono text-xs">
        {activeTab === "state" && (
          <div>
            <div className="text-[11px] text-slate-400 mb-2">
              // Current UiState synchronized with Zustand store and URL:
            </div>
            <pre className="overflow-x-auto text-emerald-400">
              {JSON.stringify(uiState, null, 2)}
            </pre>
          </div>
        )}

        {activeTab === "ack" && (
          <div className="space-y-4">
            <div>
              <div className="text-[11px] text-slate-400 mb-1">
                // Last Cryptographic Verification Result:
              </div>
              {lastVerify ? (
                <div
                  className={`p-2.5 rounded-lg border text-xs font-sans ${
                    lastVerify.ok
                      ? "bg-emerald-950/60 border-emerald-500/30 text-emerald-300"
                      : "bg-red-950/60 border-red-500/30 text-red-300"
                  }`}
                >
                  <div className="font-bold">
                    {lastVerify.ok ? "✓ Verified OK" : "⚠ Verification Mismatch"}
                  </div>
                  {lastVerify.mismatches && lastVerify.mismatches.length > 0 && (
                    <pre className="mt-2 text-[11px] text-red-200 overflow-x-auto">
                      {JSON.stringify(lastVerify.mismatches, null, 2)}
                    </pre>
                  )}
                </div>
              ) : (
                <div className="text-slate-500 italic text-xs">
                  No verification run yet. Send a prompt to the assistant to trigger a verification pass.
                </div>
              )}
            </div>

            <div>
              <div className="text-[11px] text-slate-400 mb-1">
                // Last RenderAck Message sent by browser via WebSocket:
              </div>
              <pre className="overflow-x-auto text-cyan-400">
                {JSON.stringify(lastAck, null, 2)}
              </pre>
            </div>
          </div>
        )}

        {activeTab === "faults" && (
          <div className="font-sans space-y-4 text-slate-200">
            <div>
              <h4 className="text-sm font-semibold text-white">
                Fault Injection Playground
              </h4>
              <p className="text-xs text-slate-400 mt-1 leading-relaxed">
                Demonstrates how plain code verifies the AI agent's actions. Injecting a fault causes the server to alter queries, making the browser's cryptographic series hash differ from expected data.
              </p>
            </div>

            <div className="flex flex-wrap gap-2.5">
              {[
                {
                  id: "none",
                  label: "Normal (No Fault)",
                  desc: "System functions normally; hashes match perfectly.",
                },
                {
                  id: "drop_filter",
                  label: "Drop Filter",
                  desc: "Drops the first filter on queries, causing hash mismatch.",
                },
                {
                  id: "empty_widget",
                  label: "Empty Widget",
                  desc: "Simulates missing data returning 0 rows.",
                },
                {
                  id: "slow_widget",
                  label: "Slow Widget",
                  desc: "Delays widget data by 8s to trigger ack timeout.",
                },
              ].map((f) => (
                <button
                  key={f.id}
                  disabled={faultLoading}
                  onClick={() =>
                    handleSetFault(
                      f.id as "none" | "drop_filter" | "empty_widget" | "slow_widget"
                    )
                  }
                  className={`text-left p-3 rounded-xl border transition-all duration-150 max-w-[220px] ${
                    activeFault === f.id
                      ? "bg-indigo-950 border-indigo-500 ring-2 ring-indigo-500/40 text-white"
                      : "bg-slate-800/80 border-slate-700 hover:border-slate-500 text-slate-300"
                  }`}
                >
                  <div className="text-xs font-semibold flex items-center justify-between">
                    <span>{f.label}</span>
                    {activeFault === f.id && (
                      <span className="w-2 h-2 rounded-full bg-emerald-400" />
                    )}
                  </div>
                  <div className="text-[11px] text-slate-400 mt-1">
                    {f.desc}
                  </div>
                </button>
              ))}
            </div>

            <div className="p-3 bg-slate-800/60 rounded-xl border border-slate-700 text-xs text-slate-300">
              Active fault:{" "}
              <strong className="text-white font-mono">{activeFault}</strong>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
