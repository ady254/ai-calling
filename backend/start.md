# Local dev quickstart ────────────────────────────────────────────────

This is the one file I keep open so I never lose a day to "wait, what do I
need running before the app will start?".

## 1. Prerequisites

- Python 3.12 — the Dockerfile pins `python:3.12-slim`.
- PostgreSQL 15+ (or Docker — see below).
- Redis 7+ for the ARQ background worker.
- A `.env` filled from `.env.example`.

Services that are **optional** for local dev and only needed in production:

- LiveKit — required for SIP-connected AI phone calls. If `LIVEKIT_SIP_DOMAIN`
  is empty the app falls back to a recorded greeting, so you can develop
  locally without it.
- Twilio — required to receive inbound voice calls. Use ngrok to expose a
  public HTTPS URL and point a Twilio number at it during testing.
- ElevenLabs — only needed if you enable ElevenLabs TTS; Google gTTS is the
  fallback.
- Gemini API key — required for the AI agent. The app will not function
  without `GEMINI_API_KEY`.

## 2. One-command local stack (Docker Compose recommended)

The fastest path is to run Postgres + Redis in containers and the app in the
venv. That mirrors production closely without needing a separate Compose file
for the app itself.

Example `docker-compose.yml` if you want it in the repo:

```yaml
services:
  db:
    image: postgres:15-alpine
    environment:
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: devpassword
      POSTGRES_DB: aicalling
    ports: ["5432:5432"]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres"]
      interval: 5s
      retries: 5

  redis:
    image: redis:7-alpine
    ports: ["6379:6379"]
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      retries: 5
```

```bash
docker compose up -d
# wait a few seconds for both healthchecks to pass
docker compose ps
```

## 3. Copy and fill `.env`

```bash
cp .env.example .env
```

Required changes (the app refuses to start with the defaults):

- `DATABASE_URL` — point at your Postgres, default is
  `postgresql+asyncpg://postgres:YOUR_DB_PASSWORD@localhost:5432/aicalling`.
- `SECRET_KEY` — generate with:
  `python -c "import secrets; print(secrets.token_hex(32))"`.
- `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET` / `LIVEKIT_URL` — from LiveKit
  Console.
- `GEMINI_API_KEY` — from Google AI Studio.
- `TWILIO_*` — from Twilio Console (only needed to receive calls).
- `INTERNAL_API_KEY` — **must** be changed from the default and be 32+ chars.
  Generate with the same `secrets.token_hex(32)` command. The app will crash
  on startup with a `ValueError` if you leave it as the placeholder.



```
INTERNAL_API_KEY=8a9596922fdde6c27c0e7a6bcbb7293e1410753c22486db10ce68d9c55d72396
```

That is a throwaway example. Replace it with your own generated value; do not
commit a real key to source control and do not paste mine into `.env` on a
shared machine.

Optional but recommended for local dev:

- `ALLOW_PUBLIC_SIGNUP=true` — only for local. Production must leave this
  `false` and provision users with `scripts/create_user.py`.
- `BASE_URL` — set to your ngrok URL when testing Twilio webhooks. If it
  still says `localhost`, the startup log warns you and Twilio can't reach the
  webhooks.
- `LOG_LEVEL=DEBUG` — noisy but useful while debugging.

## 4. Create the database and run migrations

```bash
# From the project root, using the venv
source venv/bin/activate

# Create the database (one time)
psql -U postgres -c "CREATE DATABASE aicalling;"
# Or via Docker:
docker exec -it <db-container> psql -U postgres -c "CREATE DATABASE aicalling;"

# Apply migrations
alembic upgrade head
```

The Dockerfile also runs `alembic upgrade head` on startup, so in production
you usually do not need to run this manually. Run it locally any time you pull
new migrations.

## 5. Install dependencies (if not using Docker for the app)

```bash
python -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

One dependency caveat worth remembering: `bcrypt` is pinned to `<4.1.0` in
`requirements.txt`. Newer bcrypt versions break password hashing in this project
(`passlib` probe triggers the 72-byte hard error). Do not upgrade bcrypt.

## 6. Create the first user

By default `ALLOW_PUBLIC_SIGNUP` is `false`, so there is no `/auth/signup` path
open to the public. The intended path is the admin script:

```bash
python scripts/create_user.py
```

Use this both to create your first admin-style user and to provision additional
users for local testing. Do not flip `ALLOW_PUBLIC_SIGNUP` true on a
reachable server — that lets anyone register and spend the Twilio/Gemini budget.

## 7. Start the app and the worker

```bash
# In terminal A:
uvicorn app.main:app --reload --port 8000

# In terminal B:
arq app.worker.WorkerSettings
```

The app serves on `http://localhost:8000`. Health check:

```bash
curl http://localhost:8000/health
```

Expected: `{"status":"healthy","service":"ai-calling-backend"}`.

Notes:

- `ARQ_REDIS_URL` is read from `REDIS_URL` by the settings object; the worker
  and the app share the same Redis.
- The app mounts `/audio` from the `audio/` directory for TTS output.
- If `REDIS_URL` points at a container, make sure the port mapping matches
  what you put in `.env`.

## 8. ngrok for Twilio webhooks

Twilio calls your backend over the public internet. While developing locally,
forward port 8000:

```bash
ngrok http 8000
```

Copy the HTTPS URL into `.env` as `BASE_URL`, then point a Twilio phone number
at:

```
https://<your-ngrok-url>/call/inbound
```

The `BASE_URL` config is also used for any other outbound webhook URLs the app
constructs, so double-check it before testing call flows.

## 9. Common "why won't it start?" checklist

- **Postgres not reachable** — the app uses `asyncpg` and will fail at startup
  if `DATABASE_URL` is wrong or the DB is still coming up.
- **Migrations not run** — a fresh schema means requests 500. Run
  `alembic upgrade head`.
- **`INTERNAL_API_KEY` still default** — config validation raises immediately.
- **`GEMINI_API_KEY` missing** — AI agent calls fail; the rest of the app may
  still start depending on the endpoint.
- **`ALLOW_PUBLIC_SIGNUP` left false and no user created** — nobody can log in.
- **Twilio webhooks not hitting** — `BASE_URL` is still localhost, or the
  Twilio number points at the wrong URL.
- **Redis down** — the background worker fails; the web app may still start but
  queued tasks (call execution, etc.) will not run.

## 10. Running tests

```bash
pytest
```

Config lives in `pytest.ini`. There is a shared `tests/conftest.py`. If you
add integration-style tests that need the DB, point them at a separate test DB
rather than the dev database.

## 11. Prod vs local reminders

- The Docker image runs migrations on startup by default
  (`RUN_MIGRATIONS=true`). Set it to `false` when a separate migration job
  owns DDL in a multi-instance deploy.
- Sentry is a no-op when `SENTRY_DSN` is empty, so local logs stay local.
- The app runs as a non-root `appuser` in the container and the TTS
  `audio/` directory is created with the right ownership on build.
- Passwords: `passlib` + `bcrypt<4.1.0`. Do not unpin bcrypt.
