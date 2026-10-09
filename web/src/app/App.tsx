import { useEffect, useState } from 'react'
import { ActionDialog } from '../actions/ActionDialog'
import { Toasts } from '../actions/Toasts'
import { AgentPanel } from '../agent/AgentPanel'
import { CanvasArea } from '../agent/CanvasView'
import { LeftNav } from '../nav/LeftNav'
import { PageView } from '../pages/PageView'
import { boot } from '../state/session'
import { useStore } from '../state/store'
import { TopBar } from './TopBar'

export function App() {
  const bootState = useStore((s) => s.boot)
  const error = useStore((s) => s.bootError)
  const app = useStore((s) => s.app)
  const pageId = useStore((s) => s.state?.page_id)
  const [agentOpen, setAgentOpen] = useState(false)
  useEffect(() => { void boot() }, [])

  if (bootState === 'error') {
    return (
      <div className="h-full grid place-items-center p-6">
        <div className="max-w-md bg-surface border border-border rounded-xl p-6 shadow-sm">
          <h1 className="text-lg font-semibold text-danger">Can’t reach the backend</h1>
          <p className="text-sm text-muted mt-2">{error}</p>
          <p className="text-sm mt-3">Start it with <code className="bg-page px-1 rounded">uvicorn backend.main:app --port 8000</code> from the repo root, then reload.</p>
          <button className="mt-4 px-3 py-1.5 text-sm rounded-lg bg-primary text-white" onClick={() => location.reload()}>Reload</button>
        </div>
      </div>
    )
  }
  if (bootState === 'loading' || !app) return <div className="h-full grid place-items-center text-muted">Loading application…</div>
  const page = app.pages.find((p) => p.page_id === pageId)

  return (
    <div className="h-full flex">
      <LeftNav />
      <div className="flex-1 min-w-0 flex flex-col">
        <TopBar agentOpen={agentOpen} onToggleAgent={() => setAgentOpen(!agentOpen)} />
        <main className="flex-1 overflow-auto scroll-thin">
          <CanvasArea />
          {page ? <PageView key={page.page_id} page={page} /> : <div className="p-6 text-muted">Unknown page.</div>}
        </main>
      </div>
      {agentOpen && <AgentPanel onClose={() => setAgentOpen(false)} />}
      <ActionDialog />
      <Toasts />
    </div>
  )
}
