import logging
from typing import Any
import base64
from email.message import EmailMessage
from bs4 import BeautifulSoup
from googleapiclient.discovery import build
from tools.base import Tool, SecurityRisk
from tools.google_auth import get_google_credentials

logger = logging.getLogger("atlas.tools.gmail")

def _clean_html(html_str: str) -> str:
    """Basic HTML-to-text sanitization to preserve LLM tokens."""
    soup = BeautifulSoup(html_str, "html.parser")
    # Simplify and extract text
    return soup.get_text(separator="\n", strip=True)

class GmailReadTool(Tool):
    name = "read_inbox"
    description = "Scans for unread messages in Gmail. Summarizes attachments simply to avoid massive payloads."
    is_destructive = False

    schema = {
        "name": "read_inbox",
        "description": "Fetch top unread emails.",
        "input_schema": {
            "type": "object",
            "properties": {
                "max_results": {"type": "integer", "description": "Number of emails to fetch."}
            }
        }
    }

    async def run(self, max_results: int = 5, **kwargs) -> Any:
        import asyncio
        def _sync_read():
            creds = get_google_credentials()
            if not creds:
                return "Google credentials not configured."

            service = build("gmail", "v1", credentials=creds)
            results = service.users().messages().list(userId="me", q="is:unread", maxResults=max_results).execute()
            messages = results.get("messages", [])

            if not messages:
                return "You have no unread emails."

            digest = ["Unread Emails:\n"]
            for msg in messages:
                msg_full = service.users().messages().get(userId="me", id=msg["id"], format="full").execute()
                payload = msg_full.get("payload", {})
                headers = payload.get("headers", [])
                
                subject = next((h["value"] for h in headers if h["name"] == "Subject"), "No Subject")
                sender = next((h["value"] for h in headers if h["name"] == "From"), "Unknown")
                
                # Fetch snippet
                snippet = msg_full.get("snippet", "")
                
                # Attachment safety check
                parts = payload.get("parts", [])
                attachments = []
                total_size = 0
                for p in parts:
                    if p.get("filename"):
                        attachments.append(p["filename"])
                        total_size += p.get("body", {}).get("size", 0)
                
                attach_info = ""
                if attachments:
                    if total_size > 25 * 1024 * 1024:
                        attach_info = f" | Attachments: {', '.join(attachments)} [SYSTEM WARNING: Attachments exceed 25MB limit. Do not download.]"
                    else:
                        attach_info = f" | Attachments: {', '.join(attachments)}"
                
                digest.append(f"From: {sender}\nSubject: {subject}{attach_info}\nSnippet: {snippet}\n")
                
            return "\n".join(digest)

        try:
            return await asyncio.to_thread(_sync_read)
        except Exception as e:
            logger.error(f"Gmail Read error: {e}")
            return "Error interacting with Gmail: " + str(e)


class GmailDraftTool(Tool):
    name = "draft_email"
    description = "Creates a draft in the user's Gmail without sending. Safe action."
    is_destructive = False

    schema = {
        "name": "draft_email",
        "description": "Draft an email.",
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {"type": "string"},
                "subject": {"type": "string"},
                "body": {"type": "string"}
            },
            "required": ["to", "subject", "body"]
        }
    }

    async def run(self, to: str, subject: str, body: str, **kwargs) -> Any:
        import asyncio
        def _sync_draft():
            creds = get_google_credentials()
            if not creds:
                return "Google credentials not configured."
                
            service = build("gmail", "v1", credentials=creds)
            
            message = EmailMessage()
            message.set_content(body)
            message["To"] = to
            message["From"] = "me"
            message["Subject"] = subject

            encoded_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
            create_message = {"message": {"raw": encoded_message}}
            
            draft = service.users().drafts().create(userId="me", body=create_message).execute()
            return f"Draft created successfully. Draft ID: {draft['id']}"

        try:
            return await asyncio.to_thread(_sync_draft)
        except Exception as e:
            logger.error(f"Gmail Draft error: {e}")
            return "Failed to create draft: " + str(e)



class GmailSendTool(Tool):
    name = "send_email"
    description = "Irreversibly sends an email."
    is_destructive = True # HIGH RISK: Triggers Confirmation Gate

    schema = {
        "name": "send_email",
        "description": "Sends an email to a recipient.",
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {"type": "string"},
                "subject": {"type": "string"},
                "body": {"type": "string"}
            },
            "required": ["to", "subject", "body"]
        }
    }

    async def run(self, to: str, subject: str, body: str, **kwargs) -> Any:
        # Note: The Orchestrator's ConfirmationManager will intercept this before it runs!
        # When `approve_action` evaluates, this will finally fire.
        import asyncio
        def _sync_send():
            creds = get_google_credentials()
            if not creds:
                return "Google credentials not configured."
                
            service = build("gmail", "v1", credentials=creds)
            
            message = EmailMessage()
            message.set_content(body)
            message["To"] = to
            message["From"] = "me"
            message["Subject"] = subject

            encoded_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
            send_message = {"raw": encoded_message}
            
            sent = service.users().messages().send(userId="me", body=send_message).execute()
            return f"Email definitely sent. Message ID: {sent['id']}"

        try:
            return await asyncio.to_thread(_sync_send)
        except Exception as e:
            logger.error(f"Gmail Send error: {e}")
            return "Failed to send email: " + str(e)
