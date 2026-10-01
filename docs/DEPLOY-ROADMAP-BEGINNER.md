# Beginner Deployment Roadmap 🚀 (AWS Edition)

A friendly, step-by-step path to put this project on the internet for the very first time —
using **Amazon Web Services (AWS)**. This is your first cloud project, so every step explains
the **goal**, the **why**, the **exact clicks/commands**, and a **checkpoint** so you know
you're on track before moving forward.

> **The plan in one sentence:** rent **one always-on virtual machine** on AWS (called an EC2
> instance) that runs your whole backend (with `docker compose up`, exactly like on your
> laptop), and put the **website** on Vercel for free. That's it — two homes.

⏱️ **Total time:** ~2–3 hours for a first-timer. Go phase by phase. Don't skip checkpoints.

---

## Why AWS?

| Perk | Detail |
|---|---|
| **12-month Free Tier** | `t2.micro` EC2 instance (1 vCPU, 1 GB RAM) completely free for 12 months |
| **750 hrs/month** | That's enough to run one instance 24/7 — no pausing needed |
| **No billing surprise** | Free Tier usage never charges you — AWS emails a warning if you approach limits |
| **Industry standard** | AWS is the world's most-used cloud — learning it now pays off forever |
| **Sign-up link** | [aws.amazon.com/free](https://aws.amazon.com/free) |

> ⚠️ **RAM note:** The free `t2.micro` has only 1 GB RAM. Your project runs 4 containers
> (backend, worker, AI agent, Caddy). To avoid out-of-memory crashes, we add a **swap file**
> in Phase 2 — a standard trick that makes 1 GB work in practice for light load.
> If you hit limits later, upgrading to `t3.small` (~$15/mo) gives 2 GB — still very cheap.

---

## Part 1 — The big picture (read this first, 5 min)

Think of your project like a small **call centre**:

| Part of your code | What it does (plain words) | Where it will live |
|---|---|---|
| **Frontend** | The website you log into and click buttons on | **Vercel** (free forever) |
| **Backend (API)** | The "office manager" — takes requests, talks to the database, tells Twilio to make calls | **AWS EC2 instance** |
| **Worker** | The "dialer" — works through your contact list in the background | **AWS EC2 instance** |
| **AI agent** | The "voice on the phone" — listens, thinks, speaks | **AWS EC2 instance** |
| **Database (Postgres)** | The "filing cabinet" — stores users, campaigns, call logs | **Neon** (free managed cloud DB) |
| **Redis** | The "sticky-note board" — the job queue the worker reads from | **Upstash** (free managed cloud Redis) |

> 💡 **Why Neon + Upstash instead of running Postgres/Redis on the EC2 instance?**
> The `t2.micro` only has 1 GB RAM. Running the database *and* the app containers on the same
> tiny box is a recipe for OOM crashes. Neon and Upstash are permanently free managed services —
> they connect over the internet with zero maintenance. This is the right split for a small instance.

And these you **do NOT install** — they're external services you just get API keys for:
**Twilio** (phone calls), **LiveKit** (audio pipes), **Gemini** (the AI brain),
**ElevenLabs** (the voice), **Deepgram** (speech-to-text).

```mermaid
flowchart LR
    User[You in a browser] --> Vercel[Frontend on Vercel]
    Vercel -->|API calls| Server
    subgraph Server[AWS EC2 t2.micro - runs docker compose]
        Caddy[Caddy - HTTPS]
        Backend[Backend API]
        Worker[Worker]
        Agent[AI Agent]
    end
    Backend --> Neon[(Neon Postgres - free)]
    Backend --> Upstash[(Upstash Redis - free)]
    Backend -->|make a call| Twilio
    Twilio -->|webhook: call status| Backend
    Agent <-->|audio| LiveKit
    Agent --> Gemini & ElevenLabs & Deepgram
```

**Why one EC2 instance?** The Worker and AI agent must run **all the time** (waiting for calls).
Free "serverless" hosts go to sleep, which breaks everything. One always-on EC2 instance is
the simplest thing that works — and it's basically your laptop's `docker compose up`, but in
the cloud and never turned off.

---

## Part 2 — Your shopping list (do this before Phase 0)

### Accounts to create (all free to start)

- [ ] **AWS account** → [aws.amazon.com/free](https://aws.amazon.com/free).
      You **do need a credit card** for identity verification, but **you will not be charged**
      as long as you stay within Free Tier limits. AWS emails you before any charge.
- [ ] **Neon** account (free Postgres) → [neon.tech](https://neon.tech). Sign in with GitHub.
- [ ] **Upstash** account (free Redis) → [upstash.com](https://upstash.com). Sign in with GitHub.
- [ ] **Vercel** account → [vercel.com](https://vercel.com). Sign in with GitHub.
- [ ] A **domain name** — a `.xyz` is often $1–3/year from
      [Namecheap](https://namecheap.com) or [Cloudflare](https://cloudflare.com/products/registrar/).
      You need this so Twilio can reach your backend over a secure `https://` URL permanently.

### Keys to collect into ONE text file (your "secrets notepad")
Open your local project `.env` and copy these values somewhere safe —
you'll paste them onto the server in Phase 4:

- `SECRET_KEY`, `INTERNAL_API_KEY` (⚠️ will be regenerated fresh — see Phase 4)
- `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_PHONE_NUMBER`
- `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`, `LIVEKIT_URL`, `LIVEKIT_SIP_DOMAIN`
- `GEMINI_API_KEY` (**and** also note: you'll set `GOOGLE_API_KEY` to the same value — see Phase 4)
- `ELEVEN_API_KEY`, `DEEPGRAM_API_KEY`

> 🔒 **Never** commit this file to Git or paste these keys into a chat. They're like passwords.

---

## Part 3 — The phases

Do them in order. Each one builds on the last.

---

### Phase 0 — Set up free managed Postgres + Redis ⏱️ ~20 min · difficulty: 🟢 easy

**Goal:** get Neon (Postgres) and Upstash (Redis) ready with connection URLs before touching
the server. You'll need those URLs when writing your `.env` in Phase 4.

**Why:** running Postgres + Redis inside a 1 GB instance would eat all the memory. Using
permanently-free managed services is safer — and your data survives even if you delete the EC2 instance.

#### 0a — Neon (Postgres)

1. Go to [neon.tech](https://neon.tech) → **Sign up** (GitHub is easiest) → **New Project**.
2. Name it `ai-calling`, pick the region closest to you → **Create project**.
3. On the dashboard → **Connection string** → copy the URL. It looks like:
   ```
   postgresql://USER:PASSWORD@HOST.neon.tech/neondb?sslmode=require
   ```
4. **Convert to asyncpg format** (required by this app — change the scheme prefix and drop `?sslmode=require`):
   ```
   postgresql+asyncpg://USER:PASSWORD@HOST.neon.tech/neondb
   ```
   Save this as `DATABASE_URL` in your secrets notepad.

#### 0b — Upstash (Redis)

1. Go to [upstash.com](https://upstash.com) → **Sign up** → **Create Database**.
2. Name: `ai-calling` · Type: **Regional** · Region: closest to you → **Create**.
3. On the database page, find **Redis URL** (the `rediss://...` one, with TLS).
   Save it as `REDIS_URL` in your secrets notepad.

**✅ Done when:** you have both `DATABASE_URL` and `REDIS_URL` saved and ready to paste.

---

### Phase 1 — Create your AWS account + launch a free EC2 instance ⏱️ ~25 min · difficulty: 🟢 easy

**Goal:** get a free Linux server running on AWS that you can log into from your laptop.

**Why:** this is the "always-on laptop in the cloud" that will run all your backend services.

#### 1a — Sign up for AWS

1. Go to [aws.amazon.com/free](https://aws.amazon.com/free) → **Create a Free Account**.
2. Enter your email, create a password, add your credit card (identity check only),
   verify via phone call or SMS.
3. Choose **Basic (free) support plan**.
4. You land in the **AWS Management Console**. 🎉

#### 1b — Launch your EC2 instance

1. In the Console top search bar, type **EC2** → click it → click the orange **Launch instance** button.
2. Fill in the form:

   | Field | What to choose |
   |---|---|
   | **Name** | `ai-calling-server` |
   | **Application and OS Images** | `Ubuntu Server 24.04 LTS` |
   | **Instance type** | `t2.micro` — confirm it says **"Free tier eligible"** ✅ |
   | **Key pair (login)** | **Create new key pair** → name: `ai-calling-key` · type: `RSA` · format: `.pem` → **Create key pair** → your browser downloads `ai-calling-key.pem` — **save this file somewhere safe, it cannot be re-downloaded** |
   | **Network settings** | Click **Edit** → tick **Allow SSH traffic from: Anywhere** ✅, **Allow HTTP traffic** ✅, **Allow HTTPS traffic** ✅ |
   | **Configure storage** | Change `8 GiB` → **`20 GiB`** (still within free tier — more space for Docker images) |

3. Click the orange **Launch instance** button.
4. Click **View all instances** → wait until **Instance State** = `running` ✅.
5. Click your instance name → find and save the **Public IPv4 address** (e.g. `54.x.x.x`).

**✅ Done when:** instance is `running` and you have its Public IP saved.

---

### Phase 2 — SSH into the server + install Docker ⏱️ ~15 min · difficulty: 🟡 medium

**Goal:** connect to the server and install Docker so it can run your containers.

#### 2a — Connect via SSH (Windows — PowerShell or Windows Terminal)

```powershell
# Step 1: fix .pem file permissions (Windows SSH rejects keys that are too open)
icacls "C:\Users\YourName\Downloads\ai-calling-key.pem" /inheritance:r /grant:r "$($env:USERNAME):(R)"

# Step 2: SSH in (replace 54.x.x.x with your actual Public IP)
ssh -i "C:\Users\YourName\Downloads\ai-calling-key.pem" ubuntu@54.x.x.x
```

Type `yes` when asked to confirm the fingerprint. You're now inside your AWS server. 🖥️

> 💡 The default username for Ubuntu on AWS is always `ubuntu` (not your Windows username).

#### 2b — Install Docker

Paste this entire block into the SSH terminal:
```bash
# Install Docker using the official convenience script
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
```

Type `exit` to disconnect, then SSH back in (the group change needs a fresh session):
```bash
# Reconnect — same command as before
ssh -i "C:\Users\YourName\Downloads\ai-calling-key.pem" ubuntu@54.x.x.x

# Verify — both should print version numbers with no errors
docker --version
docker compose version
```

#### 2c — Add swap space (critical for t2.micro's 1 GB RAM)

```bash
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile

# Make swap permanent across reboots
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

This gives the server 2 GB of "virtual RAM" on disk — a standard trick that prevents
out-of-memory crashes when running multiple containers on a small instance.

**✅ Done when:** `docker --version` prints a version, and `free -h` shows `~2.0G` in the Swap row.

---

### Phase 3 — Clone your code onto the server ⏱️ ~10 min · difficulty: 🟢 easy

**Goal:** get your project files onto the EC2 instance.

```bash
git clone <your-repo-url> ai-calling
cd ai-calling
```

> If your repo is **private**, use a GitHub personal access token in the URL:
> ```bash
> git clone https://YOUR_TOKEN@github.com/yourname/your-repo.git ai-calling
> ```
> Generate a token at GitHub → Settings → Developer settings → Personal access tokens → Fine-grained tokens.

**✅ Done when:** `ls` inside the `ai-calling` folder shows your project files (docker-compose.yml, backend/, frontend/, etc.).

---

### Phase 4 — Configure your production environment ⏱️ ~30 min · difficulty: 🟡 medium

**Goal:** create the `.env` file on the server with all secrets and production settings.

```bash
cp .env.example .env
nano .env     # text editor — Ctrl+O to save, Ctrl+X to exit
```

Fill in every variable. Here's exactly what to set:

| Variable | Value | Notes |
|---|---|---|
| `SECRET_KEY` | run `openssl rand -hex 32` → paste the output | Fresh random string, never reuse your local one |
| `INTERNAL_API_KEY` | run `openssl rand -hex 32` again → paste | **App refuses to start without a real value here** |
| `DATABASE_URL` | your Neon URL from Phase 0 | `postgresql+asyncpg://USER:PASS@HOST.neon.tech/neondb` |
| `REDIS_URL` | your Upstash URL from Phase 0 | `rediss://...` |
| `TWILIO_ACCOUNT_SID` | from your secrets notepad | |
| `TWILIO_AUTH_TOKEN` | from your secrets notepad | |
| `TWILIO_PHONE_NUMBER` | from your secrets notepad | |
| `LIVEKIT_API_KEY` | from your secrets notepad | |
| `LIVEKIT_API_SECRET` | from your secrets notepad | |
| `LIVEKIT_URL` | from your secrets notepad | e.g. `wss://innvox-um8kvrmw.livekit.cloud` |
| `LIVEKIT_SIP_DOMAIN` | from your secrets notepad | |
| `GEMINI_API_KEY` | your Gemini key | |
| `GOOGLE_API_KEY` | **same value as `GEMINI_API_KEY`** | ⚠️ The AI agent's LiveKit Google plugin reads `GOOGLE_API_KEY`. If missing or wrong, the agent breaks silently with no obvious error. Set both to the same key. |
| `ELEVEN_API_KEY` | from your secrets notepad | |
| `DEEPGRAM_API_KEY` | from your secrets notepad | |
| `BASE_URL` | `https://api.yourdomain.com` | Your domain — you'll set up DNS in Phase 5 |
| `ALLOWED_ORIGINS` | `https://your-app.vercel.app` | Your Vercel URL — you'll get it in Phase 7; you can update later |
| `ALLOW_PUBLIC_SIGNUP` | `false` | Prevents strangers from registering and spending your Twilio/LLM budget |
| `ENVIRONMENT` | `production` | |
| `LOG_FORMAT` | `json` | Cleaner structured logs on a server |

**✅ Done when:** `.env` has no `YOUR_...` or placeholder values left. Verify with:
```bash
grep -E "YOUR_|CHANGE_ME|placeholder" .env   # should return nothing
```

---

### Phase 5 — Point your domain at the server + enable HTTPS ⏱️ ~25 min · difficulty: 🟡 medium

**Goal:** make `https://api.yourdomain.com` reach your server securely, permanently —
no more ngrok, no more updating URLs on every restart.

**Why:** Twilio sends call-status updates ("call answered", "call ended") to your backend
as **webhooks**, which require a real, permanent `https://` address to work reliably.

#### 5a — DNS: point your subdomain at the EC2 instance

1. Log into your domain registrar (Namecheap, Cloudflare, etc.).
2. Go to **DNS settings** → add an **A record**:
   - **Name/Host:** `api`
   - **Value/Points to:** `54.x.x.x` *(your EC2 Public IPv4)*
   - **TTL:** 300 (or Auto)
3. Save. DNS changes propagate in 1–10 minutes.

#### 5b — Create the Caddy config (auto-HTTPS, no cert setup needed)

On the server, inside your `ai-calling` folder:
```bash
nano Caddyfile
```
Paste exactly this (replace the domain):
```
api.yourdomain.com {
    reverse_proxy backend:8000
}
```
Save and exit (`Ctrl+O`, `Ctrl+X`).

Caddy automatically fetches and renews a free Let's Encrypt HTTPS certificate for you.
No `certbot`, no manual cert management. It just works.

#### 5c — Create the production compose override

```bash
nano docker-compose.prod.yml
```
Paste:
```yaml
services:
  # Caddy: handles HTTPS + proxies traffic to the backend container
  caddy:
    image: caddy:2-alpine
    restart: unless-stopped
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy_data:/data
    networks:
      - aicalling-net

  # Disable local postgres — using Neon (managed, free) instead
  postgres:
    profiles: ["disabled"]

  # Disable local redis — using Upstash (managed, free) instead
  redis:
    profiles: ["disabled"]

  # Disable the frontend container — using Vercel instead
  frontend:
    profiles: ["disabled"]

volumes:
  caddy_data:
```
Save and exit.

**✅ Done when:** `Caddyfile` and `docker-compose.prod.yml` are both saved in the project folder.

---

### Phase 6 — Launch everything ⏱️ ~10 min · difficulty: 🟢 easy

**Goal:** start the entire backend with one command.

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

- `-d` = run detached (in the background)
- `--build` = build Docker images first — takes **3–8 minutes** the first time, that's normal
- The **backend automatically runs database migrations** (Alembic) on startup — no manual step needed

Watch things come online:
```bash
docker compose ps                    # all services should show "running" or "healthy"
docker compose logs -f backend       # watch backend startup — Ctrl+C to stop
```

**✅ Done when:** opening `https://api.yourdomain.com/health` in your browser shows
`{"status":"healthy",...}` with a padlock 🔒.

> ⏳ If HTTPS isn't working yet, wait 1–2 minutes — Caddy is fetching the certificate from
> Let's Encrypt in the background. Refresh and try again.

---

### Phase 7 — Deploy the frontend to Vercel ⏱️ ~15 min · difficulty: 🟢 easy

**Goal:** put the dashboard online for free.

1. Go to [vercel.com](https://vercel.com) → **Add New → Project → Import** your GitHub repo.
2. Set **Root Directory = `frontend`** — this is important, your website lives in that subfolder.
   Vercel auto-detects Next.js + pnpm.
3. Add **Environment Variables** before clicking Deploy:
   - `NEXT_PUBLIC_API_URL` = `https://api.yourdomain.com`
   - `NEXT_PUBLIC_LIVEKIT_URL` = your LiveKit URL (e.g. `wss://innvox-um8kvrmw.livekit.cloud`)
4. Click **Deploy**. Vercel gives you a URL like `https://your-app.vercel.app`. Save it.
5. **Back on the server**, update `ALLOWED_ORIGINS` in `.env` with your Vercel URL, then restart:
   ```bash
   nano .env    # set ALLOWED_ORIGINS=https://your-app.vercel.app
   docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d backend
   ```
   *(Required for CORS — without this, your browser will silently block API requests from the frontend.)*

**✅ Done when:** the Vercel URL opens your dashboard's login page.

---

### Phase 8 — Connect Twilio + LiveKit to your permanent URL ⏱️ ~15 min · difficulty: 🟡 medium

**Goal:** tell the phone services where your live backend lives.

**Why:** they were pointing at your laptop/ngrok before. Now they get a permanent URL —
**no more restarting ngrok, ever.**

1. **Twilio Console → Phone Numbers → Manage → Active numbers → your number → Configure:**
   - Set the **A call comes in** webhook to `https://api.yourdomain.com/...` (same path as local, just new domain).
   - Set the **Call status changes** callback similarly.
   - Save.

2. **LiveKit — confirm the AI agent connected:**
   ```bash
   docker compose logs ai-agent | tail -30
   ```
   You should see it register as a LiveKit worker (no crashes, no auth errors in the last lines).

**✅ Done when:** Twilio webhooks point to your domain and the ai-agent logs confirm it's connected to LiveKit.

---

### Phase 9 — Create your account + make a test call ⏱️ ~10 min · difficulty: 🟢 easy

**Goal:** prove the whole system works end-to-end.

1. Public signup is off (`ALLOW_PUBLIC_SIGNUP=false`), so create your account directly from the server:
   ```bash
   docker compose exec backend python scripts/create_user.py --name "You" --email you@you.com
   ```
   It prints a generated password — **copy it now** (it can't be recovered). Or set your own:
   ```bash
   docker compose exec backend python scripts/create_user.py --name "You" --email you@you.com --password 'YourPassword!'
   ```

2. Open your Vercel URL → log in with those credentials.
3. Create a campaign with **one contact (your own mobile number)** → start it.

**✅ Done when:** your phone rings 📞, the AI talks to you, and a row appears in **Call History**
with a transcript and a call outcome. 🎉 **You are live. Congratulations.**

---

## Part 4 — Running it day-to-day (bookmark this)

All commands run in the EC2 SSH terminal, inside the `~/ai-calling` folder.

| I want to… | Command |
|---|---|
| Check if everything is running | `docker compose ps` |
| Watch backend logs live | `docker compose logs -f backend` |
| Watch the AI agent (where call bugs show up) | `docker compose logs -f ai-agent` |
| Restart after changing `.env` | `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d` |
| Pull new code + redeploy | `git pull && docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build` |
| Stop everything | `docker compose down` |

Containers have `restart: unless-stopped` — they come back automatically after a server reboot.

### When a call fails — check in this order (catches ~every bug)

1. Is `BASE_URL` in `.env` exactly `https://api.yourdomain.com`? Did you restart the backend after editing?
2. `docker compose logs -f ai-agent` — the real error almost always shows here, not in Twilio.
3. Is `GOOGLE_API_KEY` set to the **same valid key** as `GEMINI_API_KEY`? A missing or wrong `GOOGLE_API_KEY` breaks the agent silently.
4. Are your campaign **voice IDs real ElevenLabs IDs** (like `qtqlHrXyBpEXHx2JBPgx`)? Names like "alloy" crash TTS.
5. Is the Neon database paused? Free projects auto-pause after 5 days of inactivity.
   Go to [neon.tech](https://neon.tech) → find your project → click **Resume**.

---

## Part 5 — Don't get a surprise bill 💸

**Do this right now:** AWS Console → search bar → **"Billing"** → **Budgets** → **Create budget** →
choose **"Zero spend budget"** → enter your email → create. AWS will email you the **moment**
anything starts costing money. This is free to set up.

### What is free (12 months from signup)

| Resource | Free limit |
|---|---|
| EC2 `t2.micro` | 750 hrs/month (= 1 instance 24/7 ✅) |
| EBS storage | 30 GB/month |
| Data transfer out | 100 GB/month |
| Neon Postgres | Free tier, permanent |
| Upstash Redis | Free tier, permanent |
| Vercel frontend | Free forever (Hobby plan) |

### What costs real money from day 1

| Resource | Cost |
|---|---|
| **Twilio** | ~$1/month per phone number + a few cents per call minute |
| **ElevenLabs / Deepgram** | Tiny usage-based amounts at test volume |

### After 12 months

The `t2.micro` moves to on-demand pricing (~$8–9/month). You can keep it at that price
or upgrade to `t3.small` (~$15/mo, 2 GB RAM) for more headroom.

### Stop the instance when not using it

EC2 → your instance → **Instance state → Stop**. Compute billing pauses immediately.
Your disk (and everything on it) is preserved.

> ⚠️ **IP changes on stop/start:** When you stop and start an EC2 instance, the Public IP
> changes by default. Two options:
> - **Elastic IP** (recommended): allocate one in EC2 → Elastic IPs → Allocate, then associate
>   it with your instance. It's **free while the instance is running** — just don't leave it
>   allocated to a stopped instance or you'll be charged ~$0.005/hr.
> - Or: just update your DNS A record each time you start the instance.

---

## Part 6 — What's next once this works

You've done the hard part. Later, when you want to level up:

1. **Elastic IP** — fix your Public IP so DNS never needs updating → EC2 → Elastic IPs → Allocate → Associate.
2. **Upgrade to `t3.small`** — when you need 2 GB RAM for more stable AI agent performance (~$15/mo).
3. **Sentry error alerts** — set `SENTRY_DSN` in `.env` to get notified on crashes before users complain.
4. **Auto-deploy with GitHub Actions** — add a workflow that SSHs into the server and runs
   `git pull && docker compose up -d --build` on every push to `main`.
5. **Neon / Upstash paid tiers** — only if you exceed free limits (unlikely during validation).

You don't need any of this to launch. Get Phase 9 working first. 💪

---

*Simplest path summary: **1 AWS EC2 `t2.micro`** (free for 12 months · runs `docker compose` =
backend + worker + AI agent + Caddy) · **Neon** (free Postgres) · **Upstash** (free Redis) ·
**Vercel** (free website) · **1 cheap domain** (for permanent HTTPS webhooks).
The production-grade version lives in `docs/DEPLOYMENT.md`.*
