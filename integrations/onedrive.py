"""
OneDrive integration — Microsoft Graph API (cloud).
Works from any machine (local laptop OR cloud VPS) because it talks directly
to Microsoft's cloud API. Files are always accessible as long as they're in
OneDrive, regardless of whether the user's laptop is on or off.

One-time setup: http://localhost:8000/auth/microsoft/setup
"""

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_CREDS_PATH       = Path(__file__).parent.parent / "memory" / "microsoft_credentials.json"
_GRAPH_BASE       = "https://graph.microsoft.com/v1.0"
# consumers endpoint for personal MSA accounts (no Azure AD tenant required)
_TOKEN_URL        = "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"
# Same public client ID used by main.py device code flow (no client_secret)
_PUBLIC_CLIENT_ID = "14d82eec-204b-4c2f-b7e8-296a70dab67e"

_SETUP_MSG = (
    "OneDrive not connected. Visit http://localhost:8000/auth/microsoft/device "
    "to authorize (2 minutes, no Azure portal needed — just enter a short code)."
)

# Extensions we can read as plain text
_TEXT_EXTENSIONS = {
    ".txt", ".csv", ".json", ".xml", ".md", ".log", ".py",
    ".js", ".html", ".htm", ".css", ".yaml", ".yml", ".ini", ".cfg", ".toml",
}


# ── Token management ───────────────────────────────────────────────────────────

async def _get_token() -> str | None:
    """Return a valid access token, auto-refreshing if expired."""
    if not _CREDS_PATH.exists():
        return None
    try:
        creds = json.loads(_CREDS_PATH.read_text())
    except Exception:
        return None

    # Token still valid
    if creds.get("expires_at", 0) > time.time() + 60:
        return creds.get("access_token")

    # Refresh it — public client flow, no client_secret needed
    refresh_token = creds.get("refresh_token")
    if not refresh_token:
        return None

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(_TOKEN_URL, data={
                "grant_type":    "refresh_token",
                "client_id":     _PUBLIC_CLIENT_ID,
                "refresh_token": refresh_token,
                "scope":         "https://graph.microsoft.com/Files.ReadWrite.All offline_access",
            })
        if resp.status_code == 200:
            new_creds = resp.json()
            new_creds["expires_at"] = time.time() + new_creds.get("expires_in", 3600)
            _CREDS_PATH.write_text(json.dumps(new_creds, indent=2))
            logger.debug("OneDrive token refreshed")
            return new_creds.get("access_token")
        else:
            logger.error("OneDrive token refresh failed: %s %s", resp.status_code, resp.text[:200])
    except Exception as e:
        logger.error("OneDrive token refresh error: %s", e)
    return None


# ── Item summary helper ────────────────────────────────────────────────────────

def _item_info(item: dict) -> dict:
    """Convert a Graph API driveItem into a compact summary dict."""
    parent_path = item.get("parentReference", {}).get("path", "")
    folder = parent_path.split("root:")[-1].strip("/") if "root:" in parent_path else ""
    name   = item["name"]
    ext    = Path(name).suffix.upper().lstrip(".") or "file"
    return {
        "id":       item["id"],
        "name":     name,
        "type":     ext,
        "size_kb":  round(item.get("size", 0) / 1024, 1),
        "modified": (item.get("lastModifiedDateTime") or "")[:16].replace("T", " "),
        "folder":   folder or "/",
        "web_url":  item.get("webUrl", ""),
    }


# ── Integration class ──────────────────────────────────────────────────────────

