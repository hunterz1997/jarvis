"""WhatsApp integration via local Node.js bridge on http://localhost:3001."""

import logging
from typing import Any

import httpx

from config import settings

logger = logging.getLogger(__name__)


class WhatsAppIntegration:
    """Async wrapper for the local WhatsApp bridge."""

    def __init__(self) -> None:
        self.base_url = settings.whatsapp_bridge_url
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if not self._client or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=20.0)
        return self._client

    async def health_check(self) -> dict:
        try:
            client = self._get_client()
            r = await client.get(f"{self.base_url}/status", timeout=5.0)
            return r.json() if r.status_code == 200 else {"status": "error", "code": r.status_code}
        except Exception as e:
            return {"status": "offline", "error": str(e)}

    async def execute(self, tool_name: str, params: dict) -> dict[str, Any]:
        dispatch = {
            "whatsapp_list_chats": self._list_chats,
            "whatsapp_read_messages": self._read_messages,
            "whatsapp_send_message": self._send_message,
        }
        handler = dispatch.get(tool_name)
        if not handler:
            return {"success": False, "error": f"Unknown WhatsApp tool: {tool_name}"}

        health = await self.health_check()
        if health.get("status") not in ("ready", "authenticated"):
            return {
                "success": False,
                "error": "WhatsApp bridge is not running or not authenticated.",
                "suggestion": (
                    "Start the WhatsApp bridge with run.bat, then scan the QR code "
                    "in the terminal window titled 'WhatsApp Bridge'."
                ),
            }
        return await handler(params)

    async def _list_chats(self, params: dict) -> dict:
        try:
            client = self._get_client()
            limit = params.get("limit", 20)
            r = await client.get(f"{self.base_url}/chats", params={"limit": limit})
            return {"success": True, "chats": r.json()}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def _read_messages(self, params: dict) -> dict:
        try:
            client = self._get_client()
            r = await client.get(
                f"{self.base_url}/messages",
                params={"contact": params["contact"], "limit": params.get("limit", 20)},
            )
            return {"success": True, "messages": r.json()}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def _send_message(self, params: dict) -> dict:
        # ── PHASE 1 — confirm message text ───────────────────────────────────
        if not params.get("confirmed"):
            return {
                "success": False,
                "needs_confirmation": True,
                "preview": {
                    "to": params["contact"],
                    "message": params["message"],
                },
                "instruction": (
                    "Show the user this draft and ask: "
                    "'Send this exact message to <contact>?' Wait for an explicit 'yes'. "
                    "Then re-call this tool with confirmed=true."
                ),
            }

        try:
            client = self._get_client()
            payload = {
                "contact": params["contact"],
                "message": params["message"],
            }
            # Optional explicit fields the LLM can pass after disambiguation
            if params.get("chat_id"):
                payload["chat_id"] = params["chat_id"]
            if params.get("allow_group"):
                payload["allow_group"] = True

            r = await client.post(f"{self.base_url}/send", json=payload)
            data = r.json()

            # ── PHASE 2 — bridge says contact is ambiguous → ask user to pick ──
            if r.status_code == 409 and data.get("candidates"):
                return {
                    "success": False,
                    "needs_disambiguation": True,
                    "candidates": data["candidates"],
                    "instruction": (
                        "Multiple chats matched this contact. Show the user the candidates "
                        "list (each item has chat_id, name, isGroup) and ask which one is "
                        "correct. Then re-call this tool with chat_id=<chosen id> and "
                        "confirmed=true. If user picks a group, also pass allow_group=true."
                    ),
                }

            # ── PHASE 3 — bridge refuses to send to a group → confirm intent ──
            if r.status_code == 400 and data.get("chat", {}).get("isGroup"):
                return {
                    "success": False,
                    "needs_group_confirmation": True,
                    "chat": data["chat"],
                    "instruction": (
                        f"The contact '{params['contact']}' resolved to a GROUP chat called "
                        f"'{data['chat']['name']}'. Ask the user: 'This is a GROUP chat — "
                        f"send anyway?' If yes, re-call with chat_id='{data['chat']['id']}' "
                        f"and allow_group=true and confirmed=true."
                    ),
                }

            # Everything else — pass through (success or other error)
            return {"success": data.get("success", False), **data}
        except Exception as e:
            return {"success": False, "error": str(e)}


whatsapp = WhatsAppIntegration()
