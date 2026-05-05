"""
JARVIS — Zomato MCP re-authentication helper  (v3 — dynamic client registration)
=================================================================================
Zomato's MCP server uses OAuth 2.1 with:
  • Dynamic client registration  (RFC 7591)  → fresh client_id every time
  • PKCE S256  (mandatory per .well-known metadata)
  • Scopes: mcp:tools  mcp:resources  mcp:prompts

Flow:
  1) POST /register  →  fresh client_id
  2) Open browser to /authorize?...&code_challenge=<S256>&...
  3) Catch redirect to localhost:9753/callback?code=...
  4) POST /token with code + code_verifier  →  access_token + refresh_token
  5) Write everything into Claude Desktop config  (both Jarvis and the MCP wrapper use it)

Usage:
    .venv\\Scripts\\python.exe deploy\\zomato_reauth.py
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import sys
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import httpx

# ── Config ────────────────────────────────────────────────────────────────────
CFG_PATH      = Path.home() / "AppData" / "Roaming" / "Claude" / "claude_desktop_config.json"
BASE_URL      = "https://mcp-server.zomato.com"
AUTH_URL      = f"{BASE_URL}/authorize"
TOKEN_URL     = f"{BASE_URL}/token"
REGISTER_URL  = f"{BASE_URL}/register"
REDIRECT_URI  = "http://localhost:9753/callback"
CALLBACK_PORT = 9753
SCOPE         = "mcp:tools mcp:resources mcp:prompts"

# ── Shared state for HTTP callback ─────────────────────────────────────────────
_state: dict = {"code": None, "error": None, "received": threading.Event()}


def _print(msg: str) -> None:
    print(msg, flush=True)


# ── PKCE helpers ──────────────────────────────────────────────────────────────
def _pkce_pair() -> tuple[str, str]:
    verifier  = base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode()
    digest    = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


# ── Config helpers ─────────────────────────────────────────────────────────────
def _read_config() -> tuple[dict, dict]:
    if not CFG_PATH.exists():
        _print(f"ERROR: Claude Desktop config not found at {CFG_PATH}")
        sys.exit(2)
    cfg = json.loads(CFG_PATH.read_text(encoding="utf-8"))
    zomato = cfg.setdefault("mcpServers", {}).setdefault("zomato", {})
    env = zomato.setdefault("env", {})
    return cfg, env


def _write_config(cfg: dict) -> None:
    CFG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


# ── Local HTTP server (catches the OAuth callback) ────────────────────────────
class _CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/callback":
            self.send_response(404); self.end_headers(); return

        qs = urllib.parse.parse_qs(parsed.query)
        if "error" in qs:
            _state["error"] = qs.get("error_description", qs["error"])[0]
            body = b"<h2>Authorization failed.</h2><p>You can close this window.</p>"
        else:
            _state["code"] = qs.get("code", [""])[0]
            body = b"<h2>Authorized! You can close this tab.</h2>"

        self.send_response(200)
        self.send_header("Content-Type",   "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        _state["received"].set()

    def log_message(self, *a, **kw): pass  # silence access log


# ── Main ──────────────────────────────────────────────────────────────────────
def main() -> int:
    _print("")
    _print("  +------------------------------------------------+")
    _print("  |  JARVIS — Zomato re-auth  (fresh client reg)   |")
    _print("  +------------------------------------------------+")
    _print("")

    cfg, env = _read_config()

    # ── Step 1: Dynamic client registration ───────────────────────────────────
    _print("  [1/4] Registering fresh OAuth client with Zomato...")
    try:
        r = httpx.post(
            REGISTER_URL,
            json={
                "redirect_uris":              [REDIRECT_URI],
                "client_name":                "Jarvis MCP",
                "token_endpoint_auth_method": "none",
                "grant_types":                ["authorization_code", "refresh_token"],
                "response_types":             ["code"],
                "scope":                      SCOPE,
            },
            headers={"Content-Type": "application/json"},
            timeout=15,
        )
    except Exception as e:
        _print(f"  ERROR: Could not reach Zomato registration endpoint: {e}")
        return 1

    if r.status_code not in (200, 201):
        _print(f"  ERROR: Registration failed  HTTP {r.status_code}")
        _print(f"  Body: {r.text[:400]}")
        return 1

    reg = r.json()
    client_id = reg.get("client_id")
    if not client_id:
        _print(f"  ERROR: No client_id in registration response: {reg}")
        return 1

    _print(f"  New client_id: {client_id[:8]}...{client_id[-4:]}")

    # ── Step 2: Build authorize URL with PKCE ─────────────────────────────────
    state_val                 = secrets.token_urlsafe(16)
    code_verifier, challenge  = _pkce_pair()

    params = {
        "response_type":         "code",
        "client_id":             client_id,
        "redirect_uri":          REDIRECT_URI,
        "scope":                 SCOPE,
        "state":                 state_val,
        "code_challenge":        challenge,
        "code_challenge_method": "S256",
    }
    auth_url = f"{AUTH_URL}?{urllib.parse.urlencode(params)}"

    # Start callback server BEFORE opening browser
    server        = HTTPServer(("localhost", CALLBACK_PORT), _CallbackHandler)
    server_thread = threading.Thread(target=server.handle_request, daemon=True)
    server_thread.start()

    _print("")
    _print("  [2/4] Opening Zomato authorization page...")
    _print("  If browser doesn't open, paste this URL manually:")
    _print("")
    _print(f"    {auth_url}")
    _print("")
    try:
        webbrowser.open(auth_url)
    except Exception as e:
        _print(f"  (Could not auto-open browser: {e})")

    _print("  Waiting for you to approve in the browser (5-min timeout)...")
    received = _state["received"].wait(timeout=300)
    server.server_close()

    if not received:
        _print("  Timed out waiting for callback. Try again.")
        return 1
    if _state["error"]:
        _print(f"  Authorization failed: {_state['error']}")
        return 1
    code = _state["code"]
    if not code:
        _print("  No authorization code received.")
        return 1

    # ── Step 3: Exchange code for tokens ──────────────────────────────────────
    _print("")
    _print("  [3/4] Exchanging code for tokens...")
    try:
        tr = httpx.post(
            TOKEN_URL,
            data={
                "grant_type":    "authorization_code",
                "code":          code,
                "client_id":     client_id,
                "redirect_uri":  REDIRECT_URI,
                "code_verifier": code_verifier,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=15,
        )
    except Exception as e:
        _print(f"  Token exchange failed (network): {e}")
        return 1

    if tr.status_code != 200:
        _print(f"  Token exchange failed: HTTP {tr.status_code}")
        _print(f"  Body: {tr.text[:400]}")
        return 1

    data   = tr.json()
    new_at = data.get("access_token")
    new_rt = data.get("refresh_token")
    if not new_at or not new_rt:
        _print(f"  Unexpected token response: {data}")
        return 1

    # ── Step 4: Save to both places ───────────────────────────────────────────
    _print("")
    _print("  [4/4] Writing fresh tokens...")

    # Claude Desktop config (used by both Jarvis + the MCP wrapper)
    env["ZOMATO_ACCESS_TOKEN"]  = new_at
    env["ZOMATO_REFRESH_TOKEN"] = new_rt
    env["ZOMATO_CLIENT_ID"]     = client_id
    _write_config(cfg)

    # Also write to zomato_auth.json (legacy location)
    auth_json = Path(r"C:\Users\premj\OneDrive\Apps\AI Apps\MCP\Zomato\zomato_auth.json")
    if auth_json.exists():
        auth_json.write_text(json.dumps({
            "redirect_uri":  REDIRECT_URI,
            "client_id":     client_id,
            "access_token":  new_at,
            "refresh_token": new_rt,
        }, indent=4), encoding="utf-8")
        _print("  Also updated zomato_auth.json")

    _print("")
    _print("  +------------------------------------------+")
    _print("  |  SUCCESS — Fresh tokens written.          |")
    _print("  +------------------------------------------+")
    _print("")
    _print(f"  client_id            = {client_id[:8]}...{client_id[-4:]}")
    _print(f"  ZOMATO_ACCESS_TOKEN  = {new_at[:8]}...{new_at[-4:]}  (len {len(new_at)})")
    _print(f"  ZOMATO_REFRESH_TOKEN = {new_rt[:8]}...{new_rt[-4:]}  (len {len(new_rt)})")
    _print("")
    _print("  Next: restart the 'zomato' MCP connector in Claude Desktop")
    _print("  (toggle off then on) so it picks up the new client_id.")
    _print("")
    return 0


if __name__ == "__main__":
    sys.exit(main())
