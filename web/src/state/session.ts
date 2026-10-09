/**
 * Session orchestration: boot, the WebSocket protocol, state changes (user or agent), acknowledgements, URL sync.
 *
 *   backend --apply_state--> browser   state to show (agent actions, undo)        -> we render, then
 *   browser --render_ack---> backend   what each widget actually requested/showed  -> the verifier compares
 *   browser --user_state_change--> backend   the user clicked something
 */
import type { AgentEvent, FilterValue, Page, Sort, UiState, WidgetAck, DateRange } from '../lib/types'
import { buildRequest, requestKey } from '../lib/request'
import { fromUrl, stateForPage, toUrl } from '../lib/url'
import { api, onApiCount } from '../net/api'
import { getStore, toast, useStore } from './store'

let socket: WebSocket | null = null
let reconnectTimer: number | undefined

const send = (msg: unknown) => {
  if (socket && socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify(msg))
}

// ─── URL sync ──────────────────────────────────────────────────────────────────
function pushUrl(state: UiState, mode: 'push' | 'replace') {
  const app = getStore().app
  if (!app) return
  const url = toUrl(app, state)
  if (location.pathname + location.search === url) return
  history[mode === 'push' ? 'pushState' : 'replaceState'](null, '', url)
}

function applyLocationAsUser() {
  const { app, presets } = getStore()
  if (!app) return
  const parsed = fromUrl(app, location.pathname + location.search, presets)
  if (parsed.state) {
    if (parsed.errors.length) toast('info', 'Parts of that link were ignored: ' + parsed.errors.slice(0, 2).join('; '))
    commit(parsed.state, false)
  }
}

// ─── state changes ─────────────────────────────────────────────────────────────
function commit(next: UiState, push: boolean) {
  const cur = getStore().state
  const withVersion = { ...next, version: (cur?.version ?? 0) + 1 }
  useStore.setState({ state: withVersion })
  if (push) pushUrl(withVersion, 'push')
  if (socket && socket.readyState === WebSocket.OPEN) send({ type: 'user_state_change', state: withVersion })
  else { const sid = getStore().sessionId; if (sid) api.putState(sid, withVersion).catch(() => {}) }
}

export function goToPage(pageId: string, opts: { filters?: Record<string, FilterValue>; date_range?: DateRange | null; sort?: Sort | null } = {}) {
  const { app, presets } = getStore()
  const page = app?.pages.find((p) => p.page_id === pageId)
  if (!page) return
  commit(stateForPage(page, presets, opts.filters ?? {}, opts.date_range ?? null, opts.sort ?? null), true)
}

export function openUrl(url: string) {
  const { app, presets } = getStore()
  if (!app) return
  const parsed = fromUrl(app, url, presets)
  if (!parsed.state) { toast('error', parsed.errors[0] ?? 'Unknown link'); return }
  if (parsed.errors.length) toast('info', 'Parts of that link were ignored: ' + parsed.errors.slice(0, 2).join('; '))
  commit(parsed.state, true)
}

function update(fn: (s: UiState) => UiState) {
  const cur = getStore().state
  if (cur) commit(fn(cur), true)
}

export const setFilter = (fid: string, fv: FilterValue | null) => update((s) => {
  const filters = { ...s.filters }
  if (fv && fv.value.length) filters[fid] = fv
  else delete filters[fid]
  return { ...s, filters }
})
export const clearFilters = () => update((s) => ({ ...s, filters: {} }))
export const setDateRange = (date_range: DateRange | null) => update((s) => ({ ...s, date_range }))
export const setSort = (sort: Sort | null) => update((s) => ({ ...s, sort }))
export const selectWidget = (id: string | null) => update((s) => ({ ...s, selected_widget: id }))

export function currentPageOf(state: UiState | null): Page | undefined {
  return getStore().app?.pages.find((p) => p.page_id === state?.page_id)
}

// ─── acknowledgements ──────────────────────────────────────────────────────────
const ACK_WAIT_MS = 5000