class OneDriveIntegration:

    async def execute(self, tool_name: str, params: dict) -> dict[str, Any]:
        token = await _get_token()
        if not token:
            return {"success": False, "error": _SETUP_MSG}

        handlers = {
            "onedrive_search":     self._search,
            "onedrive_list":       self._list,
            "onedrive_read":       self._read,
            "onedrive_email_file": self._email_file,
        }
        handler = handlers.get(tool_name)
        if not handler:
            return {"success": False, "error": f"Unknown OneDrive tool: {tool_name}"}
        try:
            return await handler(params, token)
        except Exception as e:
            logger.exception("OneDrive tool %s failed", tool_name)
            return {"success": False, "error": str(e)}

    # ── Search ─────────────────────────────────────────────────────────────────

    async def _search(self, params: dict, token: str) -> dict:
        query = params["query"]
        limit = min(params.get("limit", 10), 25)
        ftype = params.get("file_type", "").lower().lstrip(".")

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{_GRAPH_BASE}/me/drive/root/search(q='{query}')",
                headers={"Authorization": f"Bearer {token}"},
                params={
                    "$top":    limit * 2,  # fetch extra so we can filter
                    "$select": "id,name,size,lastModifiedDateTime,parentReference,webUrl,folder",
                },
            )

        if resp.status_code != 200:
            return {"success": False, "error": f"Graph API error {resp.status_code}: {resp.text[:200]}"}

        items = [i for i in resp.json().get("value", []) if "folder" not in i]
        if ftype:
            items = [i for i in items if Path(i["name"]).suffix.lower().lstrip(".") == ftype]

        return {
            "success": True,
            "query":   query,
            "count":   len(items[:limit]),
            "files":   [_item_info(i) for i in items[:limit]],
        }

    # ── List ───────────────────────────────────────────────────────────────────

    async def _list(self, params: dict, token: str) -> dict:
        folder = params.get("folder", "").strip("/")
        limit  = min(params.get("limit", 20), 50)

        url = (
            f"{_GRAPH_BASE}/me/drive/root:/{folder}:/children"
            if folder else
            f"{_GRAPH_BASE}/me/drive/root/children"
        )

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                url,
                headers={"Authorization": f"Bearer {token}"},
                params={
                    "$top":      limit,
                    "$orderby":  "lastModifiedDateTime desc",
                    "$select":   "id,name,size,lastModifiedDateTime,parentReference,webUrl,folder",
                },
            )

        if resp.status_code != 200:
            return {"success": False, "error": f"Graph API error {resp.status_code}: {resp.text[:200]}"}

        items = resp.json().get("value", [])
        result = []
        for item in items:
            info = _item_info(item)
            info["is_folder"] = "folder" in item
            result.append(info)

        return {
            "success": True,
            "folder":  folder or "/",
            "count":   len(result),
            "files":   result,
        }

    # ── Read ───────────────────────────────────────────────────────────────────

    async def _read(self, params: dict, token: str) -> dict:
        ref = params.get("file_path") or params.get("file_name") or params.get("file_id") or ""

        item = await self._resolve_item(ref, token)
        if not item:
            return {"success": False, "error": f"File not found in OneDrive: '{ref}'"}

        name    = item["name"]
        item_id = item["id"]

        if Path(name).suffix.lower() not in _TEXT_EXTENSIONS:
            return {
                "success":   True,
                "file_name": name,
                "id":        item_id,
                "size_kb":   round(item.get("size", 0) / 1024, 1),
                "note": (
                    f"'{name}' is a {Path(name).suffix.upper()} file — "
                    "cannot read as text. Use onedrive_email_file to send it as an attachment."
                ),
            }

        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.get(
                f"{_GRAPH_BASE}/me/drive/items/{item_id}/content",
                headers={"Authorization": f"Bearer {token}"},
            )

        if resp.status_code != 200:
            return {"success": False, "error": f"Download error {resp.status_code}"}

        content = resp.text
        return {
            "success":   True,
            "file_name": name,
            "id":        item_id,
            "content":   content[:8000] + ("…[truncated]" if len(content) > 8000 else ""),
        }

    # ── Email file ─────────────────────────────────────────────────────────────

    async def _email_file(self, params: dict, token: str) -> dict:
        ref = params.get("file_path") or params.get("file_name") or params.get("file_id") or ""

        if not params.get("confirmed"):
            return {
                "success":              False,
                "requires_confirmation": True,
                "preview": {
                    "to":      params.get("to"),
                    "subject": params.get("subject"),
                    "file":    ref,
                },
                "message": "Ready to email this OneDrive file — please confirm to send.",
            }

        item = await self._resolve_item(ref, token)
        if not item:
            return {"success": False, "error": f"File not found in OneDrive: '{ref}'"}

        name    = item["name"]
        item_id = item["id"]

        # Download the file content
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            resp = await client.get(
                f"{_GRAPH_BASE}/me/drive/items/{item_id}/content",
                headers={"Authorization": f"Bearer {token}"},
            )

        if resp.status_code != 200:
            return {"success": False, "error": f"Could not download '{name}': {resp.status_code}"}

        file_bytes = resp.content

        # Send via Gmail
        from integrations.google_drive import _build_mime_attachment, _build_gmail
        gmail_svc = _build_gmail()
        if not gmail_svc:
            return {
                "success": False,
                "error":   "Gmail not connected. Visit /auth/google/start first.",
            }

        raw_msg = _build_mime_attachment(
            to=params["to"],
            subject=params.get("subject", f"Jarvis — {name}"),
            body=params.get("body", f"Please find attached: {name}"),
            filename=name,
            file_bytes=file_bytes,
        )

        loop = asyncio.get_event_loop()
        sent = await loop.run_in_executor(
            None,
            lambda: gmail_svc.users().messages().send(userId="me", body=raw_msg).execute(),
        )
        return {
            "success":  True,
            "message":  f"'{name}' emailed to {params['to']} via Gmail.",
            "email_id": sent["id"],
        }

    # ── Helpers ────────────────────────────────────────────────────────────────

    async def _resolve_item(self, ref: str, token: str) -> dict | None:
        """Find a drive item by ID, path, or name search. Returns the raw Graph item dict."""
        headers = {"Authorization": f"Bearer {token}"}
        sel     = {"$select": "id,name,size,folder"}

        # 1. Try as item ID (long string without spaces or dots)
        if len(ref) > 30 and " " not in ref and "." not in ref:
            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    r = await client.get(
                        f"{_GRAPH_BASE}/me/drive/items/{ref}",
                        headers=headers, params=sel,
                    )
                if r.status_code == 200:
                    return r.json()
            except Exception:
                pass

        # 2. Try as a path (contains / or \)
        if "/" in ref or "\\" in ref:
            path = ref.strip("/\\").replace("\\", "/")
            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    r = await client.get(
                        f"{_GRAPH_BASE}/me/drive/root:/{path}",
                        headers=headers, params=sel,
                    )
                if r.status_code == 200:
                    return r.json()
            except Exception:
                pass

        # 3. Search by name
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                r = await client.get(
                    f"{_GRAPH_BASE}/me/drive/root/search(q='{ref}')",
                    headers=headers,
                    params={"$top": 10, **sel},
                )
            if r.status_code != 200:
                return None

            items = [i for i in r.json().get("value", []) if "folder" not in i]
            if not items:
                return None

            # Prefer exact name match, else first result
            ref_lower = ref.lower()
            exact = [i for i in items if i["name"].lower() == ref_lower]
            return exact[0] if exact else items[0]
        except Exception as e:
            logger.error("OneDrive item resolve error: %s", e)
            return None


onedrive = OneDriveIntegration()
