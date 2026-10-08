/**
 * KPI Card widget.
 * Shows one large formatted number with title and unit description.
 * Designed for "Overview" pages — one row, no dimensions.
 */

import type { FilterDef, Widget } from "../lib/types";
import { formatValue } from "../lib/format";
import { useAppStore } from "../state/store";
import { useWidgetData, WidgetCard } from "./widgetBase";

interface KpiCardWidgetProps {
  widget: Widget;
  pageFilters: FilterDef[];
  datasetFieldNames: string[];
}

export function KpiCardWidget({
  widget,
  pageFilters,
  datasetFieldNames,
}: KpiCardWidgetProps) {
  const uiState = useAppStore((s) => s.uiState);
  const application = useAppStore((s) => s.application);

  const dateRange = uiState?.date_range ?? null;
  const sort = uiState?.sort ?? null;

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
  const metricId = widget.metrics[0];
  const metric = dataset?.metrics.find((m) => m.metric_id === metricId);
  const unit = metric?.unit ?? "";

  const value =
    data && data.rows.length > 0 ? data.rows[0][metricId] : null;
  const formatted = value !== null && value !== undefined
    ? formatValue(value, unit)
    : "—";

  return (
    <WidgetCard
      widgetId={widget.widget_id}
      title={widget.title}
      loading={loading}
      error={error}
      empty={isEmpty}
    >
      <div className="flex flex-col gap-1">
        <span className="text-[28px] font-bold text-slate-900 leading-tight">
          {formatted}
        </span>
        {metric && (
          <span className="text-xs text-slate-400 uppercase tracking-wide">
            {metric.title}
          </span>
        )}
      </div>
    </WidgetCard>
  );
}
