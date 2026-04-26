"""
Atlas Bot — Proactive Alerts Module
-------------------------------------
Four autonomous alert loops that run on the bot's job_queue (APScheduler):

1. Payment Alert     — polls Redis for Paystack webhook notifications  (every 30s)
2. Email Alert       — polls orchestrator for urgent new emails         (every 5 min)
3. Meeting Reminder  — checks today's calendar for imminent events      (every 1 min)
4. Worker Offline    — checks if PC worker has disconnected             (every 10 min)

All alerts send directly to Telegram via context.bot.send_message().
Deduplication is handled via in-process sets (reset on restart) — 
Redis-backed dedup is used for payment alerts to survive restarts.

Urgency scoring for emails uses a pure algorithmic approach (zero LLM calls):
  - Sender scoring     (VIP list, reply history, noreply detection)
  - Subject keywords   (weighted urgent/spam keyword matching)
  - Reply bonus        (subject starts with Re:)
  - Recency bonus      (email arrived < 10 min ago)
"""

import logging
import os
import json
from datetime import datetime, timezone, timedelta

import httpx
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from auth import ALLOWED_IDS

logger = logging.getLogger("atlas.bot.alerts")

# ─── Config ───────────────────────────────────────────────────────────────────

ORCHESTRATOR_URL = os.environ.get("ORCHESTRATOR_URL", "http://orchestrator:8001")
API_URL = os.environ.get("API_URL", "http://api:8000")
REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")

URGENT_EMAIL_THRESHOLD = int(os.environ.get("URGENT_EMAIL_SCORE_THRESHOLD", "60"))


def _safe(text: str) -> str:
    """Escape characters that break Telegram MarkdownV1 parsing."""
    for ch in ("*", "_", "`", "["):
        text = text.replace(ch, f"\\{ch}")
    return text

# VIP contacts — comma-separated full emails or domains in .env
_vip_emails = set(e.strip().lower() for e in os.environ.get("ALERT_VIP_EMAILS", "").split(",") if e.strip())
_vip_domains = set(d.strip().lower() for d in os.environ.get("ALERT_VIP_DOMAINS", "paystack.com").split(",") if d.strip())

# In-process deduplication sets (reset on restart)
_alerted_email_ids: set[str] = set()
_alerted_meeting_ids: set[str] = set()
_worker_offline_alerted: bool = False

# ─── Urgency Scoring Algorithm ────────────────────────────────────────────────

URGENT_KEYWORDS: dict[str, int] = {
    # Financial
    "payment": 15, "invoice": 15, "overdue": 20, "failed": 20, "declined": 20,
    "transfer": 10, "refund": 10, "charge": 10,
    # Emergency
    "urgent": 20, "asap": 20, "emergency": 25, "critical": 20, "immediately": 15,
    # Business / Legal
    "deadline": 15, "legal": 15, "contract": 10, "proposal": 8,
    # Technical
    "down": 15, "outage": 20, "error": 10, "broken": 15, "alert": 10,
}

SPAM_KEYWORDS: dict[str, int] = {
    "unsubscribe": -30, "newsletter": -30, "digest": -25,
    "% off": -25, "sale": -20, "offer": -20, "deal": -15,
    "notification": -10, "weekly": -15, "monthly": -15,
}


def _score_email(email_id: str, sender: str, subject: str, snippet: str, date_ms: int) -> int:
    """
    Pure algorithmic urgency scorer. Returns 0-100.
    No LLM calls. Runs in microseconds.
    """
    score = 0
    sender_lower = sender.lower()
    subject_lower = subject.lower()
    sender_domain = sender_lower.split("@")[-1].strip(">").strip() if "@" in sender_lower else ""

    # ── Sender Score (0-40 pts) ──────────────────────────────────────────────
    if any(vip in sender_lower for vip in _vip_emails):
        score += 40
    elif sender_domain and any(d in sender_domain for d in _vip_domains):
        score += 25
    elif "noreply" in sender_lower or "no-reply" in sender_lower or "donotreply" in sender_lower:
        score -= 20

    # ── Subject Keyword Score (capped at +40, uncapped negative) ────────────
    keyword_score = 0
    full_text = f"{subject_lower} {snippet.lower()}"
    for kw, pts in URGENT_KEYWORDS.items():
        if kw in full_text:
            keyword_score += pts
    for kw, pts in SPAM_KEYWORDS.items():
        if kw in full_text:
            keyword_score += pts  # pts are already negative

    score += min(keyword_score, 40) if keyword_score > 0 else keyword_score

    # ── Reply Bonus (+10) ───────────────────────────────────────────────────
    if subject_lower.startswith("re:") or subject_lower.startswith("re "):
        score += 10

    # ── Recency Bonus (+10 if < 10 min old) ─────────────────────────────────
    if date_ms:
        email_age_minutes = (datetime.now(timezone.utc).timestamp() * 1000 - date_ms) / 60000
        if email_age_minutes < 10:
            score += 10

    return max(0, min(score, 100))


# ─── Alert 1: Paystack Payment ────────────────────────────────────────────────

