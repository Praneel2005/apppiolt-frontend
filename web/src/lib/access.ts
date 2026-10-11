/** What a role may open and do, mirrored from the server's roles (the server enforces it; this only hides what would be refused). */
import type { Page, Role } from './types'

export const moduleOf = (p: Page) => p.directory.split('/')[0]
export const canOpen = (r: Role | null, p: Page) => !r || r.modules === '*' || r.modules.includes(moduleOf(p))
export const canWrite = (r: Role | null, apiId: string) => !r || r.writes === '*' || r.writes.includes(apiId)
export const scopeText = (r: Role | null) => (r ? Object.entries(r.scope).map(([k, v]) => `${k.replace(/_/g, ' ')} = ${v.join(', ')}`).join('; ') : '')
