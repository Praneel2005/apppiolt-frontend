/**
 * Dynamic page renderer.
 *
 * Detects one of 4 page layout types from widget composition:
 *   Overview (≥1 kpi_card)        → row of KPI cards + full-width line chart
 *   Breakdown 1-dim (bar+grid, 1) → bar 60% left + table 40% right
 *   Breakdown 2-dim (grid, 2 dim) → bar full-width top + table below
 *   Trend (first widget line over order_month) → line full-width + table below
 */

import type { Application, Page, Widget } from "../lib/types";
import { BarChartWidget } from "../widgets/BarChartWidget";
import { GridWidget } from "../widgets/GridWidget";
import { KpiCardWidget } from "../widgets/KpiCardWidget";
import { LineChartWidget } from "../widgets/LineChartWidget";
import { FilterBar } from "./FilterBar";

interface PageRendererProps {
  page: Page;
  application: Application;
}

type PageType = "overview" | "breakdown1" | "breakdown2" | "trend";

function detectPageType(page: Page, widgets: Widget[]): PageType {
  const pageWidgets = page.widgets
    .map((id) => widgets.find((w) => w.widget_id === id))
    .filter(Boolean) as Widget[];

  if (pageWidgets.some((w) => w.type === "kpi_card")) return "overview";
  const lineFirst = pageWidgets[0]?.type === "line_chart";
  if (lineFirst) return "trend";
  const grids = pageWidgets.filter((w) => w.type === "grid");
  if (grids.some((w) => w.dimensions.length >= 2)) return "breakdown2";
  return "breakdown1";
}

export function PageRenderer({ page, application }: PageRendererProps) {
  const allWidgets = application.widgets;
  const datasets = application.datasets;

  const pageWidgets = page.widgets
    .map((id) => allWidgets.find((w) => w.widget_id === id))
    .filter(Boolean) as Widget[];

  const getDatasetFields = (widget: Widget) => {
    const ds = datasets.find((d) => d.dataset_id === widget.dataset_id);
    return (ds?.fields ?? []).map((f) => f.name);
  };

  const pageType = detectPageType(page, allWidgets);

  const kpiWidgets = pageWidgets.filter((w) => w.type === "kpi_card");
  const barWidget = pageWidgets.find((w) => w.type === "bar_chart");
  const lineWidget = pageWidgets.find((w) => w.type === "line_chart");
  const gridWidgets = pageWidgets.filter((w) => w.type === "grid");

  function renderWidget(widget: Widget) {
    const fields = getDatasetFields(widget);
    const props = {
      widget,
      pageFilters: page.filters,
      datasetFieldNames: fields,
    };
    switch (widget.type) {
      case "kpi_card":
        return <KpiCardWidget key={widget.widget_id} {...props} />;
      case "bar_chart":
        return <BarChartWidget key={widget.widget_id} {...props} />;
      case "line_chart":
        return <LineChartWidget key={widget.widget_id} {...props} />;
      case "grid":
        return <GridWidget key={widget.widget_id} {...props} />;
      default:
        return null;
    }
  }

  return (
    <div className="flex flex-col gap-6 p-6 flex-1 overflow-y-auto bg-[#F8FAFC] min-h-0">
      {/* Breadcrumb */}
      <div className="text-xs text-slate-400">
        {page.directory.replace(/\//g, " › ")}
      </div>

      {/* Page header */}
      <div>
        <h1 className="text-[20px] font-semibold text-slate-900">{page.title}</h1>
        {page.description && (
          <p className="text-sm text-slate-500 mt-1">{page.description}</p>
        )}
      </div>

      {/* Filter bar */}
      {page.filters.length > 0 && (
        <FilterBar page={page} />
      )}

      {/* Widgets by layout type */}
      {pageType === "overview" && (
        <>
          {kpiWidgets.length > 0 && (
            <div
              className="grid gap-4"
              style={{
                gridTemplateColumns: `repeat(${Math.min(kpiWidgets.length, 4)}, 1fr)`,
              }}
            >
              {kpiWidgets.map(renderWidget)}
            </div>
          )}
          {lineWidget && (
            <div className="w-full">{renderWidget(lineWidget)}</div>
          )}
          {/* Any other non-kpi, non-line widgets */}
          {pageWidgets
            .filter((w) => w.type !== "kpi_card" && w.type !== "line_chart")
            .map(renderWidget)}
        </>
      )}

      {pageType === "trend" && (
        <>
          {lineWidget && (
            <div className="w-full">{renderWidget(lineWidget)}</div>
          )}
          {gridWidgets.map(renderWidget)}
        </>
      )}

      {pageType === "breakdown1" && (
        <div className="flex gap-4 flex-wrap xl:flex-nowrap">
          {barWidget && (
            <div className="flex-[3] min-w-0">{renderWidget(barWidget)}</div>
          )}
          {gridWidgets.length > 0 && (
            <div className="flex-[2] min-w-0">{gridWidgets.map(renderWidget)}</div>
          )}
        </div>
      )}

      {pageType === "breakdown2" && (
        <>
          {barWidget && (
            <div className="w-full">{renderWidget(barWidget)}</div>
          )}
          {gridWidgets.map(renderWidget)}
        </>
      )}
    </div>
  );
}
