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
    <header className="h-14 shrink-0 bg-surface/90 backdrop-blur border-b border-border flex items-center justify-between px-5 gap-3">
      <div className="flex items-center gap-4 text-xs text-muted">
        <span className="flex items-center gap-1.5"><span className={`w-2 h-2 rounded-full ${dot}`} />{link === 'open' ? 'Live' : link === 'connecting' ? 'Connecting…' : 'Offline'}</span>
        <span>Data as of <b className="text-ink">{app?.as_of_date}</b></span>
        <span title="Backend calls made by this page">API calls <b className="text-ink tabular-nums">{calls}</b></span>
        {verify && (
          <span className={`px-2.5 py-1 rounded-full font-medium ${verify.ok ? 'bg-success-soft text-success' : 'bg-danger-soft text-danger'}`}
            title={verify.ok ? 'The backend verified the screen matches the requested state' : JSON.stringify(verify.mismatches).slice(0, 400)}>
            {verify.ok ? '✓ screen verified' : '✕ mismatch caught'}
          </span>
        )}
      </div>
      <div className="flex items-center gap-2">
        <button onClick={undo} className="btn" title="Go back to the previous screen state">↶ Undo view</button>
        <button onClick={() => void newSession()} className="btn">New session</button>
        <button onClick={onToggleAgent} className={`btn ${agentOpen ? 'btn-primary' : 'border-primary! text-primary! hover:bg-primary-soft!'}`}>✦ Assistant</button>
      </div>
    </header>
  )
}
