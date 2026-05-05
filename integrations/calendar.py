"""
Google Calendar integration — direct API using google-api-python-client.
Credentials stored in memory/google_credentials.json (OAuth2).
Run /auth/google/start in the Jarvis UI to authenticate.
"""

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_CREDENTIALS_PATH = Path(__file__).parent.parent / "memory" / "google_credentials.json"
_CLIENT_SECRET_PATH = Path(__file__).parent.parent / "memory" / "google_client_secret.json"

_SETUP_MESSAGE = (
    "Google Calendar is not connected. To set it up:\n"
    "1. Go to http://localhost:8000/auth/google/start in your browser\n"
    "2. Sign in with Google and grant Calendar + Gmail permissions\n"
    "3. You'll be redirected back automatically\n\n"
    "First time? You need Google OAuth credentials:\n"
    "1. Visit https://console.cloud.google.com/\n"
    "2. Create a project → Enable 'Google Calendar API' and 'Gmail API'\n"
    "3. Create OAuth 2.0 credentials (Desktop app type)\n"
    "4. Download as JSON and save to: C:\\Claude\\Jarvis\\memory\\google_client_secret.json"
)


def _get_credentials():
    """Load and refresh OAuth2 credentials. Returns None if not set up."""
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request

        if not _CREDENTIALS_PATH.exists():
            return None

        creds = Credentials.from_authorized_user_file(str(_CREDENTIALS_PATH))

        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            # Save refreshed credentials
            _CREDENTIALS_PATH.write_text(creds.to_json())

        return creds if creds and creds.valid else None
    except Exception as e:
        logger.error("Failed to load Google credentials: %s", e)
        return None


def _build_service(service_name: str, version: str):
    """Build a Google API service client."""
    from googleapiclient.discovery import build
    creds = _get_credentials()
    if not creds:
        return None
    return build(service_name, version, credentials=creds)


