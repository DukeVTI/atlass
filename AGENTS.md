# ATLAS — Agent Operating Instructions
# Place this file at the root of the project as AGENTS.md

## Who You Are
You are building Atlas — a distributed, cloud-resident personal AI agent
for a single user (Duke). The system has three tiers:
- VPS Brain (Contabo, 29GB RAM, 8 cores, Ubuntu 22.04) — always-on cloud host
- PC Worker — lightweight local daemon on the user's laptop
- Mobile Worker — React Native app (built last)

## The PRD
The full Product Requirements Document is in /docs/Atlas_PRD_v2.0.md.
Read it before starting any feature. Every decision must align with it.
When in doubt, refer back to the PRD. Do not invent features not in the PRD.

## Build Philosophy
- Build in layers. Do not start Layer N+1 until Layer N is working and tested.
- Every service runs in Docker. No exceptions. Local dev = Docker Compose.
- Production deployment = same Docker Compose on VPS via GitHub Actions.
- Never hardcode secrets. All secrets go in .env (gitignored) and the VPS keyring.
- Every tool call Atlas makes must be written to the audit log. No exceptions.
- Destructive actions (delete file, send email, transfer money) must have a
  confirmation gate enforced at the executor layer — not just checked in the LLM.

## Deployment & Update Rules (CRITICAL GOTCHA)
- **Code Updates:** Services like `api` and `orchestrator` use `COPY . .` in their Dockerfiles without live volume mounts for code. This means **code changes are baked into the image**.
- To apply code updates from git, you **MUST** run `docker-compose up -d --build <service_name>`. Running `docker-compose restart` will only restart the old image with the stale code.

## Current Build Layer
Layer 1 — Infrastructure
- Docker Compose scaffold with all services defined
- PostgreSQL + Redis + ChromaDB containers up and healthy
- FastAPI API service with health checks and audit logging
- Telegram bot gateway with command handlers
- Claude 4.6 Sonnet LLM integration (single provider, no fallbacks)
- Core tool system (web search, Gmail, Calendar, Paystack)
- Confirmation gates for destructive actions

Do not touch Layer 2 (PC Worker) until Layer 1 passes all health checks.
Note: Ollama and Cloudflare Tunnel are planned for future layers (cost optimization and webhook exposure, respectively).

## Project Structure
atlas/
├── docker-compose.yml
├── docker-compose.prod.yml
├── .env.example              # committed — shows required vars, no values
├── .env                      # gitignored — real values
├── services/
│   ├── bot/                  # Telegram gateway (Python)
│   ├── orchestrator/         # brain + LLM routing (Python)
│   ├── api/                  # FastAPI — webhooks + worker comms
│   ├── memory/               # ChromaDB + Postgres interfaces
│   └── whatsapp/             # Baileys Node.js sidecar
├── workers/
│   └── pc-worker/            # laptop daemon (Python)
├── skills/                   # YAML skill definitions
├── scripts/                  # setup, deploy, migration scripts
└── docs/                     # PRD + architecture notes

## LLM Routing Logic
All tasks → Anthropic Claude 4.6 Sonnet (`claude-sonnet-4-6`) — single provider, all cases.
No routing logic. No fallbacks. No secondary providers.
Note: Groq integration and cost-based LLM routing are post-v1.0 optimizations.

## Tech Stack (Do Not Deviate Without Asking)
- Python 3.11+ for all backend services
- FastAPI for the API server
- Redis for caching and task queue (Celery configured in future layer)
- PostgreSQL + SQLAlchemy (async) for structured data
- ChromaDB for vector memory
- Anthropic Claude 4.6 Sonnet (`claude-sonnet-4-6`) — sole LLM for all tasks
- python-telegram-bot v20 (async) for Telegram
- Baileys (Node.js) for WhatsApp — runs as a sidecar, called via HTTP
- Docker + Docker Compose for all services
- GitHub Actions for CI/CD to VPS

## Code Standards
- All Python code must be async where possible (asyncio, async SQLAlchemy)
- Type hints on every function signature
- Every module has a docstring explaining its purpose
- No print() statements — use Python logging module throughout
- All database operations wrapped in try/except with specific error handling
- No TODO comments left in committed code — either implement it or open an issue

## Security Rules (Non-Negotiable)
- Zero plaintext secrets anywhere in code or committed files
- All API keys loaded from environment variables only
- Audit log must be written before AND after every tool call
- Confirmation gates enforced in executor.py — never only in the LLM prompt
- PC worker communicates with VPS only via authenticated WebSocket
- Telegram bot ignores all messages from user IDs not in ALLOWED_USER_IDS

## When You're Unsure
1. Re-read the relevant PRD section in /docs/Atlas_PRD_v2.0.md
2. Ask before implementing — do not guess on architecture decisions
3. If two approaches are equally valid, pick the simpler one
4. Never refactor working code to add a feature — extend it

## Definition of Done for Each Layer
A layer is done when:
- All services in that layer start without errors via docker-compose up
- A test script in /scripts/ validates the layer's core functionality
- The audit log is writing correctly
- Duke has confirmed it works in his environment