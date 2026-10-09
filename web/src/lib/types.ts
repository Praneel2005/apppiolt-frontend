/**
 * Types mirroring the backend contracts (contracts/metadata.py v0.2, ui_state.py, canvas.py, events.py)
 * and the API catalogue entry. Keep in sync with the Python models; the backend is the source of truth.
 */

// ─── metadata (GET /api/application) ───────────────────────────────────────────
export interface FieldDef {
  name: string
  type: 'date' | 'string' | 'enum' | 'integer' | 'decimal' | 'boolean'
  role: 'time' | 'dimension' | 'metric' | 'id' | 'attribute'
  description: string
  values?: string[] | null
}
export interface Metric {
  metric_id: string
  title: string
  description: string
  sql: string
  unit: string
  higher_is_better: boolean
  additive: boolean
  weight_sql?: string | null
}
export interface Dataset {
  dataset_id: string
  table: string
  description: string
  time_field?: string | null
  fields: FieldDef[]
  metrics: Metric[]
}
export interface FilterDef {
  filter_id: string
  field: string
  type: 'date_range' | 'select' | 'multiselect' | 'text' | 'number_range'
  title: string
  allowed_ops: string[]
  allowed_values?: string[] | null
  synonyms: Record<string, string[]>
}
export type ColumnType = 'text' | 'number' | 'money' | 'percent' | 'ratio' | 'date' | 'datetime' | 'badge' | 'list'
export interface Column {
  name: string
  title: string
  type: ColumnType
  unit: string
  description: string
  hidden: boolean
}
export interface ChartSpec {
  x: string
  y: string[]
  stacked: boolean
}
export type Scalar = string | number | boolean | null
export interface Action {
  action_id: string
  api_id: string
  label: string
  description: string
  params_from_row: Record<string, string>
  fixed: Record<string, Scalar>
  form: boolean
  style: 'default' | 'danger'
}
export interface WidgetSource {
  api_id: string
  params: Record<string, Scalar>
  filter_params: Record<string, string>
  date_params: { from: string; to: string } | null
  sort_param: string | null
  items_path: string
  columns: Column[]
  chart: ChartSpec | null
  row_actions: Action[]
}
export interface Widget {
  widget_id: string
  widget_code: string | null
  type: 'grid' | 'bar_chart' | 'line_chart' | 'pie_chart' | 'kpi_card' | 'filter_bar'
  title: string
  description: string
  dataset_id: string | null
  metrics: string[]
  dimensions: string[]
  supports: ('sort' | 'filter' | 'date_range' | 'drilldown')[]
  source: WidgetSource | null
}
export interface Page {
  page_id: string
  page_code: string | null
  kind: 'report' | 'operations'
  title: string
  description: string
  agent_context: string
  directory: string
  route: string
  keywords: string[]
  widgets: string[]
  layout: string[][] | null
  filters: FilterDef[]
  default_state: { date_range?: { preset?: string } } & Record<string, unknown>
  actions: Action[]
}
export interface Directory { path: string; title: string; description: string }
export interface Module { module_id: string; title: string; description: string; section: 'operations' | 'reports' }
export interface Application {
  schema_version: string
  app_id: string
  name: string
  as_of_date: string | null
  modules: Module[]
  directories: Directory[]
  pages: Page[]
  widgets: Widget[]
  datasets: Dataset[]
}

// ─── UI state and protocol (contracts/ui_state.py) ─────────────────────────────
export interface DateRange { from: string; to: string; preset?: string | null }
export interface Sort { field: string; dir: 'asc' | 'desc' }
export type FilterOp = 'eq' | 'in' | 'between' | 'contains' | 'gte' | 'lte'
export interface FilterValue { op: FilterOp; value: string[] }
export interface UiState {
  route: string
  page_id: string
  filters: Record<string, FilterValue>
  date_range: DateRange | null
  sort: Sort | null
  selected_widget: string | null
  version: number
}
export interface WidgetAck {
  widget_id: string
  applied_filters: Record<string, FilterValue>
  applied_date_range: DateRange | null
  applied_sort: Sort | null
  row_count: number
  series_hash: string
  render_ms: number
  error: string | null
}

// ─── widget data (POST /api/widget-data) ───────────────────────────────────────
export interface WidgetRequest {
  widget_id: string
  page_id: string
  filters: Record<string, FilterValue>
  date_range: DateRange | null
  sort: Sort | null
}
export interface WidgetData {
  columns: string[]
  hash_columns: string[]
  rows: Record<string, unknown>[]
  row_count: number
  total: number | null
  kind: 'metric' | 'api'
}

// ─── API catalogue (GET /api/catalog) ──────────────────────────────────────────
export interface CatalogParam {
  name: string
  in: 'query' | 'path'
  required: boolean
  type: string | string[]
  description: string
  default?: unknown
  enum?: string[]
}
export interface JsonSchema {
  type?: string | string[]
  properties?: Record<string, JsonSchema & { title?: string; description?: string; enum?: unknown[]; anyOf?: JsonSchema[]; items?: JsonSchema; format?: string; default?: unknown; minimum?: number; maximum?: number }>
  required?: string[]
}
export interface CatalogEntry {
  api_id: string
  method: 'GET' | 'POST' | 'PATCH' | 'PUT' | 'DELETE'
  path: string
  title: string
  description: string
  entity: string
  kind: 'read' | 'write'
  data_layers: string[]
  requires_confirmation: boolean
  supports_dry_run: boolean
  undoable: boolean
  parameters: CatalogParam[]
  body_schema: JsonSchema | null
}

// ─── write results ─────────────────────────────────────────────────────────────
export interface RowChange {
  table: string
  pk: unknown
  op: 'insert' | 'update'
  before: Record<string, unknown> | null
  after: Record<string, unknown>
  changed?: string[]
}
export interface WriteResult {
  status: 'preview' | 'applied'
  api_id: string
  action_id?: string
  result: Record<string, unknown>
  changes: RowChange[]
  message?: string
}
export interface ApiErrorBody { error: { code: string; message: string; did_you_mean?: string[]; allowed?: string[]; candidates?: string[]; [k: string]: unknown } }

// ─── agent events and canvas (contracts/events.py, canvas.py) ──────────────────
export interface AgentEvent { seq: number; type: string; data: Record<string, any> }
export interface FormSpec {
  api_id: string; method: string; path: string; title: string
  path_params: Record<string, unknown>; body_schema: JsonSchema; defaults: Record<string, unknown>
  submit_label: string; requires_confirmation: boolean
}
export interface CanvasSpec {
  canvas_id: string
  kind: 'chart' | 'table' | 'form'
  title: string
  subtitle: string
  chart_type: 'bar_chart' | 'line_chart' | null
  chart: ChartSpec | null
  columns: Column[]
  rows: Record<string, unknown>[]
  form: FormSpec | null
  evidence_ids: string[]
  data_layers: string[]
  note: string
}
