import logging
import datetime
from typing import Any
from googleapiclient.discovery import build
from tools.base import Tool
from tools.google_auth import get_google_credentials

logger = logging.getLogger("atlas.tools.calendar")

class CalendarReadTool(Tool):
    name = "get_upcoming_events"
    description = "Retrieves upcoming events from the user's primary Google Calendar."
    is_destructive = False

    schema = {
        "name": "get_upcoming_events",
        "description": "Fetch upcoming events from the agenda.",
        "input_schema": {
            "type": "object",
            "properties": {
                "max_results": {
                    "type": "integer",
                    "description": "Number of upcoming events to retrieve. Default is 10."
                }
            }
        }
    }

    async def run(self, max_results: int = 10, **kwargs) -> Any:
        import asyncio
        def _sync_read():
            creds = get_google_credentials()
            if not creds:
                return "Google credentials not configured or valid. Ask Duke to provide token.json."

            service = build("calendar", "v3", credentials=creds)
            # Use UTC internally
            now = datetime.datetime.utcnow().isoformat() + "Z"
            
            events_result = service.events().list(
                calendarId="primary", 
                timeMin=now,
                maxResults=max_results, 
                singleEvents=True,
                orderBy="startTime"
            ).execute()
            
            events = events_result.get("items", [])
            
            if not events:
                return "You have no upcoming events."
                
            report = ["Upcoming Calendar Events:\n"]
            for event in events:
                start = event["start"].get("dateTime", event["start"].get("date"))
                report.append(f"- {start}: {event.get('summary', 'Untitled Event')}")
                
            return "\n".join(report)

        try:
            return await asyncio.to_thread(_sync_read)
        except Exception as e:
            logger.error(f"Calendar API error: {e}")
            return "Error: Calendar API unreachable or failed. " + str(e)



class CalendarCreateTool(Tool):
    name = "create_event"
    description = "Creates a new event on Google Calendar. Automatically checks for double booking."
    is_destructive = False # Low risk, but can optionally be Medium if desired. Kept low to align with PRD.

    schema = {
        "name": "create_event",
        "description": "Create an event on the user's calendar.",
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "The title or summary of the event."
                },
                "start_time_iso": {
                    "type": "string",
                    "description": "ISO format start time (e.g. 2026-04-20T10:00:00Z)."
                },
                "end_time_iso": {
                    "type": "string",
                    "description": "ISO format end time (e.g. 2026-04-20T11:00:00Z)."
                },
                "attendees": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of attendee email addresses."
                }
            },
            "required": ["summary", "start_time_iso", "end_time_iso"]
        }
    }

    async def run(self, summary: str, start_time_iso: str, end_time_iso: str, attendees: list = None, **kwargs) -> Any:
        import asyncio
        def _sync_create():
            creds = get_google_credentials()
            if not creds:
                return "Google credentials not configured. Ask Duke to provide token.json."

            service = build("calendar", "v3", credentials=creds)
            
            # Sub-Tool call: PRD Edge Case (Double Booking check)
            events_result = service.events().list(
                calendarId="primary", 
                timeMin=start_time_iso,
                timeMax=end_time_iso,
                singleEvents=True
            ).execute()
            
            overlaps = events_result.get("items", [])
            if overlaps:
                overlap_summaries = ", ".join(e.get('summary', 'Busy') for e in overlaps)
                return f"SYSTEM GATE / CONFLICT: The user is already double booked during this time with: {overlap_summaries}. Alert the user."

            import uuid
            # If no overlaps, create event
            event_body = {
                "summary": summary,
                "start": {"dateTime": start_time_iso},
                "end": {"dateTime": end_time_iso},
                "conferenceData": {
                    "createRequest": {
                        "requestId": uuid.uuid4().hex,
                        "conferenceSolutionKey": {"type": "hangoutsMeet"}
                    }
                }
            }
            if attendees:
                event_body["attendees"] = [{"email": email} for email in attendees]

            event = service.events().insert(
                calendarId="primary", 
                body=event_body,
                conferenceDataVersion=1
            ).execute()
            
            meet_link = ""
            if event.get("conferenceData") and event["conferenceData"].get("entryPoints"):
                for ep in event["conferenceData"]["entryPoints"]:
                    if ep.get("entryPointType") == "video":
                        meet_link = f" (Meet Link: {ep.get('uri')})"
                        break

            return f"Event created successfully: {event.get('htmlLink')}{meet_link}"

        try:
            return await asyncio.to_thread(_sync_create)
        except Exception as e:
            logger.error(f"Calendar create routing error: {e}")
            return "Error: Calendar API unreachable or failed. " + str(e)
