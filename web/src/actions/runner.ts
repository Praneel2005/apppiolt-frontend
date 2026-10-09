/** Turning an Action (metadata) + a row into a confirmed write: form -> dry-run preview -> apply -> undo toast. */
import { create } from 'zustand'
import type { Action, CatalogEntry } from '../lib/types'
import { api, ApiError } from '../net/api'
import { refreshData, toast, useStore } from '../state/store'

export interface ActionRequest {
  entry: CatalogEntry
  label: string
  description: string
  style: 'default' | 'danger'
  /** values already known (from the row or fixed by the action); not editable */
  locked: Record<string, unknown>
  /** show the input form even if every required field is known */
  form: boolean
  /** prefilled but editable values (e.g. defaults suggested by the assistant) */
  initial?: Record<string, unknown>
}

export const useActionDialog = create<{ req: ActionRequest | null; open: (r: ActionRequest) => void; close: () => void }>()((set) => ({
  req: null,
  open: (req) => set({ req }),
  close: () => set({ req: null }),
}))

export function runAction(action: Action, row?: Record<string, unknown>) {
  const entry = useStore.getState().catalog[action.api_id]
  if (!entry) { toast('error', `Unknown API ${action.api_id}`); return }
  const locked: Record<string, unknown> = { ...action.fixed }
  for (const [target, col] of Object.entries(action.params_from_row)) if (row && row[col] != null) locked[target] = row[col]
  useActionDialog.getState().open({ entry, label: action.label, description: action.description, style: action.style, locked, form: action.form })
}

/** Open a write API directly, e.g. from a generated form canvas. */
export function runApi(apiId: string, label: string, initial: Record<string, unknown> = {}) {
  const entry = useStore.getState().catalog[apiId]
  if (!entry) { toast('error', `Unknown API ${apiId}`); return }
  useActionDialog.getState().open({ entry, label, description: entry.description, style: 'default', locked: {}, form: true, initial })
}

export async function applyWrite(req: ActionRequest, values: Record<string, unknown>) {
  const res = await api.callWrite(req.entry, values, false)
  refreshData()
  const id = res.action_id
  toast('success', `${req.label} done${id ? ` · ${id}` : ''}`, id ? async () => {
    try {
      await api.undoAction(id)
      refreshData()
      toast('info', `${id} undone`)
    } catch (e) {
      toast('error', e instanceof ApiError ? e.message : String(e))
    }
  } : undefined, 10000)
  return res
}
