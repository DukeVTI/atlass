import logging
import os
import json
import httpx
import asyncpg
from typing import Any
from tools.base import Tool

logger = logging.getLogger("atlas.tools.whatsapp")

class WhatsAppReadTool(Tool):
    name = "whatsapp_read"
    description = "Queries the Atlas database to retrieve recent incoming WhatsApp messages. Use this to read the user's smart inbox."
    is_destructive = False

    schema = {
        "name": "whatsapp_read",
        "description": "Read recent incoming WhatsApp messages.",
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Number of recent messages to retrieve. Default is 10."
                },
                "sender_name": {
                    "type": "string",
                    "description": "Optional name of the sender to filter by (case-insensitive partial match)."
                }
            }
        }
    }

    async def run(self, limit: int = 10, sender_name: str = None, **kwargs) -> Any:
        try:
            dsn = os.environ["POSTGRES_DSN"].replace("+asyncpg", "")
            conn = await asyncpg.connect(dsn)
            
            if sender_name:
                query = """
                    SELECT sender_name, remote_jid, message_text, timestamp 
                    FROM whatsapp_messages 
                    WHERE sender_name ILIKE $1
                    ORDER BY timestamp DESC LIMIT $2
                """
                rows = await conn.fetch(query, f"%{sender_name}%", limit)
            else:
                query = """
                    SELECT sender_name, remote_jid, message_text, timestamp 
                    FROM whatsapp_messages 
                    ORDER BY timestamp DESC LIMIT $1
                """
                rows = await conn.fetch(query, limit)
            
            await conn.close()
            
            if not rows:
                return "No WhatsApp messages found matching the criteria."
                
            result = []
            for r in rows:
                result.append({
                    "sender": r["sender_name"],
                    "jid": r["remote_jid"],
                    "message": r["message_text"],
                    "timestamp": r["timestamp"].isoformat()
                })
            return result
        except Exception as e:
            logger.error(f"Failed to read WhatsApp messages: {e}")
            return f"Error reading database: {str(e)}"


class WhatsAppSendTool(Tool):
    name = "whatsapp_send"
    description = "Sends a WhatsApp text message to a contact on the user's behalf. Requires explicit user confirmation."
    is_destructive = True

    schema = {
        "name": "whatsapp_send",
        "description": "Send a text message via WhatsApp.",
        "input_schema": {
            "type": "object",
            "properties": {
                "remote_jid": {
                    "type": "string",
                    "description": "The WhatsApp number to send to, including country code (e.g. 2348000000000)."
                },
                "text": {
                    "type": "string",
                    "description": "The text content of the message to send."
                }
            },
            "required": ["remote_jid", "text"]
        }
    }

    async def run(self, remote_jid: str, text: str, **kwargs) -> Any:
        try:
            whatsapp_url = os.environ.get("WHATSAPP_URL", "http://whatsapp:3000")
            logger.info(f"Calling WhatsApp sidecar at {whatsapp_url}/send for JID {remote_jid}")
            
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    f"{whatsapp_url}/send",
                    json={"remote_jid": remote_jid, "text": text}
                )
                
                logger.info(f"WhatsApp sidecar response: {response.status_code} - {response.text}")

                if response.status_code == 200:
                    return f"Successfully sent WhatsApp message to {remote_jid}."
                elif response.status_code == 404:
                    return f"Failed: {remote_jid} is not a registered WhatsApp number."
                elif response.status_code == 503:
                    return "Failed: WhatsApp service is currently disconnected. Please ask Duke to check the logs and re-scan the QR code."
                else:
                    err = response.json().get("error", "Unknown error")
                    return f"Failed to send message: {err}"
        except Exception as e:
            logger.error(f"Failed to call WhatsApp send endpoint: {e}")
            return f"Error connecting to WhatsApp service: {str(e)}"

class WhatsAppContactSearchTool(Tool):
    name = "whatsapp_contact_search"
    description = "Searches the database for a contact's WhatsApp JID and phone numbers by fuzzy matching their name. Use this before sending a WhatsApp message to ensure you have the correct JID."
    is_destructive = False

    schema = {
        "name": "whatsapp_contact_search",
        "description": "Search for a WhatsApp contact by name.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The name or part of the name to search for (e.g. 'Dana' or 'Virusia')."
                }
            },
            "required": ["query"]
        }
    }

    async def run(self, query: str, **kwargs) -> Any:
        try:
            dsn = os.environ["POSTGRES_DSN"].replace("+asyncpg", "")
            conn = await asyncpg.connect(dsn)
            
            # Using ILIKE for fuzzy search
            sql = """
                SELECT name, whatsapp, phone, vip 
                FROM contacts 
                WHERE name ILIKE $1
                LIMIT 5
            """
            rows = await conn.fetch(sql, f"%{query}%")
            
            await conn.close()
            
            if not rows:
                return f"No contacts found matching '{query}'."
                
            result = []
            for r in rows:
                result.append({
                    "name": r["name"],
                    "jid": r["whatsapp"],
                    "phone": json.loads(r["phone"]) if r["phone"] else [],
                    "vip": r["vip"]
                })
            return result
        except Exception as e:
            logger.error(f"Failed to search WhatsApp contacts: {e}")
            return f"Error reading database: {str(e)}"
