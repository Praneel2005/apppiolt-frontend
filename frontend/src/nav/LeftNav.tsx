/**
 * Left navigation sidebar.
 *
 * Structure driven by application.json:
 *   Module → Overview page (if exists) + collapsible groups (breakdowns, trends)
 *
 * Features:
 *   - Search box filters page titles with instant clear button
 *   - Visual module icons & counts
 *   - Current page highlighted with primary styling
 *   - Clicking a page navigates and sends user_state_change
 */

import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import type { Application, Page } from "../lib/types";
import { useAppStore } from "../state/store";
import { uiStateToSearch } from "../state/urlSync";

interface NavProps {
  application: Application;
}

const MODULE_ICONS: Record<string, string> = {
  sales: "💰",
  customers: "👥",
  orders: "📋",
  products: "📦",
  sellers: "🏪",
  delivery: "🚚",
  freight: "🚚",
  reviews: "⭐",
  marketing: "📣",
  payments: "💳",
};

export function LeftNav({ application }: NavProps) {
  const [search, setSearch] = useState("");
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const uiState = useAppStore((s) => s.uiState);
  const wsSend = useAppStore((s) => s.wsSend);
  const setUiState = useAppStore((s) => s.setUiState);
  const navigate = useNavigate();

  const currentPageId = uiState?.page_id;

  // Group pages by module
  const moduleGroups = useMemo(() => {
    const groups: Record<
      string,
      { overview: Page | null; dirs: Record<string, Page[]>; totalPages: number }
    > = {};

    for (const mod of application.modules) {
      groups[mod.module_id] = { overview: null, dirs: {}, totalPages: 0 };
    }

    const low = search.toLowerCase();

    for (const page of application.pages) {
      if (search && !page.title.toLowerCase().includes(low)) continue;
      const parts = page.directory.split("/");
      const moduleId = parts[0];

      if (!groups[moduleId]) {
        groups[moduleId] = { overview: null, dirs: {}, totalPages: 0 };
      }

      groups[moduleId].totalPages++;

      if (parts.length === 1 || page.title.toLowerCase().includes("overview")) {
        if (!groups[moduleId].overview) {
          groups[moduleId].overview = page;
        }
      } else {
        const subDir = parts.slice(1).join("/");
        if (!groups[moduleId].dirs[subDir]) {
          groups[moduleId].dirs[subDir] = [];
        }
        groups[moduleId].dirs[subDir].push(page);
      }
    }

    return groups;
  }, [application, search]);

  function navigate_to(page: Page) {
    if (!uiState) return;
    const newState = {
      ...uiState,
      route: page.route,
      page_id: page.page_id,
      filters: {},
      sort: null,
      selected_widget: null,
      version: uiState.version + 1,
    };
    setUiState(newState);
    if (wsSend) {
      wsSend({ type: "user_state_change", state: newState });
    }
    const searchParam = uiStateToSearch(newState);
    navigate(page.route + (searchParam ? `?${searchParam}` : ""));
  }

  function toggleDir(key: string) {
    setCollapsed((c) => ({ ...c, [key]: !c[key] }));
  }

  const dirLabel = (path: string) =>
    path
      .split("/")
      .pop()!
      .replace(/-/g, " ")
      .replace(/\b\w/g, (c) => c.toUpperCase());

  return (
    <nav
      className="w-[260px] min-w-[260px] border-r border-slate-200 bg-white flex flex-col overflow-hidden select-none"
      style={{ height: "calc(100vh - 56px)" }}
    >
      {/* Search box */}
      <div className="p-3 border-b border-slate-100">
        <div className="relative flex items-center">
          <span className="absolute left-2.5 text-slate-400 text-xs">🔍</span>
          <input
            type="text"
            placeholder="Search 100 pages…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full text-xs pl-8 pr-7 py-1.5 border border-slate-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500 bg-slate-50 focus:bg-white transition-colors"
          />
          {search && (
            <button
              onClick={() => setSearch("")}
              className="absolute right-2.5 text-slate-400 hover:text-slate-600 text-xs"
            >
              ✕
            </button>
          )}
        </div>
      </div>

      {/* Module tree */}
      <div className="overflow-y-auto flex-1 py-2 px-1 space-y-1">
        {application.modules.map((mod) => {
          const group = moduleGroups[mod.module_id];
          if (!group) return null;
          const hasContent =
            group.overview || Object.keys(group.dirs).length > 0;
          if (!hasContent && search) return null;

          const icon = MODULE_ICONS[mod.module_id.toLowerCase()] || "📊";

          return (
            <div key={mod.module_id} className="mb-2">
              {/* Module header */}
              <div className="px-3 py-1 flex items-center justify-between text-xs font-bold text-slate-500 uppercase tracking-wider">
                <span className="flex items-center gap-1.5">
                  <span>{icon}</span>
                  <span>{mod.title}</span>
                </span>
                <span className="text-[10px] text-slate-400 font-normal px-1.5 py-0.2 bg-slate-100 rounded-full">
                  {group.totalPages}
                </span>
              </div>

              {/* Overview page */}
              {group.overview && (
                <NavItem
                  page={group.overview}
                  active={group.overview.page_id === currentPageId}
                  onClick={() => navigate_to(group.overview!)}
                  indent={1}
                />
              )}

              {/* Sub-directories (Breakdowns, Trends, etc.) */}
              {Object.entries(group.dirs).map(([dir, pages]) => {
                const key = `${mod.module_id}/${dir}`;
                const isOpen = !collapsed[key];
                return (
                  <div key={dir}>
                    <button
                      onClick={() => toggleDir(key)}
                      className="flex items-center justify-between w-full px-3 py-1.5 text-xs text-slate-600 hover:text-slate-900 hover:bg-slate-50 rounded-lg transition-colors"
                      style={{ paddingLeft: "1.25rem" }}
                    >
                      <div className="flex items-center gap-1.5">
                        <span className="text-slate-400 text-[10px]">
                          {isOpen ? "▼" : "▶"}
                        </span>
                        <span className="font-medium">{dirLabel(dir)}</span>
                      </div>
                      <span className="text-[10px] text-slate-400">
                        {pages.length}
                      </span>
                    </button>
                    {isOpen &&
                      pages.map((page) => (
                        <NavItem
                          key={page.page_id}
                          page={page}
                          active={page.page_id === currentPageId}
                          onClick={() => navigate_to(page)}
                          indent={2}
                        />
                      ))}
                  </div>
                );
              })}
            </div>
          );
        })}
      </div>
    </nav>
  );
}

interface NavItemProps {
  page: Page;
  active: boolean;
  onClick: () => void;
  indent: number;
}

function NavItem({ page, active, onClick, indent }: NavItemProps) {
  return (
    <button
      onClick={onClick}
      className={`w-full text-left text-xs py-1.5 pr-2 rounded-lg transition-all duration-150 flex items-center justify-between group ${
        active
          ? "bg-indigo-50 text-indigo-700 font-semibold shadow-2xs border-l-2 border-indigo-600"
          : "text-slate-600 hover:bg-slate-50 hover:text-slate-900 border-l-2 border-transparent"
      }`}
      style={{ paddingLeft: `${indent * 14}px` }}
    >
      <span className="truncate">{page.title}</span>
      {active && (
        <span className="w-1.5 h-1.5 rounded-full bg-indigo-600 mr-1" />
      )}
    </button>
  );
}
