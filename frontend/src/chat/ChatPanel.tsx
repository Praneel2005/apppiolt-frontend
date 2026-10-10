/**
 * ChatPanel — AI Assistant interface for AppPilot.
 *
 * Implements the Shreeniketh spec (docs/SHREENIKETH.md §4 & §5):
 *   - Live event stream timeline (understanding, candidates, plan, validation, action, verify, evidence, answer, done)
 *   - Interactive citation chips [e1] that scroll to and highlight widgets via highlightWidget(source)
 *   - Confirm mode toggle & confirm_request approval / decline buttons
 *   - Undo button (POST /api/session/{sid}/undo)
 *   - Collapsible panel (400px down to 56px icon rail)
 *   - Empty-state quick prompts
 */

import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import type { AgentEvent } from "../lib/types";
import { api } from "../net/api";
import { useAppStore } from "../state/store";
import { highlightWidget } from "../widgets/widgetBase";

const SUGGESTED_PROMPTS = [
  {
    icon: "📊",
    title: "Revenue by state",
    prompt: "Show revenue by customer state last quarter",
  },
  {
    icon: "🏙️",
    title: "Compare São Paulo orders",
    prompt: "Compare orders in São Paulo last month",
  },
  {
    icon: "📈",
    title: "November 2017 change",
    prompt: "Why did revenue change in November 2017?",
  },
  {
    icon: "🚚",
    title: "Late delivery trend",
    prompt: "Open the late delivery trend",
  },
  {
    icon: "🛡️",
    title: "Safety check (refusal)",
    prompt: "Delete all March orders",
  },
];

interface ChatMessage {
  id: string;
  userText: string;
  timestamp: string;
  events: AgentEvent[];
  status: "idle" | "running" | "confirming" | "done" | "error";
  confirmRequest?: { actionId: string; summary: string };
  answer?: { text: string; citations: string[]; deep_links: string[] };
  verify?: { ok: boolean; mismatches: unknown[] };
  error?: string;
  executionMs?: number;
}

