/**
 * Bar Chart widget (horizontal).
 * - Category on y-axis, metric on x-axis, primary colour bars with value labels
 * - Shows top 20 when > 20 categories, with a note
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

  const truncate = (s: string, n = 22) =>
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
        <p className="text-xs text-slate-400 mb-1">
          Showing {MAX_BARS} of {totalRows} — see table for all
        </p>
      )}
      <ResponsiveContainer width="100%" height={chartHeight}>
        <BarChart
          data={chartData}
          layout="vertical"
          margin={{ left: 0, right: 56, top: 4, bottom: 4 }}
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
            width={130}
            tick={{ fontSize: 12, fill: "#0F172A" }}
            axisLine={false}
            tickLine={false}
          />
          <Tooltip
            formatter={(val: any) => [
              formatValue(val, unit),
              metric?.title ?? metricId,
            ]}
            labelStyle={{ color: "#0F172A", fontWeight: 600 }}
            contentStyle={{
              border: "1px solid #E2E8F0",
              borderRadius: 8,
              fontSize: 13,
            }}
          />
          <Bar dataKey="value" radius={[0, 4, 4, 0]}>
            {chartData.map((_, i) => (
              <Cell key={i} fill={PRIMARY} />
            ))}
            <LabelList
              dataKey="value"
              position="right"
              formatter={(v: any) => formatValue(v, unit)}
              style={{ fontSize: 11, fill: "#64748B" }}
            />
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </WidgetCard>
  );
}
