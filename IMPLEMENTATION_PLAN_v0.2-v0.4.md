# Atlas Brain Implementation Plan (v0.2 → v0.4)
## Complete Phased Build Plan with Dependencies

**Target**: Complete the intelligent core (memory, communications, finance) with persistent state and proactive capabilities.

---

## **PHASE OVERVIEW**

```
v0.2: Memory System (Week 1-2)
  └─ Layer 1: Memory schemas + retrieval
  └─ Layer 2: Integration with butler loop
  
v0.3: Communications Polish (Week 3-4)
  └─ Layer 1: Email advanced features
  └─ Layer 2: Calendar advanced features
  └─ Layer 3: Integration testing
  
v0.4: Finance + Proactive (Week 5-6)
  └─ Layer 1: Payment alerts (webhook → Telegram)
  └─ Layer 2: Morning briefing assembly
  └─ Layer 3: Scheduled task execution
```

---

## **v0.2: PERSISTENT MEMORY SYSTEM**

### **Current State**
- ✅ PostgreSQL running, audit_logs table initialized
- ✅ ChromaDB container up and healthy
- ✅ Redis running
- ❌ Memory retrieval logic: NOT IMPLEMENTED
- ❌ Vector embeddings: NOT CONFIGURED
- ❌ Episodic memory schema: NOT DEFINED

### **What Needs to Be Built**

#### **Task v0.2.1: Memory Data Models (3-4 hours)**
**Files to create/modify:**
- `services/orchestrator/memory/models.py` (NEW)
  - `EpisodicMemory` — past events, interactions
  - `FactualMemory` — contacts, projects, preferences
  - `ProceduralMemory` — skill definitions
  - `ConversationMemory` — session history
  
- `services/orchestrator/memory/schemas.py` (NEW)
  - PostgreSQL schemas for each memory type
  - Migration script to create tables
  
**Database Tables Needed:**
```sql
episodic_memory (
  id, user_id, timestamp, content_summary, 
  embedding_vector, tags, context, ttl, created_at
)

factual_memory (
  id, user_id, key, value, category, source, 
  last_updated, verified, created_at
)

procedural_memory (
  id, user_id, skill_name, skill_definition, 
  parameters, tags, usage_count, created_at
)

conversation_context (
  id, user_id, session_id, turn_number, 
  user_message, assistant_response, embeddings, created_at
)
```

**Deliverable**: Migration script + schema initialization

---

#### **Task v0.2.2: Vector Embeddings Pipeline (4-5 hours)**
**Files to create:**
- `services/orchestrator/memory/embeddings.py` (NEW)
  - Sentence-transformers integration
  - Async embedding generation (batch + cache)
  - Embedding storage in ChromaDB
  
**Implementation:**
1. Load `sentence-transformers/all-MiniLM-L6-v2` (33MB, CPU-safe)
2. Create ChromaDB collections:
   - `episodic_memories`
   - `conversation_history`
3. Batch embedding + storage (async)
4. Similarity search (cosine distance, top-k retrieval)

**Pseudo-code:**
```python
# In memory/embeddings.py
async def embed_text(text: str) -> np.ndarray
async def store_episodic(memory_dict: dict) -> uuid
async def retrieve_similar(query: str, limit: int = 5) -> List[dict]
```

**Dependencies**: sentence-transformers, numpy

**Deliverable**: Embeddings module with ChromaDB integration

---

#### **Task v0.2.3: Memory Service API (3-4 hours)**
**Files to modify:**
- `services/memory/main.py` (REPLACE STUB)
  - FastAPI endpoints for memory read/write
  - Health check
  
**Endpoints to create:**
```
POST   /memory/episodic    — Store event
GET    /memory/search?q=   — Search by semantic similarity
POST   /memory/factual     — Store fact (contact, preference, etc.)
GET    /memory/facts/:category
DELETE /memory/clear/{type} — User can delete memories
```

**Deliverable**: REST API with CRUD + search

---

#### **Task v0.2.4: Butler Loop Integration (2-3 hours)**
**Files to modify:**
- `services/orchestrator/butler_loop.py`
  - Before LLM call: Retrieve relevant episodic memory
  - Append to context window
  
- `services/orchestrator/tools/registry.py`
  - After tool execution: Store result as episodic event
  
**Flow:**
```
1. User message arrives
2. Query memory service: "Find relevant past interactions"
3. Append top-5 memories to system prompt
4. LLM reasons with context + tools
5. After tool execution: Store [tool, input, result, timestamp] as episodic memory
6. Store in both PostgreSQL (audit) + ChromaDB (semantic)
```

