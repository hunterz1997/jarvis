"""
Google Drive integration — direct API using google-api-python-client.
Shares OAuth credentials with Calendar/Gmail (memory/google_credentials.json).
Run /auth/google/start in Jarvis to re-authorize with Drive scope.
"""

import asyncio
import base64
import io
import logging
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email import encoders
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_CREDENTIALS_PATH = Path(__file__).parent.parent / "memory" / "google_credentials.json"

_SETUP_MESSAGE = (
    "Google Drive is not connected. Visit http://localhost:8000/auth/google/start "
    "to authorize (you may need to re-authorize to grant Drive access)."
)

# Google Workspace MIME → export format
_EXPORT_MAP = {
    "application/vnd.google-apps.document":     ("text/plain", ".txt"),
    "application/vnd.google-apps.spreadsheet":  ("text/csv",   ".csv"),
    "application/vnd.google-apps.presentation": ("text/plain", ".txt"),
}


def _get_credentials():
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
        logger.error("Google Drive credentials error: %s", e)
        return None


def _build_drive():
    from googleapiclient.discovery import build
    creds = _get_credentials()
    return build("drive", "v3", credentials=creds) if creds else None


def _build_gmail():
    from googleapiclient.discovery import build
    creds = _get_credentials()
    return build("gmail", "v1", credentials=creds) if creds else None


def _file_summary(f: dict) -> dict:
    mime = f.get("mimeType", "")
    kind = mime.replace("application/vnd.google-apps.", "Google ").replace(
        "application/vnd.openxmlformats-officedocument.", "").split(".")[-1] or mime.split("/")[-1]
    return {
        "id":       f["id"],
        "name":     f["name"],
        "type":     kind,
        "modified": f.get("modifiedTime", "")[:10],
        "size_kb":  round(int(f.get("size", 0)) / 1024, 1) if f.get("size") else None,
        "web_link": f.get("webViewLink", ""),
    }


