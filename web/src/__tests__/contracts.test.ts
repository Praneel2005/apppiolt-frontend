/** The browser must agree with the backend on two things: the series hash and the deep-link format. */
import { createHash } from 'node:crypto'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'
import { canonicalSeriesHash, cell, sha256Hex } from '../lib/hash'
import { buildRequest } from '../lib/request'
import { fromUrl, toUrl, enc, type PresetMap } from '../lib/url'
import type { Application, UiState } from '../lib/types'

const root = (p: string) => fileURLToPath(new URL('../../../' + p, import.meta.url))
const read = (p: string) => JSON.parse(readFileSync(root(p), 'utf8'))

describe('series hash (contracts/hash_vectors.json)', () => {
  for (const c of read('contracts/hash_vectors.json').cases) {
    it(c.name, async () => expect(await canonicalSeriesHash(c.rows, c.columns)).toBe(c.hash))
  }
  it('uses half-up micro-unit rounding, never toFixed', () => {
    expect(cell(0.0078125)).toBe('7813')
    expect(cell(-0.0078125)).toBe('-7813')
    expect(cell(-0.0)).toBe('0')
  })
  it('pure-JS SHA-256 (fallback for non-secure origins) equals node:crypto for every length around block edges', () => {
    for (let n = 0; n <= 200; n++) {
      const bytes = new TextEncoder().encode('é'.repeat(Math.floor(n / 2)) + 'x'.repeat(n % 2) + 'ab'.repeat(n))
      expect(sha256Hex(bytes)).toBe(createHash('sha256').update(bytes).digest('hex'))
    }
  })
})

describe('deep links (contracts/deeplink_vectors.json)', () => {
  const app = read('data/olist/application.json') as Application
  const presets: PresetMap = { last_month: { from: '2018-07-01', to: '2018-07-31' }, last_quarter: { from: '2018-04-01', to: '2018-06-30' } }
  for (const c of read('contracts/deeplink_vectors.json').cases) {
    it(`state -> url: ${c.name}`, () => expect(toUrl(app, c.state as UiState)).toBe(c.url))
    it(`url -> state: ${c.name}`, () => {
      const r = fromUrl(app, c.url, presets)
      expect(r.errors).toEqual([])
      expect(r.state).toEqual(c.state)
    })
  }
  it('encodes exactly like Python quote(safe="")', () => {
    expect(enc("a b,c!d'e(f)g*h~i")).toBe('a%20b%2Cc%21d%27e%28f%29g%2Ah~i')
  })
  it('a link without dates opens the page defaults', () => {
    const r = fromUrl(app, '/ops/orders?f.customer_state=rj,', presets)
    expect(r.state!.date_range).toEqual({ from: '2018-07-01', to: '2018-07-31', preset: 'last_month' })
    expect(r.state!.filters.customer_state.value).toEqual(['RJ'])
  })
  it('drops unknown pieces and reports them', () => {
    const r = fromUrl(app, '/ops/orders?f.nope=1&f.customer_state=Atlantis&utm=x', presets)
    expect(r.errors.length).toBe(3)
    expect(r.state!.filters).toEqual({})
    expect(fromUrl(app, '/ops/missing', presets).state).toBeNull()
  })
})

describe('widget requests follow shared semantics S5 / S10', () => {
  const app = read('data/olist/application.json') as Application
  const page = (id: string) => app.pages.find((p) => p.page_id === id)!
  const widget = (id: string) => app.widgets.find((w) => w.widget_id === id)!
  const state = (page_id: string, extra: Partial<UiState>): UiState => ({
    route: page(page_id).route, page_id, filters: {}, date_range: null, sort: null, selected_widget: null, version: 1, ...extra,
  })

  it('an API widget receives a filter only if it maps it (S10)', () => {
    const st = state('ops.dashboard', { filters: { customer_state: { op: 'in', value: ['RJ'] } } })
    expect(buildRequest(app, page('ops.dashboard'), widget('ops.dashboard.open_tickets'), st).filters).toEqual({})
  })
  it('a metric widget receives a filter only if its dataset has the field (S5)', () => {
    const p = page('sales.revenue')
    const st = state(p.page_id, { filters: { customer_region: { op: 'in', value: ['South'] } }, date_range: { from: '2018-01-01', to: '2018-03-31' } })
    const req = buildRequest(app, p, widget('sales.revenue.by_customer_state'), st)
    expect(Object.keys(req.filters)).toEqual(['customer_region'])
    expect(req.date_range).toEqual({ from: '2018-01-01', to: '2018-03-31' })
  })
  it('date range and sort apply only where the widget supports them', () => {
    const w = widget('ops.restock.grid')
    expect(buildRequest(app, page('ops.restock_orders'), w, state('ops.restock_orders', { sort: { field: 'quantity', dir: 'asc' } })).sort).toBeNull()
    const g = widget('ops.orders.grid')
    const r = buildRequest(app, page('ops.orders'), g, state('ops.orders', { sort: { field: 'days_late', dir: 'desc' }, date_range: { from: '2018-07-01', to: '2018-07-31' } }))
    expect(r.sort).toEqual({ field: 'days_late', dir: 'desc' })
    expect(r.date_range?.from).toBe('2018-07-01')
  })
})