**Pseudo-code:**
```python
# In butler_loop.py before LLM call
if iteration == 1:
    relevant_memories = await memory.retrieve_similar(user_message, limit=5)
    context_prompt = format_memories_for_context(relevant_memories)
    messages[0]["content"] = system_prompt + context_prompt

# After tool execution
await memory.store_episodic({
    "event": f"Tool {tool_name} executed",
    "input": tool_input,
    "result": tool_result,
    "user_id": user_id,
    "timestamp": now()
})
```

**Deliverable**: Memory-aware butler loop

---

### **v0.2 Deliverables Summary**
| Item | Status | Effort |
|------|--------|--------|
| PostgreSQL schemas | ❌ To build | 1h |
| ChromaDB collections | ❌ To build | 1h |
| Embeddings module | ❌ To build | 4h |
| Memory service API | ❌ To build | 3h |
| Butler loop integration | ⚠️ Partial | 2h |
| **Total v0.2** | | **~12 hours** |

---

## **v0.3: COMMUNICATIONS POLISH**

### **Current State**
- ✅ GmailReadTool (fetch unread)
- ✅ GmailDraftTool (create draft)
- ✅ GmailSendTool (send, destructive)
- ❌ Email search, priority triage, thread handling
- ✅ CalendarReadTool (fetch upcoming)
- ✅ CalendarCreateTool (create + conflict detection)
- ❌ Calendar updates, delete, smart scheduling

### **What Needs to Be Built**

#### **Task v0.3.1: Email Advanced Features (5-6 hours)**

**Task v0.3.1a: Email Search Tool (2 hours)**
```python
# services/orchestrator/tools/gmail_search.py
class GmailSearchTool(Tool):
    name = "search_email"
    description = "Semantic + keyword search across email history"
    
    # Inputs:
    # - query: "invoice from Dana"
    # - date_from: "2026-03-01"
    # - date_to: "2026-04-30"
    # - limit: 10
    
    # Returns: List of emails with snippet, sender, date
```

**Task v0.3.1b: Email Priority Triage (2 hours)**
```python
# services/orchestrator/tools/gmail_triage.py
class GmailTriageTool(Tool):
    name = "triage_emails"
    description = "Categorize unread emails by urgency"
    
    # Inputs: max_results (default 10)
    
    # Logic:
    # 1. Fetch unread emails
    # 2. For each: Extract [sender, subject, snippet]
    # 3. Claude scores urgency (1-10) based on:
    #    - Sender is frequent contact? +3
    #    - Keywords: "urgent", "ASAP", "payment"? +4
    #    - Matches project names in memory? +2
    # 4. Return sorted by urgency score
```

**Task v0.3.1c: Email Thread Handling (2 hours)**
```python
# services/orchestrator/tools/gmail_utils.py
class EmailThreadHelper:
    async def get_thread_context(email_id: str) -> dict
        # Fetch full thread (in/out)
        # Return: [list of prior emails in thread]
    
    async def reply_to_thread(email_id: str, reply_text: str)
        # Uses gmail.send with in_reply_to header
```

**Subtasks:**
- Add `search_email` tool to registry
- Add `triage_emails` tool to registry
- Enhance `GmailReadTool` to return thread context
- Test with real Gmail account

**Deliverable**: 3 new email tools + thread context

---

#### **Task v0.3.2: Calendar Advanced Features (5-6 hours)**

**Task v0.3.2a: Calendar Update/Delete Tools (2 hours)**
```python
# services/orchestrator/tools/calendar_update.py
class CalendarUpdateTool(Tool):
    name = "update_event"
    is_destructive = False
    
    # Inputs: event_id, field (title/time/attendees), new_value
    # Returns: Updated event details
    
class CalendarDeleteTool(Tool):
    name = "delete_event"
    is_destructive = True  # Confirmation gate
    
    # Inputs: event_id, reason (optional)
```

**Task v0.3.2b: Smart Scheduling Tool (2 hours)**
```python
# services/orchestrator/tools/calendar_smart.py
class CalendarSmartScheduleTool(Tool):
    name = "find_free_slot"
    
    # Inputs:
    # - duration_minutes: 90
    # - before_time: "15:00" (3pm)
    # - within_days: 7
    # - exclude_days: ["weekends"]
    
    # Logic:
    # 1. Fetch all events for next 7 days
    # 2. Find gaps >= 90 minutes before 3pm
    # 3. Return: [3 best options with times]
```

