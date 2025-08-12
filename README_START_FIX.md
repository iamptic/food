# Start Fix for Railway

This patch ensures Railway starts your backend from `backend/main.py` (not /app/main.py).

Files included:
- `main.py` (root) — tiny shim that exposes `app` from `backend.main`
- `requirements.txt` (root) — installs deps from `backend/requirements.txt`
- `Procfile` — `web: uvicorn backend.main:app --host 0.0.0.0 --port $PORT`

## How to use
1. Drop these three files into the **repo root** (next to `backend/`, `web/`, etc). Commit & push.
2. In Railway backend service:
   - Build Command: `pip install -r requirements.txt` (or leave empty; Nixpacks will detect and install)
   - Start Command (preferred): `uvicorn backend.main:app --host 0.0.0.0 --port $PORT`
     - If you can't change Start Command, Procfile will handle it.
3. Redeploy.

After deploy, check `/health` on your backend domain — should return `{"ok": true}`.
