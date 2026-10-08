/**
 * Left navigation sidebar.
 *
 * Structure driven by application.json:
 *   Module → Overview page (if exists) + collapsible groups (breakdowns, trends)
 *
 * Features:
 *   - Search box filters page titles
 *   - Current page highlighted (indigo)
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

export function LeftNav({ application }: NavProps) {
  const [search, setSearch] = useState("");
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const uiState = useAppStore((s) => s.uiState);
  const wsSend = useAppStore((s) => s.wsSend);
  const navigate = useNavigate();

  const currentPageId = uiState?.page_id;

  // Group pages by module (first segment of directory path)
  const moduleGroups = useMemo(() => {
    const groups: Record<
      string,
      { overview: Page | null; dirs: Record<string, Page[]> }
    > = {};

    for (const mod of application.modules) {
      groups[mod.module_id] = { overview: null, dirs: {} };
    }

    const low = search.toLowerCase();

    for (const page of application.pages) {
      if (search && !page.title.toLowerCase().includes(low)) continue;
      const parts = page.directory.split("/");
      const moduleId = parts[0];

      if (!groups[moduleId]) {
        groups[moduleId] = { overview: null, dirs: {} };
      }

      // Heuristic: if directory is just the module name, it's the overview
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
    if (!wsSend || !uiState) return;
    // Build a user_state_change to the new page (keep date range)
    const newState = {
      ...uiState,
      route: page.route,
      page_id: page.page_id,
      filters: {},
      sort: null,
      selected_widget: null,
    };
    wsSend({ type: "user_state_change", state: newState });
    const search = uiStateToSearch(newState);
    navigate(page.route + (search ? `?${search}` : ""));
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
      className="w-[260px] min-w-[260px] border-r border-slate-200 bg-white flex flex-col overflow-hidden"
      style={{ height: "calc(100vh - 56px)" }}
    >
      {/* Search */}
      <div className="p-3 border-b border-slate-100">
        <input
          type="text"
          placeholder="Search pages…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="w-full text-sm px-3 py-1.5 border border-slate-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-400 bg-slate-50"
        />
      </div>

      {/* Module tree */}
      <div className="overflow-y-auto flex-1 py-2">
        {application.modules.map((mod) => {
          const group = moduleGroups[mod.module_id];
          if (!group) return null;
          const hasContent =
            group.overview || Object.keys(group.dirs).length > 0;
          if (!hasContent && search) return null;

          return (
            <div key={mod.module_id} className="mb-1">
              {/* Module label */}
              <div className="px-4 py-1.5 text-xs font-bold text-slate-400 uppercase tracking-widest">
                {mod.title}
              </div>

              {/* Overview */}
              {group.overview && (
                <NavItem
                  page={group.overview}
                  active={group.overview.page_id === currentPageId}
                  onClick={() => navigate_to(group.overview!)}
                  indent={1}
                />
              )}

              {/* Sub-directories */}
              {Object.entries(group.dirs).map(([dir, pages]) => {
                const key = `${mod.module_id}/${dir}`;
                const isOpen = !collapsed[key];
                return (
                  <div key={dir}>
                    <button
                      onClick={() => toggleDir(key)}
                      className="flex items-center w-full px-4 py-1.5 text-sm text-slate-600 hover:text-slate-900 hover:bg-slate-50 transition-colors"
                      style={{ paddingLeft: "1.25rem" }}
                    >
                      <span className="mr-1.5 text-slate-400 text-xs">
                        {isOpen ? "▾" : "▸"}
                      </span>
                      {dirLabel(dir)}
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
      className={`w-full text-left text-sm py-1.5 pr-3 rounded-lg mx-1 transition-colors ${
        active
          ? "bg-indigo-50 text-indigo-700 font-medium"
          : "text-slate-600 hover:bg-slate-50 hover:text-slate-900"
      }`}
      style={{ paddingLeft: `${indent * 16}px` }}
    >
      {page.title}
    </button>
  );
}
