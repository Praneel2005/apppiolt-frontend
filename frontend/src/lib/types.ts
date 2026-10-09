/**
 * TypeScript types derived from contracts/metadata.py and contracts/ui_state.py.
 * Keep in sync with the Python models — do NOT edit the contracts/ files.
 */

// ─── Metadata (application.json) ─────────────────────────────────────────────

export interface FieldDef {
  name: string;
  type: "date" | "string" | "enum" | "integer" | "decimal" | "boolean";
  role: "time" | "dimension" | "metric" | "id" | "attribute";
  description: string;
  values?: string[] | null;
}

export interface Metric {
  metric_id: string;
  title: string;
  description: string;
  sql: string;
  unit: string;
  higher_is_better: boolean;
  additive: boolean;
}

export interface Dataset {
  dataset_id: string;
  table: string;
  description: string;
  time_field?: string | null;
  fields: FieldDef[];
  metrics: Metric[];
}

export interface FilterDef {
  filter_id: string;
  field: string;
  type: "date_range" | "select" | "multiselect" | "text" | "number_range";
  title: string;
  allowed_ops: string[];
  allowed_values?: string[] | null;
  synonyms: Record<string, string[]>;
}

export interface RenderContract {
  query_metrics: string[];
  query_dimensions: string[];
  ack_fields: ("applied_filters" | "row_count" | "series_hash")[];
}

export interface Widget {
  widget_id: string;
  type: "grid" | "bar_chart" | "line_chart" | "pie_chart" | "kpi_card" | "filter_bar";
  title: string;
  description: string;
  dataset_id: string;
  metrics: string[];
  dimensions: string[];
  supports: ("sort" | "filter" | "date_range" | "drilldown")[];
  render?: RenderContract | null;
}

export interface Page {
  page_id: string;
  title: string;
  description: string;
  directory: string;
  route: string;
  keywords: string[];
  widgets: string[]; // widget_ids
  filters: FilterDef[];
  default_state: Record<string, unknown>;
}

export interface Directory {
  path: string;
  title: string;
  description: string;
}

export interface Module {
  module_id: string;
  title: string;
  description: string;
}

export interface Application {
  schema_version: string;
  app_id: string;
  name: string;
  source_format: string;
  as_of_date?: string | null;
  modules: Module[];
  directories: Directory[];
  pages: Page[];
  widgets: Widget[];
  datasets: Dataset[];
}

// ─── UI State (contracts/ui_state.py) ────────────────────────────────────────

export interface DateRange {
  from: string;
  to: string;
  preset?: string | null;
}

export interface Sort {
  field: string;
  dir: "asc" | "desc";
}

export interface FilterValue {
  op: "eq" | "in" | "between" | "contains" | "gte" | "lte";
  value: string[];
}

export interface UiState {
  route: string;
  page_id: string;
  filters: Record<string, FilterValue>;
  date_range?: DateRange | null;
  sort?: Sort | null;
  selected_widget?: string | null;
  version: number;
}

// ─── WebSocket messages ───────────────────────────────────────────────────────

export interface ApplyStateMsg {
  type: "apply_state";
  version: number;
  nonce: string;
  state: UiState;
  cause: string;
}

export interface WidgetAck {
  widget_id: string;
  applied_filters: Record<string, FilterValue>;
  applied_date_range?: DateRange | null;
  applied_sort?: Sort | null;
  row_count: number;
  series_hash: string;
  render_ms: number;
  error?: string | null;
}

export interface RenderAckMsg {
  type: "render_ack";
  version: number;
  nonce: string;
  route: string;
  widgets: WidgetAck[];
}

export interface UserStateChangeMsg {
  type: "user_state_change";
  state: UiState;
}

export interface AgentEvent {
  seq: number;
  type: string;
  data: Record<string, unknown>;
}

export interface AgentEventMsg {
  type: "agent_event";
  event: AgentEvent;
}

// ─── API responses ────────────────────────────────────────────────────────────

export interface WidgetDataResponse {
  columns: string[];
  rows: Record<string, unknown>[];
  row_count: number;
}

export interface SessionResponse {
  session_id: string;
  state: UiState;
}

export interface DatePresetsResponse {
  [preset: string]: { from: string; to: string };
}