class GoogleDriveIntegration:

    async def execute(self, tool_name: str, params: dict) -> dict[str, Any]:
        creds = _get_credentials()
        if not creds:
            return {"success": False, "error": "Google Drive not connected.", "setup": _SETUP_MESSAGE}

        handlers = {
            "drive_search":     self._search,
            "drive_list":       self._list,
            "drive_read":       self._read,
            "drive_email_file": self._email_file,
        }
        handler = handlers.get(tool_name)
        if not handler:
            return {"success": False, "error": f"Unknown Drive tool: {tool_name}"}
        try:
            return await handler(params)
        except Exception as e:
            logger.exception("Drive tool %s failed", tool_name)
            return {"success": False, "error": str(e)}

    # ── Search ─────────────────────────────────────────────────────────────────

    async def _search(self, params: dict) -> dict:
        query   = params["query"]
        limit   = min(params.get("limit", 10), 20)
        ftype   = params.get("file_type", "")
        loop    = asyncio.get_event_loop()
        svc     = _build_drive()

        q_parts = [f"(name contains '{query}' or fullText contains '{query}')", "trashed = false"]
        _mime_map = {
            "pdf":   "application/pdf",
            "doc":   "application/vnd.google-apps.document",
            "sheet": "application/vnd.google-apps.spreadsheet",
            "slide": "application/vnd.google-apps.presentation",
            "excel": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "word":  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        }
        if ftype and ftype.lower() in _mime_map:
            q_parts.append(f"mimeType = '{_mime_map[ftype.lower()]}'")

        result = await loop.run_in_executor(None, lambda: svc.files().list(
            q=" and ".join(q_parts),
            pageSize=limit,
            orderBy="modifiedTime desc",
            fields="files(id,name,mimeType,modifiedTime,size,webViewLink)",
        ).execute())

        files = result.get("files", [])
        return {"success": True, "query": query, "count": len(files),
                "files": [_file_summary(f) for f in files]}

    # ── List ───────────────────────────────────────────────────────────────────

    async def _list(self, params: dict) -> dict:
        folder = params.get("folder", "root")
        limit  = min(params.get("limit", 20), 50)
        loop   = asyncio.get_event_loop()
        svc    = _build_drive()

        folder_id = folder
        if folder.lower() != "root" and not (len(folder) > 20 and " " not in folder):
            res = await loop.run_in_executor(None, lambda: svc.files().list(
                q=f"name = '{folder}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false",
                fields="files(id,name)", pageSize=3,
            ).execute())
            found = res.get("files", [])
            folder_id = found[0]["id"] if found else "root"

        result = await loop.run_in_executor(None, lambda: svc.files().list(
            q=f"'{folder_id}' in parents and trashed = false",
            pageSize=limit,
            orderBy="modifiedTime desc",
            fields="files(id,name,mimeType,modifiedTime,size,webViewLink)",
        ).execute())

        files = result.get("files", [])
        return {"success": True, "folder": folder, "count": len(files),
                "files": [_file_summary(f) for f in files]}

    # ── Read ───────────────────────────────────────────────────────────────────

    async def _read(self, params: dict) -> dict:
        ref  = params.get("file_id") or params.get("file_name") or params.get("query", "")
        loop = asyncio.get_event_loop()
        svc  = _build_drive()

        file_id, file_name, mime_type = await self._resolve_file(svc, ref, loop)
        if not file_id:
            return {"success": False, "error": f"File not found: '{ref}'"}

        if mime_type in _EXPORT_MAP:
            export_mime, _ = _EXPORT_MAP[mime_type]
            data = await loop.run_in_executor(None, lambda: svc.files().export(
                fileId=file_id, mimeType=export_mime).execute())
            content = data.decode("utf-8", errors="replace") if isinstance(data, bytes) else str(data)
        elif mime_type and mime_type.startswith("text/"):
            data = await loop.run_in_executor(None, lambda: svc.files().get_media(fileId=file_id).execute())
            content = data.decode("utf-8", errors="replace") if isinstance(data, bytes) else str(data)
        else:
            return {"success": True, "file_id": file_id, "file_name": file_name,
                    "note": "Binary file — cannot read as text. Use drive_email_file to send it."}

        return {"success": True, "file_id": file_id, "file_name": file_name,
                "content": content[:8000] + ("…[truncated]" if len(content) > 8000 else "")}

    # ── Email file ─────────────────────────────────────────────────────────────

    async def _email_file(self, params: dict) -> dict:
        if not params.get("confirmed"):
            return {
                "success": False, "requires_confirmation": True,
                "preview": {"to": params.get("to"), "subject": params.get("subject"),
                            "file": params.get("file_id") or params.get("file_name")},
                "message": "Ready to email this file — please confirm to send.",
            }

        ref  = params.get("file_id") or params.get("file_name") or ""
        loop = asyncio.get_event_loop()
        svc  = _build_drive()

        file_id, file_name, mime_type = await self._resolve_file(svc, ref, loop)
        if not file_id:
            return {"success": False, "error": f"File not found: '{ref}'"}

        file_bytes, dl_mime = await self._download_bytes(svc, file_id, mime_type, loop)
        if file_bytes is None:
            return {"success": False, "error": "Could not download file."}

        gmail_svc = _build_gmail()
        raw_msg   = _build_mime_attachment(
            to=params["to"],
            subject=params.get("subject", f"Jarvis — {file_name}"),
            body=params.get("body", f"Please find attached: {file_name}"),
            filename=file_name,
            file_bytes=file_bytes,
        )
        sent = await loop.run_in_executor(
            None, lambda: gmail_svc.users().messages().send(userId="me", body=raw_msg).execute()
        )
        return {"success": True, "message": f"'{file_name}' emailed to {params['to']}.",
                "email_id": sent["id"]}

    # ── Helpers ────────────────────────────────────────────────────────────────

    async def _resolve_file(self, svc, ref: str, loop) -> tuple:
        if len(ref) > 25 and " " not in ref:
            try:
                meta = await loop.run_in_executor(None, lambda: svc.files().get(
                    fileId=ref, fields="id,name,mimeType").execute())
                return meta["id"], meta["name"], meta.get("mimeType", "")
            except Exception:
                pass
        result = await loop.run_in_executor(None, lambda: svc.files().list(
            q=f"name contains '{ref}' and trashed = false",
            pageSize=5, orderBy="modifiedTime desc",
            fields="files(id,name,mimeType)",
        ).execute())
        files = result.get("files", [])
        if not files:
            return None, None, None
        f = files[0]
        return f["id"], f["name"], f.get("mimeType", "")

    async def _download_bytes(self, svc, file_id: str, mime_type: str, loop) -> tuple:
        try:
            if mime_type in _EXPORT_MAP:
                export_mime, _ = _EXPORT_MAP[mime_type]
                data = await loop.run_in_executor(None, lambda: svc.files().export(
                    fileId=file_id, mimeType=export_mime).execute())
                return (data if isinstance(data, bytes) else data.encode()), export_mime
            else:
                from googleapiclient.http import MediaIoBaseDownload
                buf  = io.BytesIO()
                req  = svc.files().get_media(fileId=file_id)
                dl   = MediaIoBaseDownload(buf, req)
                done = False
                while not done:
                    _, done = await loop.run_in_executor(None, dl.next_chunk)
                return buf.getvalue(), mime_type or "application/octet-stream"
        except Exception as e:
            logger.error("Drive download error: %s", e)
            return None, None


def _build_mime_attachment(to: str, subject: str, body: str,
                           filename: str, file_bytes: bytes) -> dict:
    """Build a Gmail API message dict with a file attachment."""
    msg = MIMEMultipart()
    msg["To"]      = to
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))
    part = MIMEBase("application", "octet-stream")
    part.set_payload(file_bytes)
    encoders.encode_base64(part)
    part.add_header("Content-Disposition", f'attachment; filename="{filename}"')
    msg.attach(part)
    return {"raw": base64.urlsafe_b64encode(msg.as_bytes()).decode()}


drive = GoogleDriveIntegration()
