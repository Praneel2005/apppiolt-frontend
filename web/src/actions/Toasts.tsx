import { dismissToast, useStore } from '../state/store'

export function Toasts() {
  const toasts = useStore((s) => s.toasts)
  return (
    <div className="fixed bottom-4 left-1/2 -translate-x-1/2 z-[60] flex flex-col gap-2 items-center">
      {toasts.map((t) => (
        <div key={t.id} className={`flex items-center gap-3 rounded-lg shadow-lg px-4 py-2.5 text-sm text-white max-w-xl
          ${t.kind === 'success' ? 'bg-slate-900' : t.kind === 'error' ? 'bg-danger' : 'bg-primary'}`}>
          <span>{t.kind === 'success' ? '✓ ' : t.kind === 'error' ? '⚠ ' : 'ℹ '}{t.text}</span>
          {t.undo && <button className="underline font-medium" onClick={() => { t.undo!(); dismissToast(t.id) }}>Undo</button>}
          <button className="opacity-70 hover:opacity-100" onClick={() => dismissToast(t.id)}>✕</button>
        </div>
      ))}
    </div>
  )
}
