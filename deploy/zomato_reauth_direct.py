"""
JARVIS — Zomato re-auth using the PRE-REGISTERED Claude Desktop client
=====================================================================
Skips dynamic client registration (which is blocked by Zomato).
Uses the existing client_id that Zomato registered for Claude Desktop:
  fd37dd28-254b-42b7-a55a-c85369d625c8

Flow:
  1) Generate fresh PKCE S256 pair
  2) Start localhost:9753 callback server
  3) Open authorize URL in browser (user approves, already logged into Zomato)
  4) Catch redirect code
  5) POST /token → access_token + refresh_token
  6) Write tokens to claude_desktop_config.json + zomato_auth.json

Usage:
    python deploy\\zomato_reauth_direct.py
"""

from __future__ import annotations
import base64, hashlib, json, secrets, sys, threading, urllib.parse, webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import httpx

# ── Config ────────────────────────────────────────────────────────────────────
CFG_PATH      = Path.home() / "AppData" / "Roaming" / "Claude" / "claude_desktop_config.json"
AUTH_AUTH_URL = "https://mcp-server.zomato.com/authorize"
TOKEN_URL     = "https://mcp-server.zomato.com/token"
CLIENT_ID     = "fd37dd28-254b-42b7-a55a-c85369d625c8"   # Zomato's registered Claude Desktop client
REDIRECT_URI  = "http://localhost:9753/callback"
CALLBACK_PORT = 9753
SCOPE         = "mcp:tools"  # only scope Zomato accepts for this client

_state: dict = {"code": None, "error": None, "done": threading.Event()}


def _pkce_pair() -> tuple[str, str]:
    verifier  = base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode()
    digest    = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def _read_config() -> tuple[dict, dict]:
    cfg = json.loads(CFG_PATH.read_text(encoding="utf-8"))
    env = cfg.setdefault("mcpServers", {}).setdefault("zomato", {}).setdefault("env", {})
    return cfg, env


def _write_config(cfg: dict) -> None:
    CFG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


class _CB(BaseHTTPRequestHandler):
    def do_GET(self):
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if "error" in qs:
            _state["error"] = qs.get("error_description", qs["error"])[0]
            body = b"<h2>Auth failed — check terminal.</h2>"
        else:
            _state["code"] = qs.get("code", [""])[0]
            body = b"<h2>Authorized! Close this tab.</h2>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        _state["done"].set()

    def log_message(self, *a, **kw): pass


def main() -> int:
    print("\n  +--------------------------------------------------+")
    print("  |  JARVIS — Zomato re-auth (pre-registered client)  |")
    print("  +--------------------------------------------------+\n")

    verifier, challenge = _pkce_pair()
    state_val = secrets.token_urlsafe(16)

    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPE,
        "state": state_val,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    auth_url = f"{AUTH_AUTH_URL}?{urllib.parse.urlencode(params)}"

    # Start callback server
    srv = HTTPServer(("localhost", CALLBACK_PORT), _CB)
    t = threading.Thread(target=srv.handle_request, daemon=True)
    t.start()

    print(f"  client_id    : {CLIENT_ID}")
    print(f"  redirect_uri : {REDIRECT_URI}")
    print(f"  verifier     : {verifier[:20]}...")
    print(f"  challenge    : {challenge[:20]}...")
    print()
    print("  [1/3] Opening Zomato auth page in browser...")
    print("        If browser doesn't open, paste this URL manually:")
    print(f"\n    {auth_url}\n")
    try:
        webbrowser.open(auth_url)
    except Exception as e:
        print(f"  (Could not auto-open: {e})")

    print("  [2/3] Waiting for you to approve (5-min timeout)...")
    ok = _state["done"].wait(timeout=300)
    srv.server_close()

    if not ok:
        print("  Timed out. Try again.")
        return 1
    if _state["error"]:
        print(f"  Auth failed: {_state['error']}")
        return 1
    code = _state["code"]
    if not code:
        print("  No code received.")
        return 1

    print(f"  Code received: {code[:12]}...")

    print("\n  [3/3] Exchanging code for tokens...")
    try:
        r = httpx.post(
            TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": CLIENT_ID,
                "redirect_uri": REDIRECT_URI,
                "code_verifier": verifier,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=15,
        )
    except Exception as e:
        print(f"  Token exchange network error: {e}")
        return 1

    print(f"  Token endpoint: HTTP {r.status_code}")
    if r.status_code != 200:
        print(f"  Body: {r.text[:400]}")
        return 1

    data = r.json()
    at = data.get("access_token")
    rt = data.get("refresh_token")
    if not at:
        print(f"  Unexpected response: {data}")
        return 1

    # Write tokens
    cfg, env = _read_config()
    env["ZOMATO_ACCESS_TOKEN"]  = at
    env["ZOMATO_REFRESH_TOKEN"] = rt or ""
    env["ZOMATO_CLIENT_ID"]     = CLIENT_ID
    _write_config(cfg)
    print("  Written to claude_desktop_config.json")

    auth_json = Path(r"C:\Users\premj\OneDrive\Apps\AI Apps\MCP\Zomato\zomato_auth.json")
    if auth_json.exists():
        auth_json.write_text(json.dumps({
            "redirect_uri":  REDIRECT_URI,
            "client_id":     CLIENT_ID,
            "access_token":  at,
            "refresh_token": rt or "",
        }, indent=4), encoding="utf-8")
        print("  Written to zomato_auth.json")

    print("\n  +----------------------------------------+")
    print("  |  SUCCESS — Fresh tokens written.        |")
    print("  +----------------------------------------+")
    print(f"\n  access_token  = {at[:12]}... (len {len(at)})")
    print(f"  refresh_token = {(rt or '')[:12]}... (len {len(rt or '')})")
    print("\n  Next: restart Zomato MCP in Claude Desktop (toggle off/on).\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
