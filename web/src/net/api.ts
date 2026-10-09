/** Thin client for the AppPilot backend. Every call is counted (the "API calls" indicator in the top bar). */
import type {
  ApiErrorBody, Application, CatalogEntry, UiState, WidgetData, WidgetRequest, WriteResult,
} from '../lib/types'
import type { PresetMap } from '../lib/url'

export class ApiError extends Error {
  status: number
  code: string
  body: ApiErrorBody['error']
  constructor(status: number, body: ApiErrorBody['error']) {
    super(body.message)
    this.status = status
    this.code = body.code
    this.body = body
  }
}

let counter = 0
const listeners = new Set<(n: number) => void>()
export const onApiCount = (fn: (n: number) => void) => { listeners.add(fn); return () => { listeners.delete(fn) } }
const bump = () => { counter += 1; listeners.forEach((f) => f(counter)) }

async function request<T>(method: string, url: string, body?: unknown, headers: Record<string, string> = {}): Promise<T> {
  bump()
  const res = await fetch(url, {
    method,
    headers: { ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}), ...headers },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  const text = await res.text()
  let data: any = null
  try { data = text ? JSON.parse(text) : null } catch { /* non-JSON error page */ }
  if (!res.ok) {
    throw new ApiError(res.status, data?.error ?? { code: 'http_' + res.status, message: text.slice(0, 200) || res.statusText })
  }
  return data as T
}

const qs = (params: Record<string, unknown>) => {
  const p = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== '') p.set(k, String(v))
  const s = p.toString()
  return s ? '?' + s : ''
}

export const api = {
  health: () => request<{ status: string; as_of_date: string; sim_now: string }>('GET', '/api/health'),
  application: () => request<Application>('GET', '/api/application'),
  presets: () => request<PresetMap>('GET', '/api/date-presets'),
  catalog: async () => {
    const r = await request<{ items: CatalogEntry[] }>('GET', '/api/catalog?limit=500')
    return Object.fromEntries(r.items.map((e) => [e.api_id, e])) as Record<string, CatalogEntry>
  },
  widgetData: (req: WidgetRequest) => request<WidgetData>('POST', '/api/widget-data', req),

  createSession: () => request<{ session_id: string; state: UiState }>('POST', '/api/session'),
  putState: (sid: string, state: UiState) => request<{ state: UiState }>('POST', `/api/session/${sid}/state`, state),
  context: (sid: string) => request<any>('GET', `/api/session/${sid}/context`),
  undoState: (sid: string) => request<any>('POST', `/api/session/${sid}/undo`, { what: 'state' }),
  undoWrite: (sid: string) => request<any>('POST', `/api/session/${sid}/undo`, { what: 'write' }),
  runPlan: (sid: string, plan: unknown, autoConfirm = false) =>
    request<any>('POST', `/api/session/${sid}/plan`, { plan, auto_confirm: autoConfirm }),
  confirm: (sid: string, actionId: string, approve: boolean) =>
    request<{ ok: boolean }>('POST', `/api/session/${sid}/confirm`, { action_id: actionId, approve }),
  message: (sid: string, text: string, confirmMode: boolean) =>
    request<{ accepted: boolean }>('POST', `/api/session/${sid}/message`, { text, config: { confirm_mode: confirmMode } }),
  setFault: (sid: string, kind: string) => request<{ fault: string }>('POST', `/api/session/${sid}/fault`, { kind }),
  search: (q: string) => request<{ results: any[] }>('GET', '/api/search' + qs({ q, k: 6 })),

  /** Call a catalogue write API directly (a user clicking an action). `dryRun` returns the preview only. */
  callWrite: (entry: CatalogEntry, values: Record<string, unknown>, dryRun: boolean) => {
    let path = entry.path
    const query: Record<string, unknown> = dryRun ? { dry_run: 'true' } : {}
    const body: Record<string, unknown> = {}
    const pathNames = new Set(entry.parameters.filter((p) => p.in === 'path').map((p) => p.name))
    const bodyNames = new Set(Object.keys(entry.body_schema?.properties ?? {}))
    for (const [k, v] of Object.entries(values)) {
      if (v === undefined || v === '' || v === null) continue
      if (pathNames.has(k)) path = path.replace(`{${k}}`, encodeURIComponent(String(v)))
      else if (bodyNames.has(k)) body[k] = v
      else query[k] = v
    }
    return request<WriteResult>(entry.method, path + qs(query), entry.body_schema ? body : undefined, { 'x-actor': 'user' })
  },
  undoAction: (code: string) => request<{ status: string; action_id: string }>('POST', `/api/actions/${code}/undo`, undefined, { 'x-actor': 'user' }),
}