class CalendarIntegration:
    """Direct Google Calendar API integration."""

    async def execute(self, tool_name: str, params: dict) -> dict[str, Any]:
        """Route tool calls to the appropriate handler."""
        creds = _get_credentials()
        if not creds:
            return {
                "success": False,
                "error": "Google Calendar not connected.",
                "setup_instructions": _SETUP_MESSAGE,
            }

        handlers = {
            "calendar_create_event": self._create_event,
            "calendar_list_events": self._list_events,
            "calendar_get_event": self._get_event,
            "calendar_update_event": self._update_event,
            "calendar_delete_event": self._delete_event,
        }

        handler = handlers.get(tool_name)
        if not handler:
            return {"success": False, "error": f"Unknown calendar tool: {tool_name}"}

        try:
            return await handler(params)
        except Exception as e:
            logger.exception("Calendar tool %s failed: %s", tool_name, e)
            return {"success": False, "error": str(e)}

    async def _create_event(self, params: dict) -> dict:
        """Create a calendar event, optionally with a Google Meet link."""
        import asyncio
        from googleapiclient.discovery import build

        creds = _get_credentials()
        service = build("calendar", "v3", credentials=creds)

        title = params["title"]
        start_time = params["start_time"]
        end_time = params["end_time"]
        description = params.get("description", "")
        attendees = params.get("attendees", [])
        add_meet_link = params.get("add_meet_link", False)
        location = params.get("location", "")

        # Parse times — assume local if no timezone
        def _to_rfc3339(dt_str: str) -> str:
            try:
                dt = datetime.fromisoformat(dt_str)
                if dt.tzinfo is None:
                    # Assume IST (UTC+5:30)
                    from datetime import timezone as tz
                    ist = timezone(timedelta(hours=5, minutes=30))
                    dt = dt.replace(tzinfo=ist)
                return dt.isoformat()
            except Exception:
                return dt_str

        event_body: dict = {
            "summary": title,
            "description": description,
            "location": location,
            "start": {"dateTime": _to_rfc3339(start_time), "timeZone": "Asia/Kolkata"},
            "end": {"dateTime": _to_rfc3339(end_time), "timeZone": "Asia/Kolkata"},
        }

        if attendees:
            event_body["attendees"] = [{"email": e} for e in attendees]

        # Google Meet link
        conference_data_version = 0
        if add_meet_link:
            event_body["conferenceData"] = {
                "createRequest": {
                    "requestId": str(uuid.uuid4()),
                    "conferenceSolutionKey": {"type": "hangoutsMeet"},
                }
            }
            conference_data_version = 1

        loop = asyncio.get_event_loop()
        event = await loop.run_in_executor(
            None,
            lambda: service.events().insert(
                calendarId="primary",
                body=event_body,
                conferenceDataVersion=conference_data_version,
                sendUpdates="all" if attendees else "none",
            ).execute()
        )

        meet_link = None
        if add_meet_link:
            conf = event.get("conferenceData", {})
            for ep in conf.get("entryPoints", []):
                if ep.get("entryPointType") == "video":
                    meet_link = ep.get("uri")
                    break

        result = {
            "success": True,
            "event_id": event["id"],
            "title": event.get("summary"),
            "start": event["start"].get("dateTime"),
            "end": event["end"].get("dateTime"),
            "html_link": event.get("htmlLink"),
            "attendees": [a["email"] for a in event.get("attendees", [])],
        }
        if meet_link:
            result["meet_link"] = meet_link
            result["message"] = f"Event created! Google Meet link: {meet_link}"
        else:
            result["message"] = f"Event '{title}' created successfully."

        return result

    async def _list_events(self, params: dict) -> dict:
        """List upcoming calendar events."""
        import asyncio
        from googleapiclient.discovery import build

        creds = _get_credentials()
        service = build("calendar", "v3", credentials=creds)

        now = datetime.now(timezone.utc).isoformat()
        time_min = params.get("time_min", now)
        time_max = params.get(
            "time_max",
            (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
        )
        max_results = params.get("max_results", 10)

        loop = asyncio.get_event_loop()
        events_result = await loop.run_in_executor(
            None,
            lambda: service.events().list(
                calendarId="primary",
                timeMin=time_min,
                timeMax=time_max,
                maxResults=max_results,
                singleEvents=True,
                orderBy="startTime",
            ).execute()
        )

        events = events_result.get("items", [])
        formatted = []
        for e in events:
            start = e["start"].get("dateTime", e["start"].get("date", ""))
            formatted.append({
                "id": e["id"],
                "title": e.get("summary", "(No title)"),
                "start": start,
                "end": e["end"].get("dateTime", e["end"].get("date", "")),
                "location": e.get("location", ""),
                "attendees": [a["email"] for a in e.get("attendees", [])],
                "meet_link": next(
                    (ep["uri"] for ep in
                     e.get("conferenceData", {}).get("entryPoints", [])
                     if ep.get("entryPointType") == "video"),
                    None,
                ),
            })

        return {
            "success": True,
            "count": len(formatted),
            "events": formatted,
        }

    async def _get_event(self, params: dict) -> dict:
        """Get a specific event by ID."""
        import asyncio
        from googleapiclient.discovery import build

        creds = _get_credentials()
        service = build("calendar", "v3", credentials=creds)

        loop = asyncio.get_event_loop()
        event = await loop.run_in_executor(
            None,
            lambda: service.events().get(
                calendarId="primary",
                eventId=params["event_id"],
            ).execute()
        )

        return {
            "success": True,
            "event_id": event["id"],
            "title": event.get("summary"),
            "start": event["start"].get("dateTime"),
            "end": event["end"].get("dateTime"),
            "description": event.get("description", ""),
            "location": event.get("location", ""),
            "attendees": [a["email"] for a in event.get("attendees", [])],
            "html_link": event.get("htmlLink"),
        }

    async def _update_event(self, params: dict) -> dict:
        """Update an existing event."""
        import asyncio
        from googleapiclient.discovery import build

        creds = _get_credentials()
        service = build("calendar", "v3", credentials=creds)

        loop = asyncio.get_event_loop()
        # Get existing event first
        event = await loop.run_in_executor(
            None,
            lambda: service.events().get(
                calendarId="primary",
                eventId=params["event_id"],
            ).execute()
        )

        if "title" in params:
            event["summary"] = params["title"]
        if "description" in params:
            event["description"] = params["description"]
        if "start_time" in params:
            event["start"] = {"dateTime": params["start_time"], "timeZone": "Asia/Kolkata"}
        if "end_time" in params:
            event["end"] = {"dateTime": params["end_time"], "timeZone": "Asia/Kolkata"}
        if "attendees" in params:
            event["attendees"] = [{"email": e} for e in params["attendees"]]

        updated = await loop.run_in_executor(
            None,
            lambda: service.events().update(
                calendarId="primary",
                eventId=params["event_id"],
                body=event,
                sendUpdates="all",
            ).execute()
        )

        return {
            "success": True,
            "event_id": updated["id"],
            "message": f"Event '{updated.get('summary')}' updated successfully.",
        }

    async def _delete_event(self, params: dict) -> dict:
        """Delete a calendar event."""
        if not params.get("confirmed"):
            return {
                "success": False,
                "requires_confirmation": True,
                "message": "Please confirm before deleting the calendar event.",
            }

        import asyncio
        from googleapiclient.discovery import build

        creds = _get_credentials()
        service = build("calendar", "v3", credentials=creds)

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            lambda: service.events().delete(
                calendarId="primary",
                eventId=params["event_id"],
            ).execute()
        )

        return {
            "success": True,
            "message": "Calendar event deleted successfully.",
        }


calendar = CalendarIntegration()
