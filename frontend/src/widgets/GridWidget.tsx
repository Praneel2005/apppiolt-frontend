/**
 * Grid (table) widget — plain table with client-side sorting and pagination.
 * (TanStack Table v9 has a significantly changed API; using a simpler approach
 *  that is easier to debug during the hackathon.)
 *
 * - Sortable column headers (sort change → user_state_change via store)
 * - Numbers right-aligned and formatted by unit
 * - Sticky header
 * - 25 rows per page pagination
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

  return (
    <WidgetCard
      widgetId={widget.widget_id}
      title={widget.title}
      loading={loading}
      error={error}
      empty={isEmpty}
    >
      <div className="overflow-auto max-h-96 rounded border border-slate-100">
        <table className="w-full text-sm border-collapse">
          <thead className="sticky top-0 bg-white z-10">
            <tr>
              {columns.map((col) => (
                <th
                  key={col.id}
                  onClick={() => handleSort(col.id)}
                  className={`px-3 py-2 border-b border-slate-100 text-xs font-semibold text-slate-500 uppercase tracking-wide cursor-pointer select-none whitespace-nowrap ${
                    col.numeric ? "text-right" : "text-left"
                  }`}
                >
                  {col.header}
                  {sortField === col.id
                    ? sortDir === "asc"
                      ? " ↑"
                      : " ↓"
                    : ""}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {pageRows.map((row, i) => (
              <tr key={i} className={i % 2 === 0 ? "bg-white" : "bg-slate-50"}>
                {columns.map((col) => (
                  <td
                    key={col.id}
                    className={`px-3 py-2 text-slate-700 ${
                      col.numeric ? "text-right tabular-nums" : ""
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

      {totalPages > 1 && (
        <div className="flex items-center justify-between pt-2 text-xs text-slate-400">
          <button
            onClick={() => setPage((p) => Math.max(0, p - 1))}
            disabled={page === 0}
            className="px-2 py-1 rounded hover:bg-slate-100 disabled:opacity-40"
          >
            ← Prev
          </button>
          <span>
            Page {page + 1} of {totalPages}
          </span>
          <button
            onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
            disabled={page >= totalPages - 1}
            className="px-2 py-1 rounded hover:bg-slate-100 disabled:opacity-40"
          >
            Next →
          </button>
        </div>
      )}
    </WidgetCard>
  );
}
