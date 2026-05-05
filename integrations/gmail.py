"""
Gmail integration — direct API using google-api-python-client.
Shares credentials with Google Calendar (memory/google_credentials.json).
Run /auth/google/start in the Jarvis UI to authenticate.
"""

import base64
import email as email_lib
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_CREDENTIALS_PATH = Path(__file__).parent.parent / "memory" / "google_credentials.json"

_SETUP_MESSAGE = (
    "Gmail is not connected. Visit http://localhost:8000/auth/google/start to connect."
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
            _CREDENTIALS_PATH.write_text(creds.to_json())

        return creds if creds and creds.valid else None
    except Exception as e:
        logger.error("Failed to load Google credentials: %s", e)
        return None


class GmailIntegration:
    """Direct Gmail API integration."""

    async def execute(self, tool_name: str, params: dict) -> dict[str, Any]:
        creds = _get_credentials()
        if not creds:
            return {
                "success": False,
                "error": "Gmail not connected.",
                "setup_instructions": _SETUP_MESSAGE,
            }

        handlers = {
            "gmail_send_email": self._send_email,
            "gmail_read_inbox": self._read_inbox,
            "gmail_search_emails": self._search_emails,
            "gmail_create_draft": self._create_draft,
            "gmail_reply_email": self._reply_email,
        }

        handler = handlers.get(tool_name)
        if not handler:
            return {"success": False, "error": f"Unknown Gmail tool: {tool_name}"}

        try:
            return await handler(params)
        except Exception as e:
            logger.exception("Gmail tool %s failed: %s", tool_name, e)
            return {"success": False, "error": str(e)}

    def _build_service(self):
        from googleapiclient.discovery import build
        creds = _get_credentials()
        return build("gmail", "v1", credentials=creds)

    def _make_message(self, to: str, subject: str, body: str, cc: str = "",
                      reply_to_msg_id: str = None, thread_id: str = None) -> dict:
        """Build a Gmail API message dict."""
        msg = MIMEMultipart()
        msg["To"] = to
        msg["Subject"] = subject
        if cc:
            msg["Cc"] = cc
        if reply_to_msg_id:
            msg["In-Reply-To"] = reply_to_msg_id
            msg["References"] = reply_to_msg_id

        msg.attach(MIMEText(body, "plain"))
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        result: dict = {"raw": raw}
        if thread_id:
            result["threadId"] = thread_id
        return result

    def _parse_message(self, msg: dict) -> dict:
        """Parse Gmail API message into a clean dict."""
        headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
        snippet = msg.get("snippet", "")

        # Try to get full body
        body = ""
        payload = msg.get("payload", {})
        if payload.get("mimeType") == "text/plain":
            data = payload.get("body", {}).get("data", "")
            if data:
                body = base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
        else:
            for part in payload.get("parts", []):
                if part.get("mimeType") == "text/plain":
                    data = part.get("body", {}).get("data", "")
                    if data:
                        body = base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
                        break

        return {
            "id": msg["id"],
            "thread_id": msg.get("threadId"),
            "from": headers.get("From", ""),
            "to": headers.get("To", ""),
            "subject": headers.get("Subject", "(No subject)"),
            "date": headers.get("Date", ""),
            "snippet": snippet,
            "body": body[:2000] if body else snippet,
            "labels": msg.get("labelIds", []),
        }

    async def _send_email(self, params: dict) -> dict:
        if not params.get("confirmed"):
            return {
                "success": False,
                "requires_confirmation": True,
                "preview": {
                    "to": params.get("to"),
                    "subject": params.get("subject"),
                    "body": params.get("body"),
                },
                "message": "Please confirm before sending this email.",
            }

        import asyncio
        service = self._build_service()
        msg = self._make_message(
            to=params["to"],
            subject=params["subject"],
            body=params["body"],
            cc=params.get("cc", ""),
        )
        loop = asyncio.get_event_loop()
        sent = await loop.run_in_executor(
            None,
            lambda: service.users().messages().send(userId="me", body=msg).execute()
        )
        return {
            "success": True,
            "message_id": sent["id"],
            "message": f"Email sent to {params['to']}.",
        }

    async def _read_inbox(self, params: dict) -> dict:
        import asyncio
        service = self._build_service()
        max_results = params.get("max_results", 10)
        unread_only = params.get("unread_only", False)
        query = "is:unread" if unread_only else ""

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: service.users().messages().list(
                userId="me",
                labelIds=["INBOX"],
                q=query,
                maxResults=max_results,
            ).execute()
        )

        messages = result.get("messages", [])
        emails = []
        for m in messages:
            full = await loop.run_in_executor(
                None,
                lambda mid=m["id"]: service.users().messages().get(
                    userId="me", id=mid, format="full"
                ).execute()
            )
            emails.append(self._parse_message(full))

        return {
            "success": True,
            "count": len(emails),
            "emails": emails,
        }

    async def _search_emails(self, params: dict) -> dict:
        import asyncio
        service = self._build_service()

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: service.users().messages().list(
                userId="me",
                q=params["query"],
                maxResults=params.get("max_results", 10),
            ).execute()
        )

        messages = result.get("messages", [])
        emails = []
        for m in messages:
            full = await loop.run_in_executor(
                None,
                lambda mid=m["id"]: service.users().messages().get(
                    userId="me", id=mid, format="full"
                ).execute()
            )
            emails.append(self._parse_message(full))

        return {
            "success": True,
            "count": len(emails),
            "query": params["query"],
            "emails": emails,
        }

    async def _create_draft(self, params: dict) -> dict:
        import asyncio
        service = self._build_service()
        msg = self._make_message(
            to=params["to"],
            subject=params["subject"],
            body=params["body"],
            cc=params.get("cc", ""),
        )
        loop = asyncio.get_event_loop()
        draft = await loop.run_in_executor(
            None,
            lambda: service.users().drafts().create(
                userId="me", body={"message": msg}
            ).execute()
        )
        return {
            "success": True,
            "draft_id": draft["id"],
            "message": f"Draft saved for {params['to']}.",
        }

    async def _reply_email(self, params: dict) -> dict:
        if not params.get("confirmed"):
            return {
                "success": False,
                "requires_confirmation": True,
                "message": "Please confirm before sending this reply.",
            }

        import asyncio
        service = self._build_service()
        loop = asyncio.get_event_loop()

        # Get original thread to find the message ID and recipient
        thread = await loop.run_in_executor(
            None,
            lambda: service.users().threads().get(
                userId="me", id=params["thread_id"], format="metadata"
            ).execute()
        )

        messages = thread.get("messages", [])
        if not messages:
            return {"success": False, "error": "Thread not found."}

        orig = messages[-1]
        headers = {h["name"]: h["value"] for h in orig.get("payload", {}).get("headers", [])}
        reply_to = headers.get("From", "")
        subject = headers.get("Subject", "")
        if not subject.startswith("Re:"):
            subject = f"Re: {subject}"
        msg_id = headers.get("Message-ID", "")

        msg = self._make_message(
            to=reply_to,
            subject=subject,
            body=params["body"],
            reply_to_msg_id=msg_id,
            thread_id=params["thread_id"],
        )

        sent = await loop.run_in_executor(
            None,
            lambda: service.users().messages().send(userId="me", body=msg).execute()
        )
        return {
            "success": True,
            "message_id": sent["id"],
            "message": f"Reply sent to {reply_to}.",
        }


gmail = GmailIntegration()
