/**
 * Shared widget infrastructure:
 *  - WidgetCard wrapper (title, loading, error, empty states)
 *  - useWidgetData hook (fetches /api/widget-data, registers ack)
 *  - highlightWidget() exported function (Shreeniketh / citation chips call this)
 */

import React, { useEffect, useRef, useState } from "react";
import { canonicalSeriesHash } from "../lib/hash";
import type {
  DateRange,
  FilterDef,
  FilterValue,
  Sort,
  Widget,
  WidgetAck,
  WidgetDataResponse,
} from "../lib/types";
import { api } from "../net/api";
import { resolveWidgetAck, useAppStore } from "../state/store";

// ─── highlightWidget (exported, called by citation chips) ──────────────────────

export function highlightWidget(widgetId: string) {
  const el = document.querySelector(`[data-widget-id="${widgetId}"]`);
  if (!el) return;
  el.scrollIntoView({ behavior: "smooth", block: "center" });
  el.classList.add("widget-highlight");
  setTimeout(() => el.classList.remove("widget-highlight"), 2400);
}

// ─── useWidgetData hook ───────────────────────────────────────────────────────

function buildAppliedFilters(
  pageFilters: FilterDef[],
  storeFilters: Record<string, FilterValue>,
  datasetFields: Set<string>
): Record<string, FilterValue> {
  const applied: Record<string, FilterValue> = {};
  for (const [key, fv] of Object.entries(storeFilters)) {
    if (!fv || !fv.value || fv.value.length === 0) continue;
    const pf = pageFilters.find((f) => f.filter_id === key);
    const fieldName = pf ? pf.field : key;
    if (datasetFields.has(fieldName)) {
      applied[key] = fv;
    }
  }
  return applied;
}

interface UseWidgetDataOpts {
  widget: Widget;
  pageFilters: FilterDef[];
  datasetFieldNames: string[];
  dateRange?: DateRange | null;
  sort?: Sort | null;
}

export function useWidgetData({
  widget,
  pageFilters,
  datasetFieldNames,
  dateRange,
  sort,
}: UseWidgetDataOpts) {
  const storeFilters = useAppStore((s) => s.uiState?.filters ?? {});
  const uiStateVersion = useAppStore((s) => s.uiState?.version ?? 0);
  const datasetFields = new Set(datasetFieldNames);

  const [data, setData] = useState<WidgetDataResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const startRef = useRef(Date.now());

  const appliedFilters = buildAppliedFilters(
    pageFilters,
    storeFilters,
    datasetFields
  );

  const filterKey = JSON.stringify(appliedFilters);
  const dateKey = JSON.stringify(dateRange);
  const sortKey = JSON.stringify(sort);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    startRef.current = Date.now();

    api
      .widgetData({
        widget_id: widget.widget_id,
        filters: appliedFilters,
        date_range: dateRange,
        sort: sort ?? null,
      })
      .then(async (resp) => {
        if (cancelled) return;
        setData(resp);
        setLoading(false);

        const renderMs = Date.now() - startRef.current;
        const hash = await canonicalSeriesHash(resp.rows, resp.columns);
        const ack: WidgetAck = {
          widget_id: widget.widget_id,
          applied_filters: appliedFilters,
          applied_date_range: dateRange ?? null,
          applied_sort: sort ?? null,
          row_count: resp.row_count,
          series_hash: hash,
          render_ms: renderMs,
          error: null,
        };
        resolveWidgetAck(widget.widget_id, ack);
      })
      .catch((err: Error) => {
        if (cancelled) return;
        setError(err.message);
        setLoading(false);
        const ack: WidgetAck = {
          widget_id: widget.widget_id,
          applied_filters: appliedFilters,
          applied_date_range: dateRange ?? null,
          applied_sort: sort ?? null,
          row_count: 0,
          series_hash: "",
          render_ms: Date.now() - startRef.current,
          error: err.message,
        };
        resolveWidgetAck(widget.widget_id, ack);
      });

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [widget.widget_id, filterKey, dateKey, sortKey, uiStateVersion]);

  return { data, loading, error };
}

// ─── WidgetCard wrapper ───────────────────────────────────────────────────────

interface WidgetCardProps {
  widgetId: string;
  title: string;
  loading?: boolean;
  error?: string | null;
  empty?: boolean;
  children: React.ReactNode;
}

export function WidgetCard({
  widgetId,
  title,
  loading,
  error,
  empty,
  children,
}: WidgetCardProps) {
  return (
    <div
      data-widget-id={widgetId}
      className="bg-white border border-slate-200/90 rounded-2xl shadow-xs p-5 flex flex-col gap-3 transition-all duration-200 hover:border-slate-300"
      style={{ minHeight: 150 }}
    >
      <div className="flex items-center justify-between gap-2 select-none">
        <h3 className="text-sm font-semibold text-slate-800 leading-snug">
          {title}
        </h3>
        <span
          className="text-[10px] font-mono text-slate-400 bg-slate-50 border border-slate-200/60 px-1.5 py-0.5 rounded-md hover:text-slate-600 cursor-default"
          title={`Widget identifier: ${widgetId}`}
        >
          #{widgetId}
        </span>
      </div>
      {loading ? (
        <SkeletonBlock />
      ) : error ? (
        <ErrorState message={error} />
      ) : empty ? (
        <EmptyState />
      ) : (
        children
      )}
    </div>
  );
}

function SkeletonBlock() {
  return (
    <div className="flex flex-col gap-2.5 animate-pulse py-2">
      <div className="h-6 bg-slate-100 rounded-lg w-3/4" />
      <div className="h-6 bg-slate-100 rounded-lg w-1/2" />
      <div className="h-6 bg-slate-100 rounded-lg w-2/3" />
    </div>
  );
}

function ErrorState({ message }: { message: string }) {
  return (
    <div className="text-xs text-red-600 bg-red-50 border border-red-200 rounded-xl p-3 flex items-start gap-2">
      <span>⚠</span>
      <div>
        <span className="font-semibold">Error:</span> {message}
      </div>
    </div>
  );
}

function EmptyState() {
  return (
    <div className="flex flex-col items-center justify-center flex-1 gap-1 text-slate-400 py-6 select-none">
      <span className="text-2xl">📭</span>
      <span className="text-xs font-medium">No data found for this filter selection</span>
    </div>
  );
}
