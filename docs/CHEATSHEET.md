# 🚀 AI Calling Platform — Operations Cheat Sheet

> **Server:** Ubuntu EC2 · **Folder:** `~/ai-calling`  
> **Stack:** Caddy → FastAPI backend → PostgreSQL · Redis · ARQ Worker · AI Agent

---

## 🔑 SSH into the server

```bash
ssh -i "your-key.pem" ubuntu@<YOUR_EC2_IP>
cd ~/ai-calling
```

---

## ▶️ START everything

```bash
# Start all services (normal restart after changes)
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d

# Start + rebuild images (after code changes / git pull)
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

---

## ⏹️ STOP everything

```bash
# Stop all containers (data is preserved)
docker compose -f docker-compose.yml -f docker-compose.prod.yml down

# Stop AND wipe all data volumes ⚠️ DANGER — deletes DB!
docker compose -f docker-compose.yml -f docker-compose.prod.yml down -v
```

---

## 🔄 RESTART a single service

```bash
# Replace <service> with: caddy | backend | worker | ai-agent | postgres | redis
docker compose -f docker-compose.yml -f docker-compose.prod.yml restart <service>
```

**Examples:**
```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml restart caddy
docker compose -f docker-compose.yml -f docker-compose.prod.yml restart backend
docker compose -f docker-compose.yml -f docker-compose.prod.yml restart ai-agent
```

---

## 📊 STATUS — check if everything is running

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml ps
```

**Healthy output looks like:**
```
NAME                 STATUS
aicalling-backend    Up X hours (healthy)
aicalling-db         Up X hours (healthy)
aicalling-redis      Up X hours (healthy)
aicalling-worker     Up X hours
aicalling-agent      Up X hours
ai-calling-caddy-1   Up X hours   ← must NOT say "Restarting"
```

---

## 🪵 LOGS — debug what's wrong

```bash
# Follow live logs for a service (Ctrl+C to stop)
docker compose -f docker-compose.yml -f docker-compose.prod.yml logs -f <service>

# Last 50 lines only
docker compose -f docker-compose.yml -f docker-compose.prod.yml logs --tail 50 <service>
```

**Most useful:**
```bash
# Caddy (SSL / HTTPS issues)
docker compose -f docker-compose.yml -f docker-compose.prod.yml logs -f caddy

# Backend (API errors, login failures)
docker compose -f docker-compose.yml -f docker-compose.prod.yml logs -f backend

# AI Agent (call issues)
docker compose -f docker-compose.yml -f docker-compose.prod.yml logs -f ai-agent

# Worker (background job issues)
docker compose -f docker-compose.yml -f docker-compose.prod.yml logs -f worker

# All services at once
docker compose -f docker-compose.yml -f docker-compose.prod.yml logs -f
```

---

## 🔧 DEPLOY code updates (git pull workflow)

```bash
# 1. Pull latest code from GitHub
git pull

# 2. Rebuild and restart changed services
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build

# 3. Confirm everything is healthy
docker compose -f docker-compose.yml -f docker-compose.prod.yml ps
```

---

## 🛠️ COMMON FIXES

### Caddy keeps restarting
```bash
# Check the error
docker compose -f docker-compose.yml -f docker-compose.prod.yml logs --tail 30 caddy

# Fix Caddyfile then restart
nano ~/ai-calling/Caddyfile
docker compose -f docker-compose.yml -f docker-compose.prod.yml restart caddy
```

**Correct Caddyfile format (space before { is required!):**
```
v3api.innvox.in {
    reverse_proxy backend:8000
    encode gzip
}
```

### Backend unhealthy / login failing
```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml logs --tail 50 backend
```

### Database connection error
```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml logs --tail 30 postgres
```

### Edit environment variables
```bash
nano ~/ai-calling/.env
# After saving, restart the backend:
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d backend
```

---

## ✅ HEALTH CHECKS

```bash
# API health (should return {"status":"healthy"})
curl https://v3api.innvox.in/health

# Check open ports
sudo ufw status

# Disk space
df -h

# Memory usage
free -h

# Live container resource usage
docker stats
```

---

## ⚡ SHORTCUTS — save typing forever

Add this alias to your server so you never type the long command again:

```bash
echo "alias dcp='docker compose -f docker-compose.yml -f docker-compose.prod.yml'" >> ~/.bashrc
source ~/.bashrc
```

**Then just use:**

| Short command | What it does |
|---|---|
| `dcp ps` | Check status of all services |
| `dcp up -d` | Start everything |
| `dcp down` | Stop everything |
| `dcp up -d --build` | Rebuild + start (after git pull) |
| `dcp restart caddy` | Restart only Caddy |
| `dcp restart backend` | Restart only backend |
| `dcp logs -f caddy` | Live Caddy logs |
| `dcp logs -f backend` | Live backend logs |
| `dcp logs --tail 50 ai-agent` | Last 50 AI agent logs |

---

## 📍 Key URLs

| What | URL |
|---|---|
| API Health | https://v3api.innvox.in/health |
| API Docs (Swagger) | https://v3api.innvox.in/docs |
| Frontend | https://ai-calling-seven.vercel.app |
| Vercel Dashboard | https://vercel.com/dashboard |
| GitHub Repo | https://github.com/ady254/ai-calling |

---

## 🗂️ Key Files on the Server

| File | Purpose |
|---|---|
| `~/ai-calling/.env` | All secrets & API keys |
| `~/ai-calling/Caddyfile` | HTTPS routing (domain → backend) |
| `~/ai-calling/docker-compose.yml` | Main services config |
| `~/ai-calling/docker-compose.prod.yml` | Production overrides (Caddy) |