**Task v0.3.2c: Calendar Reminders (1 hour)**
```python
# services/orchestrator/tools/calendar_reminders.py
class CalendarReminderTool(Tool):
    name = "set_reminder"
    
    # Inputs: event_id, lead_time_minutes (15, 5, 60)
    # Stores reminder in PostgreSQL
    # Scheduler (Celery Beat) checks every 5 minutes, sends Telegram alert
```

**Subtasks:**
- Add update/delete tools to registry
- Add smart scheduling tool
- Add reminder setting tool
- Create reminder scheduler job (requires Celery Beat setup)

**Deliverable**: 4 new calendar tools + scheduler job

---

#### **Task v0.3.3: Multi-Provider Email Support (4-5 hours)**

**Optional, but PRD requires it:**
```python
# services/orchestrator/tools/email_generic.py
class EmailIMAPTool(Tool):
    # For non-Gmail providers (Outlook, generic SMTP)
    # Inputs: email_address, app_password
    # Uses: imaplib + smtplib (already in stdlib)
```

**Deliverable**: IMAP fallback implementation (optional for v0.3)

---

### **v0.3 Deliverables Summary**
| Item | Status | Effort |
|------|--------|--------|
| Email search tool | ❌ To build | 2h |
| Email triage tool | ❌ To build | 2h |
| Thread context helper | ❌ To build | 2h |
| Calendar update/delete | ❌ To build | 2h |
| Smart scheduling tool | ❌ To build | 2h |
| Calendar reminders | ❌ To build | 1h |
| IMAP fallback (optional) | ❌ To build | 4h |
| Integration testing | ❌ To do | 2h |
| **Total v0.3** | | **~15 hours** |

---

## **v0.4: FINANCE + PROACTIVE ALERTS**

