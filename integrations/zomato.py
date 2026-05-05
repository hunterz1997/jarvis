"""
Zomato integration for Jarvis.

Proxies all zomato_* tool calls to the LOCAL Zomato MCP server
running at http://127.0.0.1:8765/mcp (streamable-HTTP transport).

No tokens or credentials needed here — the MCP server handles all
authentication via Windows Credential Manager (DPAPI).

To start the local MCP server, run:
    python mcp_servers\\zomato\\zomato_mcp.py
    (with ZOMATO_MCP_TRANSPORT=streamable-http FASTMCP_PORT=8765)

Or it auto-starts at Windows login via the Startup shortcut.

Protocol notes:
  - FastMCP streamable-HTTP requires a session established via `initialize`.
  - The server returns a `Mcp-Session-Id` header that must be forwarded on
    every subsequent request in the same session.
  - If the session expires (server restart), the integration auto-reinitializes.
"""

import json
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# ── Local Zomato MCP server endpoint ─────────────────────────────────────────
_MCP_URL = "http://127.0.0.1:8765/mcp"
_TIMEOUT  = 45.0   # generous: browser-backed tools (login, address) can be slow

# ── Tools that need explicit user confirmation before executing ───────────────
_CONFIRM_REQUIRED = {
    "zomato_checkout",
    "zomato_delete_address",
}


