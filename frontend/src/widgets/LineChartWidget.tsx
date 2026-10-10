/**
 * Line Chart widget.
 * x = order_month (ascending, labelled "Jan 2018"), y = metric
 * Dots on data points, tooltip with formatted values.
 */

import {
  Area,
  CartesianGrid,
  ComposedChart,
  Dot,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { formatMonth, formatValue } from "../lib/format";
import type { FilterDef, Widget } from "../lib/types";
import { useAppStore } from "../state/store";
import { useWidgetData, WidgetCard } from "./widgetBase";

const PRIMARY = "#4F46E5";

interface LineChartWidgetProps {
  widget: Widget;
  pageFilters: FilterDef[];
  datasetFieldNames: string[];
}

export function LineChartWidget({
  widget,
  pageFilters,
  datasetFieldNames,
}: LineChartWidgetProps) {
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
  const dimKey = widget.dimensions[0] ?? "order_month";

  const chartData = (data?.rows ?? []).map((row) => ({
    month: formatMonth(String(row[dimKey] ?? "")),
    value: Number(row[metricId] ?? 0),
  }));

  return (
    <WidgetCard
      widgetId={widget.widget_id}
      title={widget.title}
      loading={loading}
      error={error}
      empty={isEmpty}
    >
      <ResponsiveContainer width="100%" height={260}>
        <ComposedChart
          data={chartData}
          margin={{ left: 0, right: 16, top: 12, bottom: 4 }}
        >
          <defs>
            <linearGradient id={`grad-${widget.widget_id}`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor={PRIMARY} stopOpacity={0.15} />
              <stop offset="95%" stopColor={PRIMARY} stopOpacity={0.0} />
            </linearGradient>
          </defs>
          <CartesianGrid stroke="#F1F5F9" strokeDasharray="3 3" vertical={false} />
          <XAxis
            dataKey="month"
            tick={{ fontSize: 11, fill: "#64748B" }}
            axisLine={false}
            tickLine={false}
            interval="preserveStartEnd"
          />
          <YAxis
            tick={{ fontSize: 11, fill: "#64748B" }}
            axisLine={false}
            tickLine={false}
            tickFormatter={(v: number) => formatValue(v, unit)}
            width={72}
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
          <Area
            type="monotone"
            dataKey="value"
            fill={`url(#grad-${widget.widget_id})`}
            stroke="none"
          />
          <Line
            type="monotone"
            dataKey="value"
            stroke={PRIMARY}
            strokeWidth={2.5}
            dot={<Dot r={3.5} fill={PRIMARY} stroke="#fff" strokeWidth={2} />}
            activeDot={{ r: 6, fill: PRIMARY, stroke: "#fff", strokeWidth: 2 }}
          />
        </ComposedChart>
      </ResponsiveContainer>
    </WidgetCard>
  );
}
