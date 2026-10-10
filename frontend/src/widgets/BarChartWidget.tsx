/**
 * Bar Chart widget (horizontal).
 * - Category on y-axis, metric on x-axis, primary colour bars with value labels
 * - Shows top 20 when > 20 categories, with a clean info banner
 */

import {
  Bar,
  BarChart,
  Cell,
  LabelList,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { formatValue } from "../lib/format";
import type { FilterDef, Widget } from "../lib/types";
import { useAppStore } from "../state/store";
import { useWidgetData, WidgetCard } from "./widgetBase";

const PRIMARY = "#4F46E5";
const MAX_BARS = 20;

interface BarChartWidgetProps {
  widget: Widget;
  pageFilters: FilterDef[];
  datasetFieldNames: string[];
}

export function BarChartWidget({
  widget,
  pageFilters,
  datasetFieldNames,
}: BarChartWidgetProps) {
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
  const dimId = widget.dimensions[0];

  const totalRows = data?.rows.length ?? 0;
  const visibleRows = (data?.rows ?? []).slice(0, MAX_BARS);

  const truncate = (s: string, n = 24) =>
    s && s.length > n ? s.slice(0, n) + "…" : s;

  const chartData = visibleRows.map((row) => ({
    label: truncate(String(row[dimId] ?? "")),
    value: Number(row[metricId] ?? 0),
  }));

  const barHeight = 28;
  const chartHeight = Math.max(180, visibleRows.length * barHeight + 40);

  return (
    <WidgetCard
      widgetId={widget.widget_id}
      title={widget.title}
      loading={loading}
      error={error}
      empty={isEmpty}
    >
      {totalRows > MAX_BARS && (
        <div className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-slate-50 border border-slate-200/80 text-xs text-slate-500 mb-2">
          <span>ℹ️</span>
          <span>
            Showing top <strong>{MAX_BARS}</strong> of <strong>{totalRows}</strong> categories — see table for complete data
          </span>
        </div>
      )}
      <ResponsiveContainer width="100%" height={chartHeight}>
        <BarChart
          data={chartData}
          layout="vertical"
          margin={{ left: 0, right: 64, top: 4, bottom: 4 }}
        >
          <XAxis
            type="number"
            tick={{ fontSize: 11, fill: "#64748B" }}
            axisLine={false}
            tickLine={false}
            tickFormatter={(v: number) => formatValue(v, unit)}
          />
          <YAxis
            type="category"
            dataKey="label"
            width={140}
            tick={{ fontSize: 12, fill: "#1E293B" }}
            axisLine={false}
            tickLine={false}
          />
          <Tooltip
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            formatter={(val: any) => [
              formatValue(val, unit),
              metric?.title ?? metricId,
            ]}
            labelStyle={{ color: "#0F172A", fontWeight: 600 }}
            contentStyle={{
              border: "1px solid #E2E8F0",
              borderRadius: 12,
              boxShadow: "0 4px 12px rgba(15,23,42,0.08)",
              fontSize: 12,
            }}
          />
          <Bar dataKey="value" radius={[0, 6, 6, 0]}>
            {chartData.map((_, i) => (
              <Cell key={i} fill={PRIMARY} />
            ))}
            <LabelList
              dataKey="value"
              position="right"
              // eslint-disable-next-line @typescript-eslint/no-explicit-any
              formatter={(v: any) => formatValue(v, unit)}
              style={{ fontSize: 11, fill: "#64748B", fontWeight: 500 }}
            />
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </WidgetCard>
  );
}
