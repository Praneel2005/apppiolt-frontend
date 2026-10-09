import { api } from '../net/api'
import { newSession } from '../state/session'
import { toast, useStore } from '../state/store'

export function TopBar({ onToggleAgent, agentOpen }: { onToggleAgent: () => void; agentOpen: boolean }) {
  const link = useStore((s) => s.link)
  const calls = useStore((s) => s.apiCalls)
  const verify = useStore((s) => s.lastVerify)
  const app = useStore((s) => s.app)
  const sid = useStore((s) => s.sessionId)
  const dot = link === 'open' ? 'bg-success' : link === 'connecting' ? 'bg-warning' : 'bg-danger'

  const undo = async () => {
    if (!sid) return
    try {
      const r = await api.undoState(sid)
      if (!r.ok) toast('info', r.message ?? 'Nothing to undo')
      else useStore.setState({ state: r.state })
    } catch (e) { toast('error', String(e)) }
  }
  return (
    <header className="h-12 shrink-0 bg-surface border-b border-border flex items-center justify-between px-4 gap-3">
      <div className="flex items-center gap-3 text-xs text-muted">
        <span className="flex items-center gap-1.5"><span className={`w-2 h-2 rounded-full ${dot}`} />{link === 'open' ? 'Live' : link === 'connecting' ? 'Connecting…' : 'Offline'}</span>
        <span>Data as of <b className="text-ink">{app?.as_of_date}</b></span>
        <span title="Backend calls made by this page">API calls <b className="text-ink tabular-nums">{calls}</b></span>
        {verify && (
          <span className={`px-2 py-0.5 rounded-full ${verify.ok ? 'bg-green-50 text-green-700' : 'bg-red-50 text-danger'}`}
            title={verify.ok ? 'The backend verified the screen matches the requested state' : JSON.stringify(verify.mismatches).slice(0, 400)}>
            {verify.ok ? '✓ screen verified' : '✕ mismatch caught'}
          </span>
        )}
      </div>
      <div className="flex items-center gap-2">
        <button onClick={undo} className="px-3 py-1.5 text-sm rounded-lg border border-border hover:bg-page" title="Go back to the previous screen state">↶ Undo view</button>
        <button onClick={() => void newSession()} className="px-3 py-1.5 text-sm rounded-lg border border-border hover:bg-page">New session</button>
        <button onClick={onToggleAgent} className={`px-3 py-1.5 text-sm rounded-lg ${agentOpen ? 'bg-primary text-white' : 'border border-primary text-primary hover:bg-primary-soft'}`}>✦ Assistant</button>
      </div>
    </header>
  )
}
