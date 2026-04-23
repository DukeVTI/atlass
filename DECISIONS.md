# Atlas Architecture Decisions Log

## Decision: Single LLM Provider (April 23, 2026)

### Decision
Atlas will use **Anthropic Claude Haiku 4.5** as the sole LLM provider for all tasks in v1.0+.

### Rejected Options
- ❌ **Groq + Claude dual routing** — Rejected for simplicity
- ❌ **Ollama local fallback** — Deferred to post-v1.0 optimization
- ❌ **Claude Sonnet for complex tasks** — Haiku sufficient for current scope

### Rationale
1. **Simplicity** — Single provider, no routing logic, no fallback complexity
2. **Cost** — Haiku is sufficient for current tasks (web search, email, calendar, paystack)
3. **Speed** — Reduced latency (no provider selection overhead)
4. **Maintainability** — Easier debugging, fewer code paths
5. **Future flexibility** — Can add routing/fallbacks in v1.1+ without rearchitecting

### Model Details
- **Model**: `claude-haiku-4-5-20251001`
- **Max tokens**: 700
- **Temperature**: 0.6
- **Context window**: 200k (more than sufficient)

### When This Decision Changes
- **Groq integration**: Consider for v1.1+ if Claude costs become prohibitive
- **Ollama local**: Consider for v1.2+ if GPU VPS is available
- **Sonnet routing**: Consider for v2.0+ if multi-step reasoning fails consistently

---

## Decision: Cloudflare Tunnel Not Required for Layer 1 (April 23, 2026)

### Decision
Cloudflare Tunnel webhook exposure is **deferred to future layers**. Paystack webhooks can be tested locally via ngrok for development.

### Rejected Options
- ❌ **Implement Cloudflare Tunnel in Layer 1** — Adds unnecessary complexity
- ✅ **Local webhook testing** — Use ngrok for dev, configure in production later

### Rationale
1. **Layer 1 focus** — Core infrastructure should stabilize first
2. **Webhook mechanism works** — HMAC verification in place, just not exposed
3. **Development flexibility** — ngrok works fine locally
4. **Future-proof** — Can add Tunnel without code changes

### When This Changes
- Production VPS deployment will require tunnel configuration
- Webhook endpoint is ready (`/webhooks/paystack`) — just not publicly exposed yet

---

## Decision: No Ollama in Layer 1 (April 23, 2026)

### Decision
Local Ollama (Llama 3.1 8B) is **deferred to future layers**. Claude Haiku is the primary inference engine.

### Rejected Options
- ❌ **CPU-only Ollama** — Too slow, not worth the latency
- ❌ **GPU Ollama on VPS** — Requires expensive GPU instance
- ✅ **Claude Haiku API** — Fast, reliable, cost-effective

### Rationale
1. **Cost efficiency** — Cloud API is cheaper than GPU VPS
2. **Speed** — Claude Haiku < 2s responses (faster than local Ollama)
3. **Reliability** — Anthropic API uptime is high
4. **Simplicity** — No model management overhead

### When This Changes
- **v1.1+**: Consider if token costs exceed budget
- **v2.0+**: Multi-provider routing with cost optimization
- GPU instances become cheaper or free tier improves

---

## Updated Documents
- ✅ **AGENTS.md** — Reflects Claude-only approach
- ⚠️ **Atlas_PRD_v2.0.txt** — Notes post-v1.0 optimizations (Groq, Ollama)
- ✅ **system_prompt.py** — Already using Claude
- ✅ **claude_client.py** — Single provider hardcoded

---

## Next Steps
1. Layer 1 is now **internally consistent** (no contradictions)
2. Proceed to Layer 2+ work without refactoring LLM choice
3. Track Anthropic costs for v1.1 routing decision
4. When ready for webhooks: add Cloudflare Tunnel or ngrok config