### **Current State**
- ✅ Paystack tools (read balance, customer, transactions)
- ✅ Paystack transfer tool (write, destructive)
- ✅ Paystack webhook endpoint (`/webhooks/paystack`)
- ❌ Webhook notification logic (receives but doesn't notify)
- ❌ Morning briefing assembly
- ❌ Scheduled task execution (Celery Beat)

### **What Needs to Be Built**

#### **Task v0.4.1: Payment Alert System (4-5 hours)**

**Task v0.4.1a: Webhook Event Handler (2 hours)**
```python
# services/api/webhooks/paystack_handler.py
async def handle_paystack_webhook(event: str, data: dict):
    if event == "charge.success":
        amount = data["amount"] / 100
        customer = data["customer"]["email"]
        
        # Store in audit log ✅ (already done)
        
        # NEW: Push notification to Telegram
        message = f"""
        💳 Payment Received!
        Amount: ₦{amount:,.0f}
        From: {customer}
        Ref: {data['reference']}
        """
        
        await telegram_notify(user_id=DUKE_ID, text=message)
```

**Task v0.4.1b: Telegram Notification Service (2 hours)**
```python
# services/orchestrator/telegram_notifier.py
class TelegramNotifier:
    async def send_alert(user_id: int, message: str, buttons: List = None)
        # Uses Telegram bot to send rich message
        # Optional inline keyboard for actions (e.g., "View transaction")
        
    async def send_payment_alert(amount: float, customer: str, ref: str)
    async def send_briefing(briefing_dict: dict)
    async def send_error_alert(error_msg: str)
```

**Deliverable**: Payment alerts in Telegram (real-time on webhook)

---

#### **Task v0.4.2: Morning Briefing Assembly (5-6 hours)**

**Task v0.4.2a: Briefing Builder (3 hours)**
```python
# services/orchestrator/briefing.py
class MorningBriefing:
    async def assemble(user_id: int) -> str:
        """Compile all daily intel."""
        
        briefing = {}
        
        # 1. Today's calendar events
        briefing["calendar"] = await get_calendar_for_today()
        # Returns: List of events with times
        
        # 2. Unread email summary
        briefing["email"] = await get_email_triage()
        # Returns: Urgent emails, count by sender
        
        # 3. Paystack overnight activity
        briefing["finance"] = await get_overnight_paystack_activity()
        # Returns: New payments, balance, pending transfers
        
        # 4. Overdue follow-ups
        briefing["follow_ups"] = await get_overdue_tasks(user_id)
        # Returns: Emails awaiting reply >3 days
        
        # 5. Active reminders
        briefing["reminders"] = await get_today_reminders()
        
        # Format as Telegram message
        return format_briefing_markdown(briefing)
```

**Task v0.4.2b: Briefing Formatter (1 hour)**
```python
# Format briefing as readable Telegram message
def format_briefing_markdown(briefing: dict) -> str:
    """
    Returns:
    ---
    🌅 Good morning, sir!
    
    📅 Today's Agenda
    • 10:00 AM - Client call (Zoom)
    • 2:00 PM - Team standup
    
    📧 Unread Email
    🔴 2 urgent: Dana (invoice), Samuel (approval needed)
    🟡 8 other
    
    💳 Overnight
    ₦50,000 received from Jane
    Balance: ₦250,000
    
    ⏰ Follow-ups
    • Dana (5 days awaiting reply)
    
    ✅ Reminders
    • Prepare presentation for Friday
    ---
    """
```

**Deliverable**: Briefing assembly + formatting

---

#### **Task v0.4.3: Scheduled Execution (Celery Beat Setup) (4-5 hours)**

**Task v0.4.3a: Celery + Celery Beat Configuration (3 hours)**
```python
# services/orchestrator/celery_app.py (NEW)
from celery import Celery
from celery.schedules import crontab

app = Celery(
    'atlas',
    broker=os.getenv("REDIS_URL"),
    backend=os.getenv("REDIS_URL")
)

# Daily morning briefing at 7:00 AM local time
@app.task(name='send_morning_briefing')
def send_morning_briefing(user_id: int):
    briefing = asyncio.run(MorningBriefing.assemble(user_id))
    asyncio.run(TelegramNotifier.send_briefing(user_id, briefing))

# Check reminders every 5 minutes
@app.task(name='check_reminders')
def check_reminders(user_id: int):
    reminders = asyncio.run(get_due_reminders(user_id))
    for reminder in reminders:
        asyncio.run(TelegramNotifier.send_alert(user_id, reminder["text"]))

# Configure schedule
app.conf.beat_schedule = {
    'morning-briefing': {
        'task': 'send_morning_briefing',
        'schedule': crontab(hour=7, minute=0),  # 7:00 AM daily
        'args': (DUKE_USER_ID,),
    },
    'check-reminders': {
        'task': 'check_reminders',
        'schedule': crontab(minute='*/5'),  # Every 5 minutes
        'args': (DUKE_USER_ID,),
    },
}
```

**Task v0.4.3b: Docker Celery Service (1 hour)**
```yaml
# Add to docker-compose.yml

celery-worker:
  build:
    context: ./services/orchestrator
    dockerfile: Dockerfile.celery
  environment:
    REDIS_URL: redis://:${REDIS_PASSWORD}@redis:6379/0
  depends_on:
    - redis
    - orchestrator
  command: celery -A celery_app worker --loglevel=info

celery-beat:
  build:
    context: ./services/orchestrator
    dockerfile: Dockerfile.celery
  environment:
    REDIS_URL: redis://:${REDIS_PASSWORD}@redis:6379/0
  depends_on:
    - redis
  command: celery -A celery_app beat --loglevel=info
```

**Deliverable**: Celery + Celery Beat configured, morning briefing scheduled

---

#### **Task v0.4.4: Integration Testing (2-3 hours)**

Test scenarios:
1. Paystack webhook fires → Telegram alert sent
2. Morning briefing scheduled → Telegram message at 7:00 AM
3. Calendar reminder triggered → Telegram alert 15 min before event
4. Email triage works → High-priority emails surfaced

---

### **v0.4 Deliverables Summary**
| Item | Status | Effort |
|------|--------|--------|
| Payment alert handler | ❌ To build | 2h |
| Telegram notifier service | ❌ To build | 2h |
| Briefing builder | ❌ To build | 3h |
| Briefing formatter | ❌ To build | 1h |
| Celery + Celery Beat config | ❌ To build | 3h |
| Docker Celery services | ❌ To build | 1h |
| Integration testing | ❌ To do | 2h |
| **Total v0.4** | | **~15 hours** |

---

## **DEPENDENCY GRAPH**

```
v0.2 (Memory) [12h]
    └─ Must complete before v0.3 + v0.4
    └─ Required by: Butler loop context

v0.3a (Email Tools) [6h]
    └─ Independent of v0.2
    └─ Prerequisite for: Morning briefing (emails in briefing)

v0.3b (Calendar Tools) [6h]
    └─ Independent of v0.2
    └─ Prerequisite for: Morning briefing + reminders

v0.4a (Payment Alerts) [4h]
    └─ Can start after v0.2 (nice-to-have: store in memory)
    └─ Independent of v0.3

v0.4b (Morning Briefing) [6h]
    └─ DEPENDS ON v0.3a + v0.3b (to fetch calendar + email)
    └─ DEPENDS ON Paystack query tools (v0.1)

v0.4c (Celery + Scheduling) [5h]
    └─ DEPENDS ON v0.4b (needs briefing builder)
    └─ DEPENDS ON v0.2 (optionally, for reminder state)
```

---

## **RECOMMENDED BUILD ORDER**

### **Week 1 (v0.2 Focus)**
1. **v0.2.1** — Memory models + PostgreSQL schemas (1.5h)
2. **v0.2.2** — Embeddings + ChromaDB (5h)
3. **v0.2.3** — Memory service API (4h)
4. **v0.2.4** — Butler loop integration (2.5h)

**Output**: Persistent memory working. Butler loop uses episodic context.

---

### **Week 2 (v0.3 Parallel Start)**
1. **v0.3.1a-c** — Email tools (search, triage, threads) (6h)
2. **v0.3.2a-c** — Calendar tools (update, smart schedule, reminders) (5h)
3. **v0.4a** — Payment alerts (4h)
4. **Testing** — All new tools work individually (2h)

**Output**: Email + calendar fully featured. Payment alerts working.

---

### **Week 3 (v0.4 Assembly)**
1. **v0.4b** — Morning briefing builder (6h)
2. **v0.4c** — Celery + scheduling (5h)
3. **Integration testing** (2h)
4. **Optional**: v0.3.3 IMAP fallback (4h)

**Output**: Complete brain core. Morning briefing + scheduled tasks working.

---

## **EFFORT SUMMARY**

| Phase | Subtasks | Hours | Days |
|-------|----------|-------|------|
| **v0.2** | 4 | 12 | 1.5 |
| **v0.3** | 6 | 15 | 2 |
| **v0.4** | 4 | 15 | 2 |
| **Total** | **14** | **42** | **5.25 days** |

**Realistic estimate** (with testing, debugging): **6-7 days of focused work**

---

## **RISKS & MITIGATIONS**

| Risk | Mitigation |
|------|-----------|
| Embeddings slow down (ChromaDB) | Cache similar results, limit retrieval to top-5 |
| Celery scheduling misses timezone | Store user timezone in PostgreSQL, use pytz |
| Gmail API rate limits | Implement exponential backoff + queue |
| Memory grows unbounded | Implement TTL (90 days for episodic) + archival |
| Webhook delivery fails | Store failed events in PostgreSQL, retry on next check |

---

## **FILES TO CREATE/MODIFY SUMMARY**

### **Create (New)**
- `services/orchestrator/memory/models.py`
- `services/orchestrator/memory/schemas.py`
- `services/orchestrator/memory/embeddings.py`
- `services/memory/main.py` (replace stub)
- `services/orchestrator/tools/gmail_search.py`
- `services/orchestrator/tools/gmail_triage.py`
- `services/orchestrator/tools/gmail_utils.py`
- `services/orchestrator/tools/calendar_update.py`
- `services/orchestrator/tools/calendar_smart.py`
- `services/orchestrator/tools/calendar_reminders.py`
- `services/orchestrator/briefing.py`
- `services/orchestrator/telegram_notifier.py`
- `services/orchestrator/celery_app.py`
- `services/orchestrator/Dockerfile.celery`

### **Modify (Existing)**
- `services/orchestrator/main.py` — Add memory service integration
- `services/orchestrator/butler_loop.py` — Add memory retrieval
- `services/orchestrator/tools/registry.py` — Register new tools + episodic logging
- `services/api/main.py` — Enhance webhook handler
- `docker-compose.yml` — Add celery-worker + celery-beat services
- `.env.example` — Add Celery config vars

---

## **SUCCESS CRITERIA**

✅ **v0.2 Done When:**
- Memory schema initialized in PostgreSQL
- ChromaDB collections populated + retrievable
- Butler loop passes episodic context to Claude
- Tool execution stored as episodic memory

✅ **v0.3 Done When:**
- All 6 email/calendar tools registered + tested
- Email search + triage work end-to-end
- Calendar smart scheduling + reminders work
- Tools pass integration tests

✅ **v0.4 Done When:**
- Paystack webhook → Telegram alert works
- Morning briefing generated + sent at 7 AM
- All calendar/email data in briefing correct
- Celery Beat scheduler runs reliably

---

**Would you like me to proceed with this plan, or would you like to adjust scope/order?**
