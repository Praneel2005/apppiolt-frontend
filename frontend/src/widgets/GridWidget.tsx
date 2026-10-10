/**
 * Grid (table) widget — table with client-side sorting and pagination.
 *
 * - Sortable column headers (sort change → user_state_change via store)
 * - Numbers right-aligned and formatted by unit
 * - Sticky header
 * - 25 rows per page pagination with clear counters
 */

import { useMemo, useState } from "react";
import { formatValue } from "../lib/format";
import type { FilterDef, Sort, Widget } from "../lib/types";
import { useAppStore } from "../state/store";
import { useWidgetData, WidgetCard } from "./widgetBase";

const PAGE_SIZE = 25;

interface GridWidgetProps {
  widget: Widget;
  pageFilters: FilterDef[];
  datasetFieldNames: string[];
}

export function GridWidget({
  widget,
  pageFilters,
  datasetFieldNames,
}: GridWidgetProps) {
  const uiState = useAppStore((s) => s.uiState);
  const wsSend = useAppStore((s) => s.wsSend);
  const application = useAppStore((s) => s.application);

  const dateRange = uiState?.date_range ?? null;
  const storeSort = uiState?.sort ?? null;

  const [sortField, setSortField] = useState<string | null>(storeSort?.field ?? null);
  const [sortDir, setSortDir] = useState<"asc" | "desc">(storeSort?.dir ?? "desc");
  const [page, setPage] = useState(0);

  const sort: Sort | null = sortField
    ? { field: sortField, dir: sortDir }
    : null;

  const { data, loading, error } = useWidgetData({
    widget,
    pageFilters,
    datasetFieldNames,
    dateRange,
    sort,
  });

  const isEmpty = !loading && !error && (data?.rows.length ?? 0) === 0;

  const dataset = application?.datasets.find(
    (d) => d.dataset_id === widget.dataset_id
  );

  // Column definitions
  type Col = { id: string; header: string; unit: string; numeric: boolean };
  const columns: Col[] = [
    ...widget.dimensions.map((dim) => ({
      id: dim,
      header: dim.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()),
      unit: "",
      numeric: false,
    })),
    ...widget.metrics.map((mId) => {
      const metric = dataset?.metrics.find((m) => m.metric_id === mId);
      return {
        id: mId,
        header: metric?.title ?? mId,
        unit: metric?.unit ?? "",
        numeric: true,
      };
    }),
  ];

  // Client-side sort of all rows
  const sortedRows = useMemo(() => {
    const rows = data?.rows ?? [];
    if (!sortField) return rows;
    return [...rows].sort((a, b) => {
      const av = a[sortField];
      const bv = b[sortField];
      const an = typeof av === "number" ? av : parseFloat(String(av));
      const bn = typeof bv === "number" ? bv : parseFloat(String(bv));
      const diff = isNaN(an) || isNaN(bn)
        ? String(av ?? "").localeCompare(String(bv ?? ""))
        : an - bn;
      return sortDir === "asc" ? diff : -diff;
    });
  }, [data, sortField, sortDir]);

  const totalPages = Math.ceil(sortedRows.length / PAGE_SIZE);
  const pageRows = sortedRows.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);

  function handleSort(colId: string) {
    const newDir: "asc" | "desc" =
      sortField === colId && sortDir === "desc" ? "asc" : "desc";
    setSortField(colId);
    setSortDir(newDir);
    setPage(0);

    if (uiState && wsSend) {
      const newSort: Sort = { field: colId, dir: newDir };
      const newState = { ...uiState, sort: newSort };
      wsSend({ type: "user_state_change", state: newState });
    }
  }

  const startEntry = sortedRows.length === 0 ? 0 : page * PAGE_SIZE + 1;
  const endEntry = Math.min((page + 1) * PAGE_SIZE, sortedRows.length);

  return (
    <WidgetCard
      widgetId={widget.widget_id}
      title={widget.title}
      loading={loading}
      error={error}
      empty={isEmpty}
    >
      <div className="overflow-auto max-h-96 rounded-xl border border-slate-200/80 shadow-2xs">
        <table className="w-full text-xs border-collapse">
          <thead className="sticky top-0 bg-slate-50 z-10 border-b border-slate-200">
            <tr>
              {columns.map((col) => {
                const isSorted = sortField === col.id;
                return (
                  <th
                    key={col.id}
                    onClick={() => handleSort(col.id)}
                    className={`px-3.5 py-2.5 text-xs font-semibold text-slate-600 uppercase tracking-wider cursor-pointer select-none whitespace-nowrap transition-colors hover:bg-slate-100 ${
                      col.numeric ? "text-right" : "text-left"
                    } ${isSorted ? "text-indigo-600 bg-indigo-50/40" : ""}`}
                  >
                    <span>{col.header}</span>
                    <span className="ml-1 text-[11px] text-slate-400">
                      {isSorted ? (sortDir === "asc" ? "▲" : "▼") : "⇅"}
                    </span>
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {pageRows.map((row, i) => (
              <tr
                key={i}
                className={`transition-colors hover:bg-indigo-50/20 ${
                  i % 2 === 0 ? "bg-white" : "bg-slate-50/50"
                }`}
              >
                {columns.map((col) => (
                  <td
                    key={col.id}
                    className={`px-3.5 py-2 text-slate-700 ${
                      col.numeric ? "text-right tabular-nums font-medium" : ""
                    }`}
                  >
                    {col.numeric
                      ? formatValue(row[col.id], col.unit)
                      : String(row[col.id] ?? "")}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {sortedRows.length > 0 && (
        <div className="flex items-center justify-between pt-1 px-1 text-xs text-slate-500 select-none">
          <span>
            Showing <strong className="text-slate-700">{startEntry}</strong> to{" "}
            <strong className="text-slate-700">{endEntry}</strong> of{" "}
            <strong className="text-slate-700">{sortedRows.length}</strong> rows
          </span>

          {totalPages > 1 && (
            <div className="flex items-center gap-1.5">
              <button
                onClick={() => setPage((p) => Math.max(0, p - 1))}
                disabled={page === 0}
                className="px-2.5 py-1 rounded-lg border border-slate-200 bg-white hover:bg-slate-50 disabled:opacity-40 disabled:cursor-not-allowed transition-colors text-xs font-medium"
              >
                ← Prev
              </button>
              <span className="text-slate-400 px-1 text-xs">
                {page + 1} / {totalPages}
              </span>
              <button
                onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
                disabled={page >= totalPages - 1}
                className="px-2.5 py-1 rounded-lg border border-slate-200 bg-white hover:bg-slate-50 disabled:opacity-40 disabled:cursor-not-allowed transition-colors text-xs font-medium"
              >
                Next →
              </button>
            </div>
          )}
        </div>
      )}
    </WidgetCard>
  );
}