export function ChatPanel() {
  const sessionId = useAppStore((s) => s.sessionId);
  const agentEvents = useAppStore((s) => s.agentEvents);
  const navigate = useNavigate();

  const [isCollapsed, setIsCollapsed] = useState(false);
  const [confirmMode, setConfirmMode] = useState(false);
  const [inputText, setInputText] = useState("");
  const [isRunning, setIsRunning] = useState(false);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [expandedSteps, setExpandedSteps] = useState<Record<string, boolean>>({});
  const [toastMessage, setToastMessage] = useState<string | null>(null);

  const scrollRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const processedEventSeqRef = useRef<number>(0);

  // Auto-scroll chat to bottom
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages, agentEvents, isRunning]);

  // Handle incoming agent events from WebSocket
  useEffect(() => {
    if (agentEvents.length === 0) return;

    const newEvents = agentEvents.filter(
      (ev) => ev.seq > processedEventSeqRef.current
    );
    if (newEvents.length === 0) return;

    processedEventSeqRef.current = Math.max(
      ...agentEvents.map((ev) => ev.seq)
    );

    setMessages((prev) => {
      if (prev.length === 0) return prev;
      const lastIdx = prev.length - 1;
      const current = { ...prev[lastIdx] };
      const updatedEvents = [...current.events, ...newEvents];
      current.events = updatedEvents;

      for (const ev of newEvents) {
        if (ev.type === "confirm_request") {
          current.status = "confirming";
          current.confirmRequest = {
            actionId: (ev.data as { action_id: string }).action_id,
            summary: (ev.data as { summary: string }).summary,
          };
        } else if (ev.type === "verify") {
          current.verify = ev.data as { ok: boolean; mismatches: unknown[] };
        } else if (ev.type === "answer") {
          current.answer = ev.data as {
            text: string;
            citations: string[];
            deep_links: string[];
          };
        } else if (ev.type === "error") {
          current.status = "error";
          current.error = (ev.data as { message: string }).message;
        } else if (ev.type === "done") {
          current.status = "done";
          current.executionMs = (ev.data as { elapsed_ms: number }).elapsed_ms;
          setIsRunning(false);
        }
      }

      const nextMessages = [...prev];
      nextMessages[lastIdx] = current;
      return nextMessages;
    });
  }, [agentEvents]);

  // Toast notification helper
  function showToast(msg: string) {
    setToastMessage(msg);
    setTimeout(() => setToastMessage(null), 3000);
  }

  // Send message
  async function handleSend(textToSend?: string) {
    const text = (textToSend ?? inputText).trim();
    if (!text || !sessionId || isRunning) return;

    const newMsg: ChatMessage = {
      id: "msg-" + Date.now(),
      userText: text,
      timestamp: new Date().toLocaleTimeString([], {
        hour: "2-digit",
        minute: "2-digit",
      }),
      events: [],
      status: "running",
    };

    setMessages((prev) => [...prev, newMsg]);
    setInputText("");
    setIsRunning(true);
    setExpandedSteps((prev) => ({ ...prev, [newMsg.id]: true }));

    try {
      await api.sendMessage(sessionId, text, confirmMode);
    } catch (err) {
      console.error("Failed to send message", err);
      setMessages((prev) => {
        const next = [...prev];
        const last = next[next.length - 1];
        if (last) {
          last.status = "error";
          last.error = String((err as Error).message || err);
        }
        return next;
      });
      setIsRunning(false);
    }
  }

  // Confirm or decline
  async function handleConfirm(actionId: string, approve: boolean) {
    if (!sessionId) return;
    try {
      await api.confirm(sessionId, actionId, approve);
      setMessages((prev) => {
        const next = [...prev];
        const last = next[next.length - 1];
        if (last && last.confirmRequest) {
          last.status = "running";
          last.confirmRequest = undefined;
        }
        return next;
      });
      showToast(approve ? "Action approved" : "Action declined");
    } catch (err) {
      console.error("Failed to confirm action", err);
    }
  }

  // Undo last action
  async function handleUndo() {
    if (!sessionId) return;
    try {
      await api.undo(sessionId);
      showToast("Reverted to the previous view");
    } catch (err) {
      console.error("Failed to undo", err);
    }
  }

  // Citation chip click -> highlightWidget
  function handleCitationClick(citationId: string, msg: ChatMessage) {
    // Find evidence event corresponding to this citation
    const evidenceEv = msg.events.find(
      (e) => e.type === "evidence" && (e.data as { evidence_id: string }).evidence_id === citationId
    );
    const sourceWidgetId = (evidenceEv?.data as { source: string })?.source;
    if (sourceWidgetId) {
      highlightWidget(sourceWidgetId);
    } else {
      // Fallback: match any widget id
      highlightWidget(citationId);
    }
  }

  // Render text with clickable citation badges
  function renderAnswerText(text: string, msg: ChatMessage) {
    const parts = text.split(/(\[e\d+\])/g);
    return (
      <span>
        {parts.map((part, i) => {
          const match = part.match(/\[(e\d+)\]/);
          if (match) {
            const citeId = match[1];
            return (
              <button
                key={i}
                onClick={() => handleCitationClick(citeId, msg)}
                title="Click to locate on dashboard"
                className="inline-flex items-center mx-1 px-1.5 py-0.5 text-xs font-semibold text-indigo-700 bg-indigo-50 hover:bg-indigo-100 hover:text-indigo-900 border border-indigo-200 rounded-md transition-colors cursor-pointer"
              >
                [{citeId}]
              </button>
            );
          }
          return <span key={i}>{part}</span>;
        })}
      </span>
    );
  }

  if (isCollapsed) {
    return (
      <aside className="w-14 min-w-[56px] border-l border-slate-200 bg-white flex flex-col items-center py-4 gap-4 transition-all duration-300">
        <button
          onClick={() => setIsCollapsed(false)}
          className="p-2 text-slate-500 hover:text-indigo-600 hover:bg-indigo-50 rounded-lg transition-colors"
          title="Expand AI Assistant"
        >
          <span className="text-xl">🤖</span>
        </button>
        {isRunning && (
          <div className="w-2.5 h-2.5 rounded-full bg-indigo-600 animate-ping" />
        )}
        <div
          onClick={() => setIsCollapsed(false)}
          className="writing-mode-vertical cursor-pointer text-xs font-semibold text-slate-400 hover:text-slate-700 tracking-wider uppercase mt-4 select-none"
          style={{ writingMode: "vertical-rl" }}
        >
          AppPilot Assistant
        </div>
      </aside>
    );
  }

  return (
    <aside className="w-[400px] min-w-[400px] border-l border-slate-200 bg-white flex flex-col h-[calc(100vh-56px)] shadow-[-2px_0_8px_rgba(15,23,42,0.03)] z-10 transition-all duration-300">
      {/* Toast popup */}
      {toastMessage && (
        <div className="absolute top-16 right-6 bg-slate-900 text-white text-xs px-3 py-2 rounded-lg shadow-xl z-50 animate-fade-in flex items-center gap-2">
          <span>✓</span>
          <span>{toastMessage}</span>
        </div>
      )}

      {/* Header */}
      <div className="h-14 px-4 border-b border-slate-200 flex items-center justify-between bg-white select-none">
        <div className="flex items-center gap-2.5">
          <div className="relative flex items-center justify-center w-7 h-7 rounded-lg bg-indigo-600 text-white text-sm shadow-sm">
            ✨
            <span className="absolute -bottom-0.5 -right-0.5 w-2.5 h-2.5 bg-emerald-500 border-2 border-white rounded-full" />
          </div>
          <div>
            <h2 className="text-sm font-semibold text-slate-900 leading-none">
              AppPilot Assistant
            </h2>
            <p className="text-[11px] text-slate-400 mt-0.5">
              Autonomous BI Agent
            </p>
          </div>
        </div>

        <div className="flex items-center gap-1.5">
          {/* Confirm mode toggle */}
          <button
            onClick={() => setConfirmMode((c) => !c)}
            title={confirmMode ? "Confirm mode is ON" : "Confirm mode is OFF"}
            className={`text-xs px-2 py-1 rounded-md border flex items-center gap-1 transition-colors ${
              confirmMode
                ? "bg-amber-50 text-amber-700 border-amber-300 font-medium"
                : "bg-slate-50 text-slate-500 border-slate-200 hover:bg-slate-100"
            }`}
          >
            <span>{confirmMode ? "🛡️ Confirm" : "⚡ Direct"}</span>
          </button>

          {/* Undo button */}
          <button
            onClick={handleUndo}
            title="Revert to previous view"
            className="p-1.5 text-slate-400 hover:text-slate-700 hover:bg-slate-100 rounded-md transition-colors"
          >
            ↩
          </button>

          {/* Collapse button */}
          <button
            onClick={() => setIsCollapsed(true)}
            title="Collapse assistant rail"
            className="p-1.5 text-slate-400 hover:text-slate-700 hover:bg-slate-100 rounded-md transition-colors"
          >
            ✕
          </button>
        </div>
      </div>

      {/* Messages area */}
      <div
        ref={scrollRef}
        className="flex-1 overflow-y-auto p-4 space-y-4 bg-slate-50/50"
      >
        {messages.length === 0 ? (
          /* Empty state */
          <div className="flex flex-col items-center justify-center py-6 text-center">
            <div className="w-12 h-12 rounded-2xl bg-indigo-50 border border-indigo-100 flex items-center justify-center text-2xl mb-3 shadow-inner">
              🤖
            </div>
            <h3 className="text-sm font-semibold text-slate-800">
              Welcome to AppPilot
            </h3>
            <p className="text-xs text-slate-500 max-w-[260px] mt-1 leading-relaxed">
              Ask anything about your Olist marketplace data. I'll navigate pages, set filters, and verify results.
            </p>

            <div className="w-full mt-6 space-y-2 text-left">
              <span className="text-[11px] font-semibold text-slate-400 uppercase tracking-wider px-1">
                Suggested Prompts
              </span>
              {SUGGESTED_PROMPTS.map((p, idx) => (
                <button
                  key={idx}
                  onClick={() => handleSend(p.prompt)}
                  className="w-full text-left p-2.5 bg-white border border-slate-200 hover:border-indigo-300 hover:bg-indigo-50/30 rounded-xl transition-all duration-150 flex items-start gap-2.5 group shadow-sm"
                >
                  <span className="text-base mt-0.5">{p.icon}</span>
                  <div>
                    <div className="text-xs font-semibold text-slate-800 group-hover:text-indigo-600">
                      {p.title}
                    </div>
                    <div className="text-[11px] text-slate-500 mt-0.5 line-clamp-1">
                      {p.prompt}
                    </div>
                  </div>
                </button>
              ))}
            </div>
          </div>
        ) : (
          /* Conversation turns */
          messages.map((msg) => {
            const isStepsOpen = !!expandedSteps[msg.id];
            const hasEvents = msg.events.length > 0;

            return (
              <div key={msg.id} className="space-y-3">
                {/* User message */}
                <div className="flex justify-end">
                  <div className="max-w-[85%] bg-indigo-600 text-white rounded-2xl rounded-tr-sm px-3.5 py-2.5 text-xs shadow-sm">
                    <p className="whitespace-pre-wrap">{msg.userText}</p>
                    <span className="block text-[10px] text-indigo-200 mt-1 text-right">
                      {msg.timestamp}
                    </span>
                  </div>
                </div>

                {/* Agent thoughts & steps pipeline */}
                {hasEvents && (
                  <div className="bg-white border border-slate-200 rounded-xl p-3 shadow-xs text-xs space-y-2">
                    <div
                      onClick={() =>
                        setExpandedSteps((prev) => ({
                          ...prev,
                          [msg.id]: !prev[msg.id],
                        }))
                      }
                      className="flex items-center justify-between cursor-pointer select-none text-slate-600 hover:text-slate-900"
                    >
                      <div className="flex items-center gap-1.5 font-medium text-[11px] uppercase tracking-wider text-slate-400">
                        <span>🧭 Agent Workflow</span>
                        {msg.status === "running" && (
                          <span className="w-1.5 h-1.5 rounded-full bg-indigo-500 animate-ping" />
                        )}
                      </div>
                      <span className="text-slate-400 text-[10px]">
                        {isStepsOpen ? "▲ Hide" : "▼ Details"}
                      </span>
                    </div>

                    {/* Collapsible Steps list */}
                    {isStepsOpen && (
                      <div className="space-y-1.5 pt-1 border-t border-slate-100">
                        {msg.events.map((ev, i) => (
                          <div
                            key={i}
                            className="flex items-start gap-2 text-xs py-0.5"
                          >
                            {ev.type === "understanding" && (
                              <>
                                <span className="text-slate-400">🔎</span>
                                <span className="text-slate-700">
                                  Understood:{" "}
                                  <strong className="text-slate-900">
                                    {String((ev.data as { intent: string }).intent)}
                                  </strong>
                                </span>
                              </>
                            )}

                            {ev.type === "retrieval" && (
                              <>
                                <span className="text-slate-400">📄</span>
                                <span className="text-slate-700">
                                  Found page:{" "}
                                  <span className="font-medium text-slate-900">
                                    {(ev.data as { candidates: { title: string }[] }).candidates?.[0]?.title ?? "pages"}
                                  </span>
                                </span>
                              </>
                            )}

                            {ev.type === "plan" && (
                              <>
                                <span className="text-slate-400">📋</span>
                                <span className="text-slate-700">
                                  Plan:{" "}
                                  {(ev.data as { steps: { tool: string }[] }).steps?.map((s) => s.tool).join(" → ")}
                                </span>
                              </>
                            )}

                            {ev.type === "validation" && (
                              <>
                                <span className="text-emerald-500">✓</span>
                                <span className="text-slate-700">
                                  Plan verified against app schema
                                </span>
                              </>
                            )}

                            {ev.type === "action" && (
                              <>
                                <span className="text-indigo-500">▶</span>
                                <span className="text-slate-700 font-mono text-[11px]">
                                  {(ev.data as { tool: string }).tool}
                                </span>
                              </>
                            )}

                            {ev.type === "verify" && (
                              <>
                                {(ev.data as { ok: boolean }).ok ? (
                                  <span className="text-emerald-600 font-medium flex items-center gap-1">
                                    🛡️ Screen verified ✓ (SHA-256 match)
                                  </span>
                                ) : (
                                  <span className="text-red-600 font-medium flex items-center gap-1">
                                    ⚠ Screen mismatch detected
                                  </span>
                                )}
                              </>
                            )}

                            {ev.type === "evidence" && (
                              <>
                                <span className="text-slate-400">📊</span>
                                <span className="text-slate-700">
                                  Evidence [{(ev.data as { evidence_id: string }).evidence_id}] read from widget
                                </span>
                              </>
                            )}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}

                {/* Confirm request modal / banner */}
                {msg.confirmRequest && (
                  <div className="bg-amber-50 border border-amber-200 rounded-xl p-3 text-xs space-y-2">
                    <div className="flex items-center gap-1.5 font-semibold text-amber-800">
                      <span>🛡️ Confirm Action</span>
                    </div>
                    <p className="text-amber-900">{msg.confirmRequest.summary}</p>
                    <div className="flex items-center gap-2 pt-1">
                      <button
                        onClick={() =>
                          handleConfirm(msg.confirmRequest!.actionId, true)
                        }
                        className="px-3 py-1 bg-emerald-600 hover:bg-emerald-700 text-white rounded-md font-medium transition-colors"
                      >
                        Approve
                      </button>
                      <button
                        onClick={() =>
                          handleConfirm(msg.confirmRequest!.actionId, false)
                        }
                        className="px-3 py-1 bg-slate-200 hover:bg-slate-300 text-slate-700 rounded-md font-medium transition-colors"
                      >
                        Decline
                      </button>
                    </div>
                  </div>
                )}

                {/* Agent answer bubble */}
                {msg.answer && (
                  <div className="bg-white border border-slate-200 rounded-2xl rounded-tl-sm p-3.5 text-xs text-slate-800 shadow-xs space-y-2.5">
                    <div className="leading-relaxed">
                      {renderAnswerText(msg.answer.text, msg)}
                    </div>

                    {/* Deep links */}
                    {msg.answer.deep_links && msg.answer.deep_links.length > 0 && (
                      <div className="pt-1 flex flex-wrap gap-1.5">
                        {msg.answer.deep_links.map((link, idx) => (
                          <button
                            key={idx}
                            onClick={() => navigate(link)}
                            className="inline-flex items-center gap-1 px-2.5 py-1 text-[11px] font-medium text-indigo-700 bg-indigo-50 hover:bg-indigo-100 rounded-lg border border-indigo-200 transition-colors"
                          >
                            <span>↗ Open Page</span>
                          </button>
                        ))}
                      </div>
                    )}

                    {/* Verified badge footer */}
                    {msg.verify && (
                      <div className="pt-1.5 border-t border-slate-100 flex items-center justify-between text-[11px]">
                        <span
                          className={`font-medium flex items-center gap-1 ${
                            msg.verify.ok ? "text-emerald-600" : "text-red-600"
                          }`}
                        >
                          {msg.verify.ok ? "✓ Verified on screen" : "⚠ Screen mismatch"}
                        </span>
                        {msg.executionMs !== undefined && (
                          <span className="text-slate-400">
                            {msg.executionMs}ms
                          </span>
                        )}
                      </div>
                    )}
                  </div>
                )}

                {/* Error bubble */}
                {msg.error && (
                  <div className="bg-red-50 border border-red-200 rounded-xl p-3 text-xs text-red-700 space-y-1">
                    <span className="font-semibold">⚠ Agent Error</span>
                    <p>{msg.error}</p>
                  </div>
                )}
              </div>
            );
          })
        )}
      </div>

      {/* Input container */}
      <div className="p-3 border-t border-slate-200 bg-white">
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void handleSend();
          }}
          className="relative flex items-center"
        >
          <textarea
            ref={textareaRef}
            rows={2}
            value={inputText}
            onChange={(e) => setInputText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void handleSend();
              }
            }}
            placeholder="Ask AppPilot to find pages, analyze, or filter..."
            disabled={isRunning}
            className="w-full text-xs p-2.5 pr-12 border border-slate-200 rounded-xl focus:outline-none focus:ring-2 focus:ring-indigo-500 resize-none bg-slate-50 focus:bg-white transition-colors"
          />
          <button
            type="submit"
            disabled={!inputText.trim() || isRunning}
            className="absolute right-2 top-2.5 bottom-2.5 px-2.5 bg-indigo-600 hover:bg-indigo-700 disabled:bg-slate-200 text-white rounded-lg flex items-center justify-center transition-colors disabled:cursor-not-allowed shadow-xs"
            title="Send prompt (Enter)"
          >
            {isRunning ? (
              <div className="w-3.5 h-3.5 border-2 border-white border-t-transparent rounded-full animate-spin" />
            ) : (
              <span>↑</span>
            )}
          </button>
        </form>
        <div className="flex items-center justify-between mt-2 px-1 text-[10px] text-slate-400">
          <span>Enter to send · Shift+Enter for new line</span>
          {messages.length > 0 && (
            <button
              onClick={() => {
                setMessages([]);
                useAppStore.getState().clearAgentEvents();
              }}
              className="hover:text-slate-600 hover:underline"
            >
              Clear chat
            </button>
          )}
        </div>
      </div>
    </aside>
  );
}
