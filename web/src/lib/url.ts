/**
 * UI state <-> deep link. TypeScript twin of backend/deeplinks.py; both are tested against
 * contracts/deeplink_vectors.json. Scheme:
 *
 *   <route>?from=2018-07-01&to=2018-07-31&preset=last_month&f.customer_state=RJ,SP&op.<id>=<op>&sort=field:dir
 *
 * Values are percent-encoded with RFC 3986 rules (so a comma inside a value is %2C and commas separate list items);
 * parameters are written in a fixed order so the same state always yields the same URL. A link without dates opens
 * the page defaults (shared semantics S1).
 */
import type { Application, DateRange, FilterDef, FilterOp, FilterValue, Page, Sort, UiState } from './types'

export type PresetMap = Record<string, { from: string; to: string }>

const DEFAULT_OP: Record<string, FilterOp> = { multiselect: 'in', select: 'eq', text: 'contains', number_range: 'between' }

export function defaultOp(f?: FilterDef): FilterOp {
  return f ? (DEFAULT_OP[f.type] ?? 'eq') : 'in'
}

/** encodeURIComponent also leaves ! ' ( ) * alone; Python's quote(safe="") encodes them. */
export function enc(v: string): string {
  return encodeURIComponent(v).replace(/[!'()*]/g, (c) => '%' + c.charCodeAt(0).toString(16).toUpperCase())
}

export function toUrl(app: Application, state: UiState): string {
  const page = app.pages.find((p) => p.page_id === state.page_id)
  if (!page) return state.route
  const pairs: [string, string][] = []
  if (state.date_range) {
    pairs.push(['from', enc(state.date_range.from)], ['to', enc(state.date_range.to)])
    if (state.date_range.preset) pairs.push(['preset', enc(state.date_range.preset)])
  }
  for (const fid of Object.keys(state.filters).sort()) {
    const fv = state.filters[fid]
    pairs.push([`f.${fid}`, fv.value.map(enc).join(',')])
    if (fv.op !== defaultOp(page.filters.find((f) => f.filter_id === fid))) pairs.push([`op.${fid}`, fv.op])
  }
  if (state.sort) pairs.push(['sort', `${enc(state.sort.field)}:${state.sort.dir}`])
  return page.route + (pairs.length ? '?' + pairs.map(([k, v]) => `${k}=${v}`).join('&') : '')
}

function splitUrl(url: string): { path: string; params: Map<string, string> } {
  let u = url.trim()
  if (u.includes('://')) u = '/' + u.split('://')[1].split('/').slice(1).join('/')
  u = u.split('#')[0]
  const q = u.indexOf('?')
  const path = (q >= 0 ? u.slice(0, q) : u).replace(/\/+$/, '') || '/'
  const params = new Map<string, string>()
  for (const pair of (q >= 0 ? u.slice(q + 1) : '').split('&').filter(Boolean)) {
    const i = pair.indexOf('=')
    const k = decodeURIComponent(i >= 0 ? pair.slice(0, i) : pair)
    params.set(k, i >= 0 ? pair.slice(i + 1) : '')
  }
  return { path, params }
}

export function pageForRoute(app: Application, route: string): Page | undefined {
  const path = splitUrl(route).path
  return app.pages.find((p) => p.route === path)
}

export function defaultDateRange(page: Page, presets: PresetMap): DateRange | null {
  const preset = page.default_state?.date_range?.preset
  const r = preset ? presets[preset] : undefined
  return r ? { from: r.from, to: r.to, preset } : null
}

export function stateForPage(page: Page, presets: PresetMap, filters: Record<string, FilterValue> = {},
                             date_range: DateRange | null = null, sort: Sort | null = null): UiState {
  return { route: page.route, page_id: page.page_id, filters, date_range: date_range ?? defaultDateRange(page, presets),
           sort, selected_widget: null, version: 0 }
}

function canon(f: FilterDef, raw: string): string | null {
  if (!f.allowed_values?.length) return raw
  if (f.allowed_values.includes(raw)) return raw
  const low = raw.toLowerCase()
  const hit = f.allowed_values.find((a) => a.toLowerCase() === low)
  if (hit) return hit
  for (const [c, aliases] of Object.entries(f.synonyms ?? {})) {
    if (c.toLowerCase() === low || aliases.some((a) => a.toLowerCase() === low)) return c
  }
  return null
}

export interface ParsedUrl { state: UiState | null; errors: string[] }

/** Lenient parse for the address bar: unknown pieces are dropped and reported; the backend validates strictly. */
export function fromUrl(app: Application, url: string, presets: PresetMap): ParsedUrl {
  const { path, params } = splitUrl(url)
  const page = app.pages.find((p) => p.route === path)
  if (!page) return { state: null, errors: [`No page has the route ${path}`] }
  const errors: string[] = []
  const filters: Record<string, FilterValue> = {}
  let sort: Sort | null = null
  let date_range: DateRange | null = null
  for (const [key, raw] of params) {
    if (key.startsWith('f.')) {
      const fid = key.slice(2)
      const f = page.filters.find((x) => x.filter_id === fid)
      if (!f) { errors.push(`Unknown filter ${fid}`); continue }
      const vals: string[] = []
      for (const part of raw.split(',').filter((x) => x !== '')) {
        const c = canon(f, decodeURIComponent(part))
        if (c === null) errors.push(`'${decodeURIComponent(part)}' is not a value of ${f.title}`)
        else vals.push(c)
      }
      if (vals.length) {
        const op = (params.get(`op.${fid}`) as FilterOp | undefined) ?? defaultOp(f)
        filters[fid] = { op, value: vals }
      }
    } else if (key === 'sort') {
      const [field, dir] = decodeURIComponent(raw).split(':')
      if (field) sort = { field, dir: dir === 'asc' ? 'asc' : 'desc' }
    } else if (!['from', 'to', 'preset'].includes(key) && !key.startsWith('op.')) {
      errors.push(`Unknown link parameter ${key}`)
    }
  }
  const hasDate = page.filters.some((f) => f.type === 'date_range')
  if (params.has('from') && params.has('to') && hasDate) {
    date_range = { from: decodeURIComponent(params.get('from')!), to: decodeURIComponent(params.get('to')!),
                   preset: params.has('preset') ? decodeURIComponent(params.get('preset')!) : null }
  } else if (params.has('preset') && hasDate) {
    const p = decodeURIComponent(params.get('preset')!)
    if (presets[p]) date_range = { ...presets[p], preset: p }
    else errors.push(`Unknown date preset ${p}`)
  }
  return { state: stateForPage(page, presets, filters, date_range, sort), errors }
}