function collectAcks(st: UiState): Promise<WidgetAck[]> {
  const { app } = getStore()
  const page = app!.pages.find((p) => p.page_id === st.page_id)!
  const expect = page.widgets.map((wid) => {
    const w = app!.widgets.find((x) => x.widget_id === wid)!
    return [wid, requestKey(buildRequest(app!, page, w, st))] as const
  })
  return new Promise((resolve) => {
    let unsub = () => {}
    const finish = (timedOut: boolean): boolean => {
      const { acks } = getStore()
      const ready = expect.every(([wid, key]) => acks[wid]?.key === key)
      if (!ready && !timedOut) return false
      unsub(); clearTimeout(timer)
      resolve(expect.map(([wid, key]) => acks[wid]?.key === key ? acks[wid].ack : {
        widget_id: wid, applied_filters: {}, applied_date_range: null, applied_sort: null,
        row_count: 0, series_hash: '', render_ms: ACK_WAIT_MS, error: 'timeout',
      }))
      return true
    }
    const timer = window.setTimeout(() => finish(true), ACK_WAIT_MS)
    unsub = useStore.subscribe(() => { finish(false) })
    finish(false)
  })
}

async function handleApplyState(msg: { version: number; nonce: string; state: UiState; cause?: string }) {
  useStore.setState({ state: msg.state })
  pushUrl(msg.state, msg.cause === 'connect' ? 'replace' : 'push')
  const widgets = await collectAcks(msg.state)
  send({ type: 'render_ack', version: msg.version, nonce: msg.nonce, route: msg.state.route, widgets })
}

// ─── events from the agent runtime ─────────────────────────────────────────────
function handleEvent(ev: AgentEvent) {
  useStore.setState((s) => {
    const patch: Partial<ReturnType<typeof getStore>> = { events: [...s.events, ev].slice(-500) }
    if (ev.type === 'canvas') patch.canvases = [...s.canvases.filter((c) => c.canvas_id !== ev.data.canvas_id), ev.data as any]
    if (ev.type === 'verify') patch.lastVerify = ev.data as any
    return patch
  })
}

// ─── socket ────────────────────────────────────────────────────────────────────
function openSocket(sid: string) {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
  useStore.setState({ link: 'connecting' })
  socket = new WebSocket(`${proto}//${location.host}/ws/${sid}`)
  socket.onopen = () => useStore.setState({ link: 'open' })
  socket.onmessage = (e) => {
    let msg: any
    try { msg = JSON.parse(e.data) } catch { return }
    if (msg.type === 'apply_state') void handleApplyState(msg)
    else if (msg.type === 'agent_event') handleEvent(msg.event)
  }
  socket.onclose = () => {
    useStore.setState({ link: 'closed' })
    socket = null
    reconnectTimer = window.setTimeout(() => { if (getStore().sessionId === sid) openSocket(sid) }, 2000)
  }
  socket.onerror = () => socket?.close()
}

export function newSession(): Promise<void> {
  window.clearTimeout(reconnectTimer)
  socket?.close()
  useStore.setState({ events: [], canvases: [], acks: {} })
  return startSession()
}

async function startSession() {
  const { app, presets } = getStore()
  const sess = await api.createSession()
  let desired: UiState = sess.state
  const atRoot = location.pathname === '/' || location.pathname === ''
  if (!atRoot) {  // opened from a deep link: the link's state wins over the server's default
    const parsed = fromUrl(app!, location.pathname + location.search, presets)
    if (parsed.state) {
      if (parsed.errors.length) toast('info', 'Parts of that link were ignored: ' + parsed.errors.slice(0, 2).join('; '))
      desired = ((await api.putState(sess.session_id, parsed.state)).state as UiState) ?? parsed.state
    }
  }
  useStore.setState({ sessionId: sess.session_id, state: desired })
  pushUrl(desired, 'replace')
  openSocket(sess.session_id)
}

let booting: Promise<void> | null = null

export function boot(): Promise<void> {
  booting ??= doBoot()  // React StrictMode mounts twice in dev: boot once
  return booting
}

async function doBoot() {
  try {
    const [app, presets, catalog] = await Promise.all([api.application(), api.presets(), api.catalog()])
    useStore.setState({ app, presets, catalog })
    onApiCount((n) => useStore.setState({ apiCalls: n }))
    await startSession()
    window.addEventListener('popstate', applyLocationAsUser)
    useStore.setState({ boot: 'ready' })
  } catch (e) {
    useStore.setState({ boot: 'error', bootError: e instanceof Error ? e.message : String(e) })
  }
}
