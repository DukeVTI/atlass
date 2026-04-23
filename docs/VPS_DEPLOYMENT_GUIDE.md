# Atlas VPS Deployment - Quick Start Guide

## Your VPS Details
- **IP**: 213.136.65.25
- **Username**: root
- **Password**: Samuelisastar
- **Specs**: 95GB RAM, 700GB SSD, 18 vCPU

---

## Step 1: Connect to VPS via SSH

**Windows (PowerShell):**
```powershell
ssh root@213.136.65.25
# When prompted, enter password: Samuelisastar
```

**Mac/Linux:**
```bash
ssh root@213.136.65.25
# When prompted, enter password: Samuelisastar
```

---

## Step 2: Prepare Deployment Script

Once connected to VPS, run:

```bash
# Download deployment script
cd /tmp
curl -fsSL https://raw.githubusercontent.com/DukeVTI/atlass/main/scripts/deploy_vps_v2.sh -o deploy.sh
chmod +x deploy.sh

# Run deployment (automated setup)
sudo bash deploy.sh
```

**What the script does:**
- ✅ Updates system packages
- ✅ Installs Docker + Docker Compose
- ✅ Clones Atlas repo from GitHub
- ✅ Creates .env template
- ✅ Initializes PostgreSQL volumes
- ✅ Deploys all services
- ✅ Verifies health

---

## Step 3: Configure Secrets (.env)

When the script pauses, edit `.env`:

```bash
nano /home/atlas/atlass/.env
```

**Fill in these values:**

```env
# PostgreSQL - Use a STRONG password!
POSTGRES_PASSWORD=YourStrongPassword123!

# Redis
REDIS_PASSWORD=RedisSecurePass456!

# Anthropic (get from https://console.anthropic.com/)
ANTHROPIC_API_KEY=sk-ant-xxxxxxxxxxxxxxxxxxxxx

# Telegram Bot (from @BotFather)
TELEGRAM_BOT_TOKEN=123456789:ABCDEFGhijklmnopqrstuvwxyz

# Your Telegram User ID (get from @userinfobot)
ALLOWED_USER_IDS=123456789

# Worker secret (any random string)
PC_WORKER_SECRET=SuperSecretWorkerKey123

# Log level
LOG_LEVEL=INFO
```

**Save & Exit:** Press `Ctrl+O`, then `Enter`, then `Ctrl+X`

---

## Step 4: Resume Deployment

After editing `.env`, re-run script:

```bash
cd /home/atlas/atlass
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

---

## Step 5: Verify Services

```bash
# Check all services are running
docker compose ps

# Expected output:
# postgres       Up (healthy)
# redis          Up (healthy)
# api            Up (healthy)
# orchestrator   Up (healthy)
# memory         Up (healthy)
# bot            Up (healthy)
# whatsapp       Up (healthy)
# chromadb       Up

# View memory service logs (most important)
docker compose logs memory --tail 100

# Should see:
# ✓ PostgreSQL connection successful
# ✓ Memory schema initialized successfully
# ✓ pgvector extension enabled (or JSONB fallback)
# ✓ Memory system fully initialized (PostgreSQL + pgvector)
# ✅ Memory Service fully operational
```

---

## Step 6: Test Everything

**Test memory endpoint:**
```bash
curl -X POST http://localhost:8002/health
# Expected: {"status":"ok"}
```

**Test bot (Telegram):**
1. Open Telegram
2. Start chat with your bot
3. Send: `/start`
4. Bot should respond with "Hello! Atlas is running."

**View all logs:**
```bash
docker compose logs -f
# Press Ctrl+C to exit
```

---

## Troubleshooting

**If memory service keeps restarting:**
```bash
docker compose logs memory --tail 100
# Check for errors (usually schema or database issues)

# Restart with clean volumes:
docker compose down -v
docker compose up -d
```

**If PostgreSQL won't start:**
```bash
# Check permissions
ls -la /home/atlas/atlass/postgres_data

# Rebuild postgres container
docker compose down -v
docker compose up -d postgres
sleep 10
docker compose up -d
```

**If bot doesn't respond:**
```bash
docker compose logs bot --tail 50
# Check TELEGRAM_BOT_TOKEN in .env is correct
```

---

## Useful Commands

```bash
# Go to Atlas directory
cd /home/atlas/atlass

# View logs
docker compose logs -f [service_name]

# Restart a service
docker compose restart [service_name]

# Stop all services
docker compose down

# Start all services
docker compose up -d

# Full rebuild
docker compose down -v
docker compose build
docker compose up -d

# Check disk usage
df -h

# Check memory
free -h

# Monitor in real-time
docker stats
```

---

## Next: v0.3 Planning

Once v0.2 is stable on VPS, we can plan v0.3:
- Enhanced email/calendar tools
- Celery task queue for async operations
- Briefing generation system

But first: **get v0.2 live and test for 24-48 hours!**

---

## Support

If issues arise:
1. Check logs: `docker compose logs [service]`
2. Verify .env values are correct
3. Ensure Docker/Docker Compose installed: `docker --version`
4. Check disk space: `df -h` (should be >50GB free)
5. Restart services: `docker compose down && docker compose up -d`
