# Deploying Awaaz AI

The repo ships with a GitHub Actions workflow that auto-deploys on every push to `main`.

---

## Option A — Railway (recommended)

1. Create a free account at [railway.app](https://railway.app)
2. New project → **Deploy from GitHub repo** → select `awaazai`
3. Add an environment variable `SARVAM_API_KEY` in the Railway dashboard
4. Copy your **Railway token** from Account Settings
5. In GitHub: **Settings → Secrets → Actions** → add `RAILWAY_TOKEN`
6. Push any commit — the workflow deploys automatically

---

## Option B — Render

1. Create a free account at [render.com](https://render.com)
2. New → **Web Service** → connect `awaazai` repo → Render reads `render.yaml` automatically
3. Add `SARVAM_API_KEY` in the Render dashboard under **Environment**
4. Copy the **Deploy Hook URL** from the service settings
5. In GitHub: **Settings → Secrets → Actions** → add `RENDER_DEPLOY_HOOK`
6. In GitHub: **Settings → Variables → Actions** → add `DEPLOY_TARGET = render`
7. Push any commit — the workflow triggers the Render deploy hook

---

## Environment variables needed everywhere

| Variable | Description |
|---|---|
| `SARVAM_API_KEY` | Your Sarvam AI API key |
| `PORT` | Auto-set by Railway/Render — do not set manually |