class ZomatoIntegration:
    """
    Thin MCP proxy — converts Jarvis tool calls into JSON-RPC 2.0 requests
    to the local Zomato MCP server and returns the unwrapped result.

    Handles the streamable-HTTP session lifecycle automatically:
      • initialize → captures Mcp-Session-Id
      • All subsequent calls include the session ID header
      • If the server restarts (session lost), reinitializes transparently
    """

    def __init__(self) -> None:
        self._req_id    = 0
        self._session_id: str | None = None   # set after initialize

    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    def _headers(self, *, include_session: bool = True) -> dict:
        """Build request headers, optionally including the MCP session ID."""
        h = {
            "Content-Type": "application/json",
            "Accept":       "application/json, text/event-stream",
        }
        if include_session and self._session_id:
            h["Mcp-Session-Id"] = self._session_id
        return h

    async def _post(self, method: str, params: dict | None = None) -> tuple[Any, dict]:
        """
        POST a JSON-RPC 2.0 message; return (result, response_headers).
        Raises ConnectionError if the server is not reachable.
        Raises RuntimeError for MCP-level errors.
        """
        payload = {
            "jsonrpc": "2.0",
            "id":      self._next_id(),
            "method":  method,
            "params":  params or {},
        }
        is_init = method == "initialize"
        headers = self._headers(include_session=not is_init)

        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(_MCP_URL, json=payload, headers=headers)
                resp.raise_for_status()
        except httpx.ConnectError:
            raise ConnectionError(
                "Cannot reach Zomato MCP server at 127.0.0.1:8765. "
                "Double-click start_jarvis_http.bat to start it, "
                "or check that the startup shortcut ran."
            )
        except httpx.HTTPStatusError as exc:
            body = exc.response.text[:300]
            # 404 on session expired — caller should reinitialize
            if exc.response.status_code == 404:
                raise RuntimeError("SESSION_EXPIRED")
            raise RuntimeError(f"MCP server HTTP {exc.response.status_code}: {body}")

        resp_headers = dict(resp.headers)
        result = self._parse_body(resp)
        return result, resp_headers

    @staticmethod
    def _parse_body(resp: httpx.Response) -> Any:
        """Parse either SSE stream or plain JSON response body."""
        ct = resp.headers.get("content-type", "")
        if "text/event-stream" in ct or "text/plain" in ct:
            for line in resp.text.splitlines():
                if line.startswith("data: "):
                    raw = line[6:].strip()
                    if raw in ("", "[DONE]"):
                        continue
                    try:
                        obj = json.loads(raw)
                        if err := obj.get("error"):
                            raise RuntimeError(f"MCP error: {err}")
                        return obj.get("result")
                    except json.JSONDecodeError:
                        continue
            return None
        # Plain JSON
        try:
            obj = resp.json()
            if err := obj.get("error"):
                raise RuntimeError(f"MCP error: {err}")
            return obj.get("result")
        except Exception:
            return resp.text

    async def _initialize(self) -> None:
        """
        Perform the MCP initialize handshake and capture the session ID.
        Safe to call multiple times — will reinitialize on session expiry.
        """
        try:
            _, headers = await self._post("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities":    {},
                "clientInfo":      {"name": "jarvis", "version": "2.0.0"},
            })
            # FastMCP returns the session ID in the response header
            sid = (
                headers.get("mcp-session-id")
                or headers.get("Mcp-Session-Id")
                or headers.get("x-mcp-session-id")
            )
            if sid:
                self._session_id = sid
                logger.debug("MCP session established: %s", sid)
            else:
                logger.debug("MCP server did not return a session ID (proceeding without)")
        except ConnectionError:
            raise
        except Exception as exc:
            logger.warning("MCP initialize warning: %s", exc)
            # Non-fatal: server may work without explicit session management

    async def _call(self, method: str, params: dict | None = None) -> Any:
        """
        Send an RPC call with automatic session recovery.
        If the session has expired (404 / missing session), reinitializes once.
        """
        if not self._session_id:
            await self._initialize()

        try:
            result, _ = await self._post(method, params)
            return result
        except RuntimeError as exc:
            if "SESSION_EXPIRED" in str(exc):
                logger.info("MCP session expired — reinitializing")
                self._session_id = None
                await self._initialize()
                result, _ = await self._post(method, params)
                return result
            raise

    @staticmethod
    def _unwrap(result: Any) -> Any:
        """
        Unwrap the MCP content-array envelope into plain text or dict.

        MCP tools/call returns:
          {"content": [{"type": "text", "text": "..."}], "isError": false}

        We join the text parts and try to parse as JSON so callers get
        a rich object rather than a raw JSON string.
        """
        if not isinstance(result, dict):
            return result
        content = result.get("content")
        if not content:
            return result
        texts = [
            c.get("text", "")
            for c in content
            if isinstance(c, dict) and c.get("type") == "text"
        ]
        joined = "\n".join(t for t in texts if t)
        try:
            return json.loads(joined)
        except (json.JSONDecodeError, ValueError):
            return joined

    @staticmethod
    def _inject_defaults(tool_name: str, params: dict) -> dict:
        """
        Inject server-required fields that the user (or LLM) typically omits.

        The MCP server's pydantic models are strict — every field without a
        Python default must be present in the JSON.  We handle the common
        optional-feeling-but-technically-required fields here so Jarvis users
        never have to know about them.
        """
        params = dict(params)  # don't mutate caller's dict

        if tool_name == "zomato_search_restaurants":
            # lat/lon are required by the server; pull from Jarvis user settings
            if "lat" not in params or "lon" not in params:
                try:
                    from config import settings
                    params.setdefault("lat", settings.user_location_lat)
                    params.setdefault("lon", settings.user_location_lon)
                except Exception:
                    pass  # server will surface a clear error

        elif tool_name == "zomato_track_order":
            # order_id is required by pydantic but "" means "most recent order"
            params.setdefault("order_id", "")

        elif tool_name == "zomato_login_verify":
            # phone is required by the server; it should normally be provided by
            # the LLM, but default to empty string to avoid a confusing error
            params.setdefault("phone", "")

        return params

    async def execute(self, tool_name: str, params: dict) -> Any:
        """
        Execute a Zomato tool.

        tool_name must be one of the 20 zomato_* tools registered in
        Jarvis's tool_registry.py.  Tool names match the MCP server's
        tool names exactly — no translation needed.
        """
        # ── Inject lat/lon and other required defaults ────────────────────────
        params = self._inject_defaults(tool_name, params)

        # ── Confirmation gate for destructive / financial actions ─────────────
        if tool_name in _CONFIRM_REQUIRED and not params.pop("confirmed", False):
            return {
                "needs_confirmation": True,
                "tool":    tool_name,
                "preview": params,
                "instruction": (
                    "Show the user a clear summary of this action and ask for "
                    "explicit confirmation. Call again with confirmed=true only "
                    "after the user has said yes."
                ),
            }

        try:
            raw = await self._call("tools/call", {
                "name":      tool_name,
                "arguments": params,
            })
            return self._unwrap(raw)

        except ConnectionError as exc:
            logger.warning("Zomato server unreachable: %s", exc)
            return {
                "success":    False,
                "error":      str(exc),
                "suggestion": (
                    "The Zomato MCP server is not running. "
                    "Start it with start_jarvis_http.bat or restart the ZomatoMCP_Jarvis shortcut."
                ),
            }
        except Exception as exc:
            logger.error("Zomato %s failed: %s", tool_name, exc)
            return {"success": False, "error": str(exc)}


# Module-level singleton used by agent.py
zomato = ZomatoIntegration()
