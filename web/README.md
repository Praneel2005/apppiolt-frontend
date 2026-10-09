# AppPilot web

React + Vite + TypeScript + Tailwind + Zustand + Recharts. The app is **rendered from backend metadata**
(`GET /api/application`): there is no hand-written page. Every page, widget, filter and action you see comes from
`data/olist/application.json`, so the assistant can act on exactly what the user sees.

```bash
# terminal 1 (repo root)            uvicorn backend.main:app --port 8000
# terminal 2
. scripts/activate_env.sh && cd web && npm install && npm run dev -- --host
```
Open `http://localhost:5173` (or `http://<workstation-ip>:5173` from another machine).

## How it maps to the backend

| Concern | Where | Backend counterpart |
|---|---|---|
| Pages, widgets, filters, actions | `pages/`, `widgets/`, `nav/` | `contracts/metadata.py` v0.2 |
| What a widget asks for under a UI state | `lib/request.ts` | `backend/views.py` (shared semantics S5/S10) |
| Series hash in every acknowledgement | `lib/hash.ts` | `contracts/ui_state.py` |
| URL <-> state | `lib/url.ts` | `backend/deeplinks.py` |
| Session, WebSocket, `apply_state` / `render_ack` | `state/session.ts` | `backend/sessions.py`, `routes/session.py` |
| Write actions: form -> dry-run preview -> confirm -> undo | `actions/` | `backend/writes.py`, API catalogue |
| Assistant panel, plan runner, canvas | `agent/` | `backend/executor.py`, `validator.py`, `verifier.py` |

## Tests
`npm test` checks the hash against `contracts/hash_vectors.json`, the URL format against
`contracts/deeplink_vectors.json`, and the filter/date/sort rules; `npm run typecheck` runs `tsc`.
