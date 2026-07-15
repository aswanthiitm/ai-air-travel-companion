# Deployment

Traveler Twin ships as two independently deployable services:

- **`api`** — FastAPI backend (`src/api.py`), reads `data/*.csv` and persists
  learned Twin state to `data/twin.db` (SQLite).
- **`ui`** — static React/Vite build that talks to the API over `/api/*`
  (relative paths, `CORS` on the API is already open — see `src/api.py`).

Pick whichever path fits where you're hosting.

## Option A — Docker Compose (self-host, single command)

```bash
docker compose up --build
```

- API on `http://localhost:8010`
- UI on `http://localhost:8080` (nginx proxies `/api/*` to the `api`
  container — see `ui/nginx.conf`)
- `data/` is persisted in the `twin-data` named volume, so learned Twin
  state survives container restarts.

To enable agent mode (LLM understanding), copy `.env.example` to `.env` and
set one provider key before running `docker compose up`:

```bash
cp .env.example .env
# edit .env: set GROQ_API_KEY, CEREBRAS_API_KEY, or OPENROUTER_API_KEY
docker compose --env-file .env up --build
```

Without a key, the deterministic fallback handles everything — this is not
required for the app to work.

## Option B — Split hosting (API + static UI on separate hosts)

Any host that runs a Dockerfile or a `uvicorn` process works for the API
(Render, Railway, Fly.io, a VPS). Any static host works for the UI (GitHub
Pages, Netlify, Vercel, Cloudflare Pages).

### API (e.g. Render / Railway / Fly)

- Build with the root `Dockerfile`, or run directly:
  ```bash
  pip install -r requirements.txt
  uvicorn src.api:app --host 0.0.0.0 --port $PORT
  ```
- Set optional env vars from `.env.example` if you want agent mode.
- `data/flights_data.csv` and `data/user_data.csv` ship in the image/repo —
  no external database needed. `data/twin.db` is created on first run;
  attach a persistent disk/volume at `data/` if you want learned Twin state
  to survive redeploys (otherwise it resets on every deploy, which is fine
  for a demo).

### UI (e.g. GitHub Pages / Netlify / Vercel)

The UI is built as static files:

```bash
cd ui
npm install
npm run build   # outputs ui/dist
```

Since the frontend calls relative `/api/*` paths, a static host needs a
rewrite/proxy rule pointing `/api/*` at your deployed API's URL:

- **Netlify**: add a `ui/_redirects` file with
  `/api/*  https://your-api-host/api/:splat  200`
- **Vercel**: add a rewrite in `ui/vercel.json`:
  ```json
  { "rewrites": [{ "source": "/api/:path*", "destination": "https://your-api-host/api/:path*" }] }
  ```
- **GitHub Pages** has no server-side proxying — either point the UI at the
  API's absolute URL (edit `ui/src/api.js` to prefix requests with the API
  host) or front both with a reverse proxy (e.g. Cloudflare) that owns the
  routing.

## CI

`.github/workflows/ci.yml` runs the pytest suite and the UI production build
on every push/PR to `main`. A deploy step can be appended once you pick a
host with a stable deploy token/API (e.g. `render-deploy-action`,
`vercel-action`) — intentionally left out here since it needs
account-specific secrets.
