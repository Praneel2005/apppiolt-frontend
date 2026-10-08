/**
 * App shell — top-level layout with:
 *   - Session initialisation (POST /api/session, open WS)
 *   - React Router wired to UI state
 *   - Top bar, left nav, main content area, right chat panel placeholder
 *   - URL ↔ store sync
 */

import { useEffect } from "react";
import {
  BrowserRouter,
  useNavigate,
  useLocation,
} from "react-router-dom";
import { api } from "../net/api";
import { openWebSocket } from "../net/ws";
import { LeftNav } from "../nav/LeftNav";
import { PageRenderer } from "../pages/PageRenderer";
import { useAppStore } from "../state/store";
import { uiStateToSearch } from "../state/urlSync";

// ─── Top bar ─────────────────────────────────────────────────────────────────

function TopBar() {
  const application = useAppStore((s) => s.application);
  const lastVerify = useAppStore((s) => s.lastVerify);

  return (
    <header
      className="h-14 min-h-[56px] flex items-center px-6 border-b border-slate-200 bg-white gap-4 z-20"
      style={{ boxShadow: "0 1px 2px rgba(15,23,42,.06)" }}
    >
      <span className="font-bold text-slate-900 text-base">AppPilot</span>
      <span className="text-slate-300">·</span>
      <span className="text-slate-500 text-sm">
        {application?.name ?? "Loading…"}
      </span>
      {application?.as_of_date && (
        <span className="ml-auto text-xs text-slate-400 border border-slate-200 rounded-lg px-3 py-1">
          Data as of {application.as_of_date}
        </span>
      )}
      {lastVerify !== null && (
        <span
          className={`text-xs rounded-full px-2 py-0.5 font-medium ${
            lastVerify.ok
              ? "bg-green-50 text-green-700"
              : "bg-red-50 text-red-600"
          }`}
        >
          {lastVerify.ok ? "✓ verified" : "⚠ mismatch"}
        </span>
      )}
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
          <div className="w-8 h-8 border-2 border-indigo-500 border-t-transparent rounded-full animate-spin" />
          <span className="text-sm">Connecting…</span>
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

function AppInner() {
  const setApplication = useAppStore((s) => s.setApplication);
  const setDatePresets = useAppStore((s) => s.setDatePresets);
  const setUiState = useAppStore((s) => s.setUiState);
  const application = useAppStore((s) => s.application);

  useEffect(() => {
    let mounted = true;

    async function init() {
      try {
        const [app, presets, session] = await Promise.all([
          api.application(),
          api.datePresets(),
          api.createSession(),
        ]);

        if (!mounted) return;
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
    return () => {
      mounted = false;
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="flex flex-col h-screen overflow-hidden font-[Inter,system-ui,sans-serif]">
      <RouterSync />
      <TopBar />
      <div className="flex flex-1 overflow-hidden">
        {application && <LeftNav application={application} />}
        <main className="flex-1 overflow-hidden flex flex-col">
          <AppRoutes />
        </main>
        {/* Chat panel placeholder — Shreeniketh builds this */}
        <aside className="w-[400px] min-w-[400px] border-l border-slate-200 bg-white flex items-center justify-center text-slate-300 text-sm">
          Chat panel (Shreeniketh)
        </aside>
      </div>
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
