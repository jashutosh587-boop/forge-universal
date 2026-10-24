# Forge — Universal Creation Workspace

Forge is an early open-source prototype for a universal creation platform: one workspace for 3D design, project files, scripts, documentation, lightweight checks, revision history, and sharing.

## Run Forge with the real backend

Requirements: Python 3.10 or newer. No third-party Python packages are required.

### Windows

```powershell
cd forge_universal
py -m venv .venv
.venv\Scripts\activate
python server.py
```

### macOS / Linux

```bash
cd forge_universal
python3 -m venv .venv
source .venv/bin/activate
python server.py
```

Open **http://127.0.0.1:8000**. The landing page links to the workspace and 3D Studio. Open **Workspace → Cloud** to create an account or sign in, then save the current project to the database. SQLite data is stored in `forge.db` next to `server.py` and persists across restarts.

> Do not use `python -m http.server` for cloud features. It only serves static files and cannot run the API. Use `python server.py`.

## Backend features in this version

- JSON API built with Python standard library HTTP server and SQLite persistence (no pip packages needed).
- Email/password account creation and sign-in.
- Password hashing with PBKDF2-HMAC-SHA256; opaque random session tokens are stored hashed in the database and expire after 14 days.
- Private project create, list, load, update, and delete API endpoints.
- Each cloud save creates an immutable JSON revision record.
- Public project discovery endpoint and fork/remix endpoint.
- Request size limits, input validation, ownership checks, parameterized SQL, and database foreign keys.
- Existing localStorage workflow remains available offline; cloud sync is explicit through the Cloud dialog.

## API quick reference

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/api/health` | Server status |
| POST | `/api/auth/register` | Create account (`email`, `password`) |
| POST | `/api/auth/login` | Sign in |
| POST | `/api/auth/logout` | Revoke current session |
| GET | `/api/me` | Current account |
| GET / POST | `/api/projects` | List or create projects |
| GET / PUT / DELETE | `/api/projects/<id>` | Read, update, or delete an owned project |
| GET | `/api/projects/<id>/revisions` | List project revisions |
| GET | `/api/public/projects?q=...` | Discover public projects |
| POST | `/api/projects/<id>/fork` | Fork a public project into your account |

Protected endpoints require `Authorization: Bearer <token>`. API requests and responses use JSON.

## Important current limits

This is a development backend, not a production deployment. The browser stores the session token in localStorage; a production release should prefer secure, HttpOnly cookies, CSRF protection, rate limiting, email verification, password reset, audit logging, backups, migrations, and security review. Use HTTPS and a proper deployment setup before exposing it to the internet. Uploaded binary assets are not yet streamed into object storage—the current cloud save persists project JSON and file metadata. Public discovery and fork endpoints exist in the API, but a polished public gallery is not yet wired into the UI. There is no live multiplayer editing, hosted compute, or validated FEA/CFD solver. Project checks are not engineering simulations.

## Project structure

- `index.html` — Forge landing page
- `workspace.html` — project workspace and Cloud sync UI
- `studio.html` — standalone Three.js primitive-based 3D concept editor
- `server.py` — Python HTTP API and SQLite schema
- `requirements.txt` — intentionally empty dependency list; standard library only
- `forge.db` — created automatically when the server starts (ignored by Git)

## Open-source direction

The project can be released under an OSI-approved license after selecting a license and reviewing all bundled dependencies/assets. A future production architecture can move to PostgreSQL, object storage, a background job queue, isolated solver adapters, and a React/TypeScript editor/plugin API.

## Deploy Forge online with Railway (deployment-ready config)

The repository includes a `Dockerfile` and `railway.json` for Railway deployment. To publish it: create a GitHub repository, upload/push the contents of this folder, create a Railway project from that GitHub repository, then configure the service below.

1. In Railway, create a **Volume** mounted at `/data` for the Forge service. Without this volume, the SQLite database may be lost on redeploy/restart.
2. Set environment variable `FORGE_DB_PATH=/data/forge.db`. The container already defaults to this path. Railway supplies `PORT`; Forge reads it and binds to `0.0.0.0`.
3. Deploy the service and wait for the `/api/health` health check to pass.
4. Use Railway's generated public domain to open Forge.

**Before public launch:** this is still a prototype. Do not use real sensitive data yet. Add rate limiting, email verification and password reset, secure cookie sessions, backups, security review, and monitoring before inviting the public. SQLite on a persistent volume is suitable for an early demo, not a high-concurrency production platform.

### Deployment checklist
- [ ] Push this folder to a GitHub repository you control.
- [ ] Connect that repository to Railway.
- [ ] Attach a persistent volume at `/data`.
- [ ] Confirm `FORGE_DB_PATH=/data/forge.db`.
- [ ] Deploy and check `/api/health`.
- [ ] Register a test account and verify project save/reload.
- [ ] Keep backups and do not commit database files or secrets.