async def check_payment_alerts(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Polls Redis for Paystack payment notifications pushed by the API webhook handler.
    Fires immediately when a payment arrives (poll every 30s).
    """
    try:
        import redis.asyncio as aioredis
        r = aioredis.from_url(REDIS_URL, decode_responses=True)

        while True:
            item = await r.lpop("atlas:notifications:telegram")
            if not item:
                break
            payload = json.loads(item)
            if payload.get("type") == "paystack_payment":
                text = payload.get("text", "💰 A payment just came in, sir.")
                for user_id in ALLOWED_IDS:
                    await context.bot.send_message(
                        chat_id=user_id,
                        text=text,
                        parse_mode=ParseMode.MARKDOWN,
                    )
                logger.info("Payment alert sent: %s", payload.get("reference"))

        await r.aclose()
    except Exception as e:
        logger.error("Payment alert check failed: %s", e)


# ─── Alert 2: Urgent Email ────────────────────────────────────────────────────

async def check_email_alerts(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Polls orchestrator for recent unread emails, scores them algorithmically,
    and alerts if score >= URGENT_EMAIL_THRESHOLD. Zero LLM calls.
    """
    global _alerted_email_ids
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(f"{ORCHESTRATOR_URL}/alerts/emails")
            if resp.status_code != 200:
                logger.warning("Email alert endpoint returned %d", resp.status_code)
                return
            emails = resp.json().get("emails", [])

        for email in emails:
            email_id = email.get("id")
            if not email_id or email_id in _alerted_email_ids:
                continue

            score = _score_email(
                email_id=email_id,
                sender=email.get("sender", ""),
                subject=email.get("subject", ""),
                snippet=email.get("snippet", ""),
                date_ms=email.get("date_ms", 0),
            )

            logger.debug("Email %s scored %d (threshold %d)", email_id[:8], score, URGENT_EMAIL_THRESHOLD)

            if score >= URGENT_EMAIL_THRESHOLD:
                flag = "🚨 Heads up, looks urgent" if score >= 80 else "📧 Something worth your attention"
                text = (
                    f"{flag} —\n"
                    f"From {_safe(email.get('sender', 'Unknown'))}\n"
                    f"\"{_safe(email.get('subject', 'No Subject'))}\""
                    + (f"\n\n{_safe(email.get('snippet', '')[:200])}" if email.get('snippet') else "")
                    + "\n\nWant me to pull it up?"
                )
                for user_id in ALLOWED_IDS:
                    await context.bot.send_message(
                        chat_id=user_id,
                        text=text,
                        parse_mode=ParseMode.MARKDOWN,
                    )
                _alerted_email_ids.add(email_id)
                logger.info("Email alert sent for %s (score=%d)", email_id[:8], score)

    except Exception as e:
        logger.error("Email alert check failed: %s", e)


# ─── Alert 3: Meeting Reminder ────────────────────────────────────────────────

async def check_meeting_reminders(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Checks upcoming calendar events every minute.
    Fires a Telegram reminder at 15 min and 5 min before each event.
    """
    global _alerted_meeting_ids
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"{ORCHESTRATOR_URL}/alerts/calendar")
            if resp.status_code != 200:
                return
            events = resp.json().get("events", [])

        now = datetime.now(timezone.utc)

        for event in events:
            event_id = event.get("id")
            start_str = event.get("start")
            summary = event.get("summary", "Untitled Event")
            location = event.get("location", "")

            if not event_id or not start_str:
                continue

            try:
                start_dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
            except ValueError:
                continue

            minutes_until = (start_dt - now).total_seconds() / 60

            for window, label in [(15, "15 minutes"), (5, "5 minutes")]:
                key = f"{event_id}_{window}"
                if key in _alerted_meeting_ids:
                    continue
                if abs(minutes_until - window) <= 0.6:  # within 36 seconds of the window
                    safe_summary = _safe(summary)
                    safe_location = _safe(location) if location else ""
                    if window == 15:
                        text = (
                            f"⏰ Just a heads up — *{safe_summary}* is in 15 minutes."
                            + (f" ({safe_location})" if safe_location else "")
                        )
                    else:
                        text = (
                            f"🔔 Last call — *{safe_summary}* starts in 5 minutes!"
                            + (f" ({safe_location})" if safe_location else "")
                        )
                    for user_id in ALLOWED_IDS:
                        await context.bot.send_message(
                            chat_id=user_id,
                            text=text,
                            parse_mode=ParseMode.MARKDOWN,
                        )
                    _alerted_meeting_ids.add(key)
                    logger.info("Meeting reminder sent for '%s' (%s window)", summary, label)

    except Exception as e:
        logger.error("Meeting reminder check failed: %s", e)


# ─── Alert 4: PC Worker Offline ───────────────────────────────────────────────

async def check_worker_health(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Checks if the PC worker has been offline for > 30 minutes.
    Resets the alert once the worker reconnects.
    """
    global _worker_offline_alerted
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{API_URL}/worker/status/pc:local")

        data = resp.json()
        is_connected = data.get("connected", False)
        offline_minutes = data.get("offline_minutes", 0)

        if not is_connected and offline_minutes >= 30 and not _worker_offline_alerted:
            for user_id in ALLOWED_IDS:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=(
                        f"🔌 Hey, your laptop has been offline for about {offline_minutes:.0f} minutes. "
                        "I can't reach local tools until it's back."
                    ),
                    parse_mode=ParseMode.MARKDOWN,
                )
            _worker_offline_alerted = True
            logger.warning("PC worker offline alert sent (%d min).", offline_minutes)

        elif is_connected and _worker_offline_alerted:
            # Worker came back online — reset flag and notify
            _worker_offline_alerted = False
            for user_id in ALLOWED_IDS:
                await context.bot.send_message(
                    chat_id=user_id,
                    text="✅ Your laptop just came back online. I've got full access again.",
                    parse_mode=ParseMode.MARKDOWN,
                )
            logger.info("PC worker reconnected — alert reset.")

    except Exception as e:
        logger.error("Worker health check failed: %s", e)
