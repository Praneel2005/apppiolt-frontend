import { formatUnit } from '../lib/format'
import type { Page, Widget } from '../lib/types'
import { useStore } from '../state/store'
import { useWidget, WidgetCard } from './widgetBase'

export function KpiCard({ widget, page }: { widget: Widget; page: Page }) {
  const app = useStore((s) => s.app)!
  const { data, loading, error } = useWidget(widget, page)
  const metricId = widget.metrics[0]
  const metric = app.datasets.find((d) => d.dataset_id === widget.dataset_id)?.metrics.find((m) => m.metric_id === metricId)
  const value = data?.rows[0]?.[metricId]
  return (
    <WidgetCard widget={widget} loading={loading} error={error}>
      <div className="text-[28px] leading-9 font-semibold text-ink tabular-nums">{formatUnit(value, metric?.unit ?? '')}</div>
      <div className="text-xs text-muted mt-1">{metric?.title ?? metricId}{metric?.unit && metric.unit !== 'count' && metric.unit !== 'ratio' && metric.unit !== 'score' ? ` · ${metric.unit}` : ''}</div>
    </WidgetCard>
  )
}
