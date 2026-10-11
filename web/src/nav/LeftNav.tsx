import { useMemo, useState } from 'react'
import { canOpen } from '../lib/access'
import type { Application, Module, Page } from '../lib/types'
import { goToPage } from '../state/session'
import { useStore } from '../state/store'

function PageLink({ page, active }: { page: Page; active: boolean }) {
  return (
    <a href={page.route} onClick={(e) => { if (e.metaKey || e.ctrlKey) return; e.preventDefault(); goToPage(page.page_id) }}
      className={`flex items-center justify-between gap-2 px-3 py-1.5 rounded-lg text-[13px] border-l-2 transition-colors ${active ? 'bg-white/12 text-white font-medium border-accent' : 'text-white/75 hover:bg-white/8 hover:text-white border-transparent'}`}>
      <span className="truncate">{page.title}</span>
      {page.page_code && <span className="text-[10px] font-mono text-white/40 shrink-0">{page.page_code}</span>}
    </a>
  )
}

function dirTitle(app: Application, path: string, mod: Module): string {
  const leaf = path.split('/')[1]
  return leaf ? leaf.charAt(0).toUpperCase() + leaf.slice(1) : mod.title
}

export function LeftNav() {
  const app = useStore((s) => s.app)!
  const activeId = useStore((s) => s.state?.page_id)
  const role = useStore((s) => s.role)
  const [q, setQ] = useState('')
  const [open, setOpen] = useState<Record<string, boolean>>({})
  const [reportsOpen, setReportsOpen] = useState(false)

  const byModule = useMemo(() => {
    const m = new Map<string, Page[]>()
    for (const p of app.pages) {
      if (!canOpen(role, p)) continue                  // a role sees only the pages it may open
      const key = p.directory.split('/')[0]
      m.set(key, [...(m.get(key) ?? []), p])
    }
    return m
  }, [app, role])

  const term = q.trim().toLowerCase()
  const match = (p: Page) => !term || `${p.title} ${p.page_code} ${p.keywords.join(' ')}`.toLowerCase().includes(term)
  const ops = app.modules.filter((m) => m.section === 'operations')
  const reports = app.modules.filter((m) => m.section === 'reports')
  const activeModule = app.pages.find((p) => p.page_id === activeId)?.directory.split('/')[0]

  return (
    <aside className="w-64 shrink-0 bg-primary-deep text-white flex flex-col h-full">
      <div className="px-4 pt-5 pb-4 border-b border-white/10">
        <div className="flex items-center gap-2">
          <div className="w-8 h-8 rounded-xl bg-gradient-to-br from-accent to-[#d9b45a] grid place-items-center text-primary-deep text-sm font-bold shadow-sm">A</div>
          <div><div className="text-sm font-semibold leading-4 tracking-tight">AppPilot</div><div className="text-[11px] text-white/55 leading-4">Olist Seller Operations</div></div>
        </div>
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Find a page…  (e.g. P-300, tickets)"
          className="mt-4 w-full bg-white/8 border border-white/15 text-white placeholder:text-white/45 rounded-lg px-3 py-2 text-[13px] focus:outline-none focus:ring-2 focus:ring-accent/50 focus:bg-white/12" />
      </div>
      <nav className="flex-1 overflow-auto scroll-dark px-2.5 py-3 space-y-4">
        {ops.map((m) => {
          const pages = (byModule.get(m.module_id) ?? []).filter(match)
          if (!pages.length) return null
          return (
            <div key={m.module_id}>
              <div className="px-3 pb-1.5 text-[10.5px] font-semibold uppercase tracking-[0.08em] text-white/40">{m.title}</div>
              {pages.map((p) => <PageLink key={p.page_id} page={p} active={p.page_id === activeId} />)}
            </div>
          )
        })}
        <div>
          <button className="w-full px-3 pb-1.5 flex items-center justify-between text-[10.5px] font-semibold uppercase tracking-[0.08em] text-white/40 hover:text-white/70"
            onClick={() => setReportsOpen(!reportsOpen)}>
            <span>Reports library</span><span>{reportsOpen || term ? '▾' : '▸'}</span>
          </button>
          {(reportsOpen || term || reports.some((m) => m.module_id === activeModule)) && reports.map((m) => {
            const pages = (byModule.get(m.module_id) ?? []).filter(match)
            if (!pages.length) return null
            const isOpen = open[m.module_id] ?? (m.module_id === activeModule || !!term)
            return (
              <div key={m.module_id} className="mb-1">
                <button onClick={() => setOpen({ ...open, [m.module_id]: !isOpen })}
                  className="w-full flex items-center justify-between px-3 py-1.5 text-[13px] font-medium text-white/90 hover:bg-white/8 rounded-lg">
                  <span>{m.title}</span><span className="text-xs text-white/40">{pages.length} {isOpen ? '▾' : '▸'}</span>
                </button>
                {isOpen && (
                  <div className="ml-3 mt-0.5 border-l border-white/12 pl-1.5 space-y-0.5">
                    {pages.map((p) => <PageLink key={p.page_id} page={p} active={p.page_id === activeId} />)}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      </nav>
    </aside>
  )
}
