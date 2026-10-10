/**
 * App shell — top-level layout with:
 *   - Session initialisation (POST /api/session, open WS)
 *   - React Router wired to UI state
 *   - Top bar, left nav, main content area, interactive AI Chat panel
 *   - Debug & verification drawer
 *   - URL ↔ store sync
 */

import { useEffect, useState } from "react";
import {
  BrowserRouter,
  useNavigate,
  useLocation,
} from "react-router-dom";
import { api } from "../net/api";
import { openWebSocket } from "../net/ws";
import { LeftNav } from "../nav/LeftNav";
import { PageRenderer } from "../pages/PageRenderer";
import { ChatPanel } from "../chat/ChatPanel";
import { DebugDrawer } from "../debug/DebugDrawer";
import { useAppStore } from "../state/store";
import { uiStateToSearch } from "../state/urlSync";

// ─── Top bar ─────────────────────────────────────────────────────────────────

interface TopBarProps {
  onToggleDebug: () => void;
  isDebugOpen: boolean;
}

function TopBar({ onToggleDebug, isDebugOpen }: TopBarProps) {
  const application = useAppStore((s) => s.application);
  const lastVerify = useAppStore((s) => s.lastVerify);

  return (
    <header
      className="h-14 min-h-[56px] flex items-center px-5 border-b border-slate-200 bg-white gap-4 z-20 select-none shadow-xs"
    >
      {/* Brand logo & title */}
      <div className="flex items-center gap-3">
        <div className="w-8 h-8 rounded-xl bg-gradient-to-tr from-indigo-600 to-indigo-500 flex items-center justify-center text-white font-bold text-base shadow-sm ring-2 ring-indigo-100">
          ✈
        </div>
        <div>
          <div className="flex items-center gap-1.5">
            <span className="font-extrabold text-slate-900 text-sm tracking-tight">
              AppPilot
            </span>
            <span className="inline-flex items-center gap-1 px-1.5 py-0.2 rounded-full text-[10px] font-semibold bg-emerald-50 text-emerald-700 border border-emerald-200/60">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
              Agent Ready
            </span>
          </div>
          <p className="text-[11px] text-slate-400 font-medium -mt-0.5">
            {application?.name ?? "Marketplace Analytics"}
          </p>
        </div>
      </div>

      {/* Right status tools */}
      <div className="ml-auto flex items-center gap-2.5">
        {/* As of date chip */}
        {application?.as_of_date && (
          <div className="hidden sm:flex items-center gap-1.5 text-xs text-slate-500 bg-slate-50 border border-slate-200 rounded-lg px-2.5 py-1">
            <span className="text-slate-400">📅</span>
            <span>Data as of {application.as_of_date}</span>
          </div>
        )}

        {/* Verification status pill */}
        {lastVerify !== null ? (
          <div
            className={`text-xs rounded-lg px-2.5 py-1 font-medium border flex items-center gap-1.5 ${
              lastVerify.ok
                ? "bg-emerald-50 text-emerald-700 border-emerald-200"
                : "bg-red-50 text-red-600 border-red-200"
            }`}
          >
            <span>{lastVerify.ok ? "🛡️ Verified (SHA-256)" : "⚠ Verify Mismatch"}</span>
          </div>
        ) : (
          <div className="hidden md:flex items-center gap-1 text-xs text-slate-400 bg-slate-50 border border-slate-200 rounded-lg px-2.5 py-1">
            <span>🛡️ Verifier Standby</span>
          </div>
        )}

        {/* Debug button */}
        <button
          onClick={onToggleDebug}
          className={`text-xs px-2.5 py-1 rounded-lg border font-medium transition-colors flex items-center gap-1.5 ${
            isDebugOpen
              ? "bg-indigo-600 text-white border-indigo-600 shadow-xs"
              : "bg-slate-50 text-slate-600 border-slate-200 hover:bg-slate-100 hover:text-slate-900"
          }`}
          title="Toggle Debug & Verification Drawer"
        >
          <span>🛠️ Debug</span>
        </button>
      </div>
    </header>
  );
}

// ─── Route sync: keep URL = uiState ──────────────────────────────────────────

function RouterSync() {
  const uiState = useAppStore((s) => s.uiState);
  const navigate = useNavigate();
  const location = useLocation();

  useEffect(() => {
    if (!uiState) return;
    const search = uiStateToSearch(uiState);
    const target = uiState.route + (search ? `?${search}` : "");
    if (location.pathname + location.search !== target) {
      navigate(target, { replace: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [uiState]);

  return null;
}

// ─── Page route mapper ────────────────────────────────────────────────────────

function AppRoutes() {
  const application = useAppStore((s) => s.application);
  const uiState = useAppStore((s) => s.uiState);

  if (!application || !uiState) {
    return (
      <div className="flex-1 flex items-center justify-center text-slate-400">
        <div className="flex flex-col items-center gap-3">
          <div className="w-9 h-9 border-3 border-indigo-600 border-t-transparent rounded-full animate-spin" />
          <span className="text-sm font-medium text-slate-600">Connecting to AppPilot workspace…</span>
        </div>
      </div>
    );
  }

  const currentPage = application.pages.find(
    (p) => p.page_id === uiState.page_id
  );

  if (!currentPage) {
    return (
      <div className="flex-1 flex items-center justify-center text-slate-400 text-sm">
        Page not found: {uiState.page_id}
      </div>
    );
  }

  return (
    <PageRenderer
      key={currentPage.page_id}
      page={currentPage}
      application={application}
    />
  );
}

// ─── App bootstrap ────────────────────────────────────────────────────────────

let initStarted = false;

function AppInner() {
  const setApplication = useAppStore((s) => s.setApplication);
  const setDatePresets = useAppStore((s) => s.setDatePresets);
  const setUiState = useAppStore((s) => s.setUiState);
  const application = useAppStore((s) => s.application);

  const [isDebugOpen, setIsDebugOpen] = useState(false);

  useEffect(() => {
    if (initStarted) return;
    initStarted = true;

    async function init() {
      try {
        const [app, presets, session] = await Promise.all([
          api.application(),
          api.datePresets(),
          api.createSession(),
        ]);

        setApplication(app);
        setDatePresets(presets);
        setUiState(session.state);

        // Open WS (sets session + wsSend in store)
        openWebSocket(session.session_id);
      } catch (err) {
        console.error("Failed to init AppPilot", err);
      }
    }

    init();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="flex flex-col h-screen overflow-hidden font-[Inter,system-ui,sans-serif] bg-slate-50">
      <RouterSync />
      <TopBar
        onToggleDebug={() => setIsDebugOpen((d) => !d)}
        isDebugOpen={isDebugOpen}
      />
      <div className="flex flex-1 overflow-hidden relative">
        {application && <LeftNav application={application} />}
        <main className="flex-1 overflow-hidden flex flex-col bg-[#F8FAFC]">
          <AppRoutes />
        </main>
        {/* Full Interactive AI Agent Chat Panel */}
        <ChatPanel />
      </div>

      {/* Debug & Cryptographic Verification Drawer */}
      <DebugDrawer isOpen={isDebugOpen} onClose={() => setIsDebugOpen(false)} />
    </div>
  );
}

export function App() {
  return (
    <BrowserRouter>
      <AppInner />
    </BrowserRouter>
  );
}
