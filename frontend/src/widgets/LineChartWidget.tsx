/**
 * Line Chart widget.
 * x = order_month (ascending, labelled "Jan 2018"), y = metric
 * Dots on data points, tooltip with formatted values.
 */

import {
  CartesianGrid,
  Dot,
  Line,
  LineChart,
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
      <ResponsiveContainer width="100%" height={240}>
        <LineChart
          data={chartData}
          margin={{ left: 0, right: 16, top: 8, bottom: 4 }}
        >
          <CartesianGrid stroke="#F1F5F9" strokeDasharray="4 2" />
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
            width={64}
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
          <Line
            type="monotone"
            dataKey="value"
            stroke={PRIMARY}
            strokeWidth={2}
            dot={<Dot r={3} fill={PRIMARY} stroke="#fff" strokeWidth={1.5} />}
            activeDot={{ r: 5 }}
          />
        </LineChart>
      </ResponsiveContainer>
    </WidgetCard>
  );
}
