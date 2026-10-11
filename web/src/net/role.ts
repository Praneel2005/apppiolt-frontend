/** The signed-in role, remembered in the browser and sent with every request (X-Role). This is role context for the demo, not authentication. */
const KEY = 'apppilot.role'
let current = ''
try { current = localStorage.getItem(KEY) ?? '' } catch { /* storage unavailable: the server default applies */ }

export const getRole = () => current
export function setRole(id: string) {
  current = id
  try { localStorage.setItem(KEY, id) } catch { /* ignore */ }
}
