"""
Jarvis — FastAPI entry point.
Startup sequence, static file serving, WebSocket endpoint, health checks.
"""

import asyncio
import io
import logging
import logging.handlers
import os
import socket
import subprocess
import sys
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path

# Force UTF-8 stdout/stderr on Windows so emoji in logs don't crash
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import re

import uvicorn
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from config import settings, BASE_DIR

# ── Logging setup ──────────────────────────────────────────────
def setup_logging() -> None:
    settings.log_file.parent.mkdir(exist_ok=True)
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    root = logging.getLogger()
    root.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))
    # Guard: don't add duplicate handlers if already configured
    if root.handlers:
        return

    # Console handler
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    root.addHandler(console)

    # Rotating file handler
    file_handler = logging.handlers.RotatingFileHandler(
        settings.log_file,
        maxBytes=settings.log_max_bytes,
        backupCount=settings.log_backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    # Suppress noisy third-party loggers
    for name in ("httpx", "httpcore", "uvicorn.access"):
        logging.getLogger(name).setLevel(logging.WARNING)

    # Prevent uvicorn loggers from propagating to root (avoids duplicate lines)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uv_logger = logging.getLogger(name)
        uv_logger.handlers.clear()       # remove uvicorn's own handlers
        uv_logger.propagate = True       # let root logger handle them once


setup_logging()
logger = logging.getLogger(__name__)


# ── Zomato MCP auto-start ──────────────────────────────────────

_ZOMATO_PROC: subprocess.Popen | None = None   # set if WE started the server

_ZOMATO_SCRIPT = Path(r"C:\Users\premj\OneDrive\Apps\AI Apps\MCP\Zomato\zomato_mcp.py")
_ZOMATO_PYTHON = Path(r"C:\Users\premj\AppData\Local\Python\bin\python.exe")
_ZOMATO_PORT   = 8765


def _port_open(port: int) -> bool:
    """Return True if something is already listening on 127.0.0.1:<port>."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", port)) == 0


async def _ensure_zomato_mcp() -> None:
    """
    Start the Zomato MCP HTTP server if it is not already running.
    Waits up to 10 s for the server to become ready.
    Sets the module-level _ZOMATO_PROC so the shutdown hook can clean up.
    """
    global _ZOMATO_PROC

    if _port_open(_ZOMATO_PORT):
        logger.info("  Zomato MCP already running on port %d", _ZOMATO_PORT)
        return

    if not _ZOMATO_SCRIPT.exists():
        logger.warning("  Zomato MCP script not found: %s", _ZOMATO_SCRIPT)
        return

    python = str(_ZOMATO_PYTHON) if _ZOMATO_PYTHON.exists() else sys.executable

    env = {
        **os.environ,
        "ZOMATO_MCP_TRANSPORT": "streamable-http",
        "FASTMCP_HOST":         "127.0.0.1",
        "FASTMCP_PORT":         str(_ZOMATO_PORT),
        "PYTHONUNBUFFERED":     "1",
    }
    popen_kwargs: dict = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "env":    env,
    }
    if sys.platform == "win32":
        # No console window pops up when Jarvis auto-starts the server
        popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

    try:
        _ZOMATO_PROC = subprocess.Popen(
            [python, str(_ZOMATO_SCRIPT)],
            **popen_kwargs,
        )
        logger.info("  Zomato MCP launching (PID %d) — waiting for port %d…",
                    _ZOMATO_PROC.pid, _ZOMATO_PORT)
    except Exception as exc:
        logger.warning("  Failed to start Zomato MCP: %s", exc)
        return

    # Wait up to 10 s for the server to accept connections
    for _ in range(20):
        await asyncio.sleep(0.5)
        if _port_open(_ZOMATO_PORT):
            logger.info("  Zomato MCP ready on port %d", _ZOMATO_PORT)
            return

    logger.warning("  Zomato MCP did not become ready within 10 s — "
                   "tools will work once it finishes starting")


# ── Startup / shutdown ─────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan — runs startup checks then yields."""
    logger.info("=" * 60)
    logger.info("J.A.R.V.I.S starting up…")

    # 1. Validate .env exists
    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        example_path = BASE_DIR / ".env.example"
        if example_path.exists():
            import shutil
            shutil.copy(example_path, env_path)
        logger.warning("⚠  .env not found — created from .env.example. Fill in your API keys.")
        print("\n" + "═" * 60)
        print("  ⚠  JARVIS NEEDS CONFIGURATION")
        print("  Open .env and set your ANTHROPIC_API_KEY")
        print("═" * 60 + "\n")

    # 2. Validate LLM backend
    backend_name = settings.llm_backend.lower()
    if backend_name == "groq":
        from core.llm import GroqBackend
        _b = GroqBackend(settings.groq_api_key, settings.groq_model)
        ok, msg = await _b.check_available()
        if ok:
            logger.info("✓  Groq ready — model: %s", settings.groq_model)
        else:
            logger.warning("⚠  Groq: %s", msg)
    elif backend_name == "ollama":
        from core.llm import OllamaBackend
        _b = OllamaBackend(settings.ollama_model, settings.ollama_url)
        ok, msg = await _b.check_available()
        if ok:
            logger.info("✓  Ollama ready — model: %s", settings.ollama_model)
        else:
            logger.warning("⚠  Ollama: %s", msg)
    else:
        if not settings.anthropic_api_key.startswith("sk-ant-"):
            logger.warning("⚠  ANTHROPIC_API_KEY looks invalid")
        else:
            logger.info("✓  Anthropic API key validated")

    # 3. Initialize SQLite memory
    from core.memory import memory
    await memory.init()
    logger.info("✓  SQLite memory initialized")

    # 4. Start task scheduler
    from core.scheduler import scheduler
    from ui.websocket import manager as ws_manager
    scheduler.set_ws_manager(ws_manager)
    await scheduler.start()
    logger.info("✓  Task scheduler started")

    # 5. WhatsApp bridge health check (non-blocking)
    try:
        from integrations.whatsapp import whatsapp
        health = await whatsapp.health_check()
        if health.get("status") in ("ready", "authenticated"):
            logger.info("✓  WhatsApp bridge online")
        else:
            logger.warning("⚠  WhatsApp bridge not ready: %s", health)
    except Exception as e:
        logger.warning("⚠  WhatsApp bridge check failed: %s", e)

    # 5b. Bridge watchdog — auto-recovers silent stale WA Web sessions
    try:
        from core.bridge_watchdog import watchdog as _bridge_watchdog
        _bridge_watchdog.start()
    except Exception as e:
        logger.warning("⚠  Bridge watchdog failed to start: %s", e)

    # 5c. Zomato MCP — auto-start HTTP server if not already running
    try:
        await _ensure_zomato_mcp()
        if _port_open(_ZOMATO_PORT):
            logger.info("✓  Zomato MCP connected on port %d", _ZOMATO_PORT)
        else:
            logger.warning("⚠  Zomato MCP not ready — Zomato tools will fail until server starts")
    except Exception as e:
        logger.warning("⚠  Zomato MCP startup error: %s", e)

    # 5d. YouTube API — pre-load module and verify API key is available
    try:
        yt_key = os.environ.get("YOUTUBE_API_KEY", "")
        if not yt_key:
            # Fallback: load from Claude Desktop config
            import json as _json
            _cfg = _json.loads(open(
                r"C:\Users\premj\AppData\Roaming\Claude\claude_desktop_config.json",
                encoding="utf-8"
            ).read())
            yt_key = _cfg.get("mcpServers", {}).get("youtube", {}).get("env", {}).get("YOUTUBE_API_KEY", "")
            if yt_key:
                os.environ["YOUTUBE_API_KEY"] = yt_key
        if yt_key:
            logger.info("✓  YouTube API key loaded (%s…)", yt_key[:8])
        else:
            logger.warning("⚠  YOUTUBE_API_KEY not found — add it to .env")
    except Exception as e:
        logger.warning("⚠  YouTube API key check failed: %s", e)

    # 6. Google Calendar / Gmail status check
    google_creds_path = BASE_DIR / "memory" / "google_credentials.json"
    google_secret_path = BASE_DIR / "memory" / "google_client_secret.json"
    if google_creds_path.exists():
        try:
            from google.oauth2.credentials import Credentials
            from google.auth.transport.requests import Request
            creds = Credentials.from_authorized_user_file(str(google_creds_path))
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
                google_creds_path.write_text(creds.to_json())
            if creds.valid:
                logger.info("✓  Google Calendar & Gmail connected")
            else:
                logger.warning("⚠  Google credentials invalid — re-authenticate at /auth/google/start")
        except Exception as e:
            logger.warning("⚠  Google auth check failed: %s", e)
    elif google_secret_path.exists():
        logger.info("ℹ  Google client secret found — visit http://localhost:%d/auth/google/start to connect", settings.port)
    else:
        logger.info("ℹ  Google not connected — visit http://localhost:%d/auth/google/start to set up Calendar & Gmail", settings.port)

    # 7. Auto-open browser (delayed so server is ready first)
    async def open_browser():
        await asyncio.sleep(1.5)
        url = f"http://localhost:{settings.port}"
        webbrowser.open(url)
        logger.info("✓  Browser opened → %s", url)

    asyncio.create_task(open_browser())

    logger.info("=" * 60)
    print(f"\n  ██╗ █████╗ ██████╗ ██╗   ██╗██╗███████╗")
    print(f"     ██╔══██╗██╔══██╗██║   ██║██║██╔════╝")
    print(f"     ███████║██████╔╝██║   ██║██║███████╗")
    print(f"     ██╔══██║██╔══██╗╚██╗ ██╔╝██║╚════██║")
    print(f"     ██║  ██║██║  ██║ ╚████╔╝ ██║███████║")
    print(f"     ╚═╝  ╚═╝╚═╝  ╚═╝  ╚═══╝  ╚═╝╚══════╝")
    print(f"\n  JARVIS online → http://localhost:{settings.port}\n")

    yield  # App is running

    # ── Shutdown ───────────────────────────────────────────────
    from core.scheduler import scheduler
    await scheduler.stop()
    try:
        from core.bridge_watchdog import watchdog as _bridge_watchdog
        await _bridge_watchdog.stop()
    except Exception:
        pass
    await memory.close()

    # Stop Zomato MCP only if Jarvis started it (don't kill an externally-running server)
    if _ZOMATO_PROC is not None and _ZOMATO_PROC.poll() is None:
        _ZOMATO_PROC.terminate()
        logger.info("Zomato MCP server stopped.")

    logger.info("JARVIS shutdown complete.")


# ── FastAPI app ────────────────────────────────────────────────
app = FastAPI(
    title="J.A.R.V.I.S",
    description="Just A Rather Very Intelligent System — Local AI Agent",
    version="1.0.0",
    lifespan=lifespan,
    docs_url=None,  # Disable public API docs
    redoc_url=None,
)

# Static files
static_dir = BASE_DIR / "ui" / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


# ── Routes ─────────────────────────────────────────────────────

@app.get("/", include_in_schema=False)
async def serve_index():
    """Serve the main chat interface."""
    return FileResponse(str(static_dir / "index.html"))


@app.get("/sw.js", include_in_schema=False)
async def serve_service_worker():
    """Serve the service worker from the root URL so it can have site-wide
    scope. Browsers restrict a service worker's scope to its own URL path
    by default, so we serve it at /sw.js (not /static/sw.js)."""
    return FileResponse(
        str(static_dir / "sw.js"),
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"},
    )


@app.get("/manifest.webmanifest", include_in_schema=False)
async def serve_manifest():
    """Mirror the manifest at the root for tools that don't follow the link tag."""
    return FileResponse(
        str(static_dir / "manifest.webmanifest"),
        media_type="application/manifest+json",
    )


@app.get("/health")
async def health():
    """System health check."""
    from ui.websocket import manager
    from integrations.whatsapp import whatsapp
    wa_health = await whatsapp.health_check()
    return JSONResponse({
        "status": "online",
        "active_connections": manager.active_count,
        "whatsapp": wa_health.get("status", "unknown"),
        "models": {
            "opus": settings.opus_model,
            "sonnet": settings.sonnet_model,
        },
    })


@app.get("/usage", include_in_schema=False)
async def get_usage():
    """Return cumulative API token usage and estimated cost."""
    from core.memory import memory
    summary = await memory.get_usage_summary()
    total_str = await memory.get_preference("anthropic_total_credits")
    total = float(total_str) if total_str else None
    remaining = round(total - summary["total_cost_usd"], 4) if total is not None else None
    return JSONResponse({**summary, "total_credits_usd": total, "remaining_usd": remaining})


@app.post("/usage/budget", include_in_schema=False)
async def set_budget(request: Request):
    """Store (or clear) the user's total Anthropic credit balance."""
    from core.memory import memory
    data   = await request.json()
    raw    = data.get("amount", "")
    if raw == "" or raw is None:
        # Clear — delete the preference so we fall back to "X used" display
        await memory._db.execute("DELETE FROM preferences WHERE key='anthropic_total_credits'")
        await memory._db.commit()
        return JSONResponse({"status": "cleared"})
    amount = float(raw)
    await memory.set_preference("anthropic_total_credits", str(round(amount, 2)))
    return JSONResponse({"status": "ok", "total_credits_usd": amount})


@app.get("/sessions")
async def get_sessions():
    """Return recent session list."""
    from core.memory import memory
    sessions = await memory.get_sessions()
    return JSONResponse({"sessions": sessions})


@app.get("/sessions/{session_id}/history")
async def get_session_history(session_id: str):
    """Return message history for a specific session (for restoring chat display)."""
    from core.memory import memory
    history = await memory.get_history(session_id, limit=200)
    messages = [
        {
            "role": r["role"],
            "content": r["content"],
            "timestamp": r["timestamp"],
        }
        for r in history
        if r["role"] in ("user", "assistant") and r.get("content")
    ]
    return JSONResponse({"session_id": session_id, "messages": messages})


@app.delete("/sessions/{session_id}", include_in_schema=False)
async def delete_session(session_id: str):
    """Delete all messages for a session."""
    from core.memory import memory
    await memory.delete_session(session_id)
    return JSONResponse({"status": "deleted", "session_id": session_id})


@app.get("/tasks")
async def get_recent_tasks():
    """Return recent task log."""
    from core.memory import memory
    tasks = await memory.get_recent_tasks(limit=20)
    return JSONResponse({"tasks": tasks})


# ── Google OAuth ──────────────────────────────────────────────

_GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/drive",
]

# Holds the in-progress Flow between /start and /callback so the PKCE
# code_verifier generated in /start is available when /callback exchanges
# the authorization code for tokens.  Single-user local app — one slot is fine.
_pending_google_flow = None


@app.get("/auth/google/start", include_in_schema=False)
async def google_auth_start():
    """Start Google OAuth2 flow — redirect to Google consent screen."""
    global _pending_google_flow
    from fastapi.responses import RedirectResponse, HTMLResponse
    client_secret_path = BASE_DIR / "memory" / "google_client_secret.json"

    if not client_secret_path.exists():
        return HTMLResponse(content="""
        <html><body style="font-family:sans-serif;max-width:600px;margin:40px auto;padding:20px">
        <h2>Google Setup Required</h2>
        <p>To connect Google Calendar and Gmail, you need to create OAuth credentials first:</p>
        <ol>
          <li>Go to <a href="https://console.cloud.google.com/" target="_blank">Google Cloud Console</a></li>
          <li>Create a new project (or select existing)</li>
          <li>Go to <b>APIs & Services → Library</b></li>
          <li>Enable <b>Google Calendar API</b> and <b>Gmail API</b></li>
          <li>Go to <b>APIs & Services → Credentials</b></li>
          <li>Click <b>Create Credentials → OAuth 2.0 Client IDs</b></li>
          <li>Choose <b>Desktop app</b> as the application type</li>
          <li>Download the JSON file</li>
          <li>Save it as: <code>C:\\Claude\\Jarvis\\memory\\google_client_secret.json</code></li>
          <li>Come back and refresh this page</li>
        </ol>
        <p><a href="/">← Back to Jarvis</a></p>
        </body></html>
        """, status_code=200)

    try:
        from google_auth_oauthlib.flow import Flow
        flow = Flow.from_client_secrets_file(
            str(client_secret_path),
            scopes=_GOOGLE_SCOPES,
            redirect_uri=f"http://localhost:{settings.port}/auth/google/callback",
        )
        auth_url, _ = flow.authorization_url(
            access_type="offline",
            prompt="consent",
            # Do NOT pass include_granted_scopes — it merges previously-granted
            # YouTube scopes into the response, triggering a scope-mismatch error.
        )
        # Store the flow so /callback can reuse the same code_verifier (PKCE fix)
        _pending_google_flow = flow
        return RedirectResponse(url=auth_url)
    except Exception as e:
        logger.error("Google OAuth start failed: %s", e)
        return HTMLResponse(content=f"<p>OAuth setup error: {e}</p>", status_code=500)


@app.get("/auth/google/callback", include_in_schema=False)
async def google_auth_callback(code: str = None, error: str = None):
    """Handle Google OAuth2 callback — save credentials."""
    global _pending_google_flow
    from fastapi.responses import HTMLResponse
    if error:
        return HTMLResponse(content=f"<p>OAuth error: {error}</p>", status_code=400)

    if not code:
        return HTMLResponse(content="<p>No authorization code received.</p>", status_code=400)

    client_secret_path = BASE_DIR / "memory" / "google_client_secret.json"
    credentials_path = BASE_DIR / "memory" / "google_credentials.json"

    try:
        from google_auth_oauthlib.flow import Flow
        # Reuse the flow from /start to preserve the PKCE code_verifier
        flow = _pending_google_flow
        if flow is None:
            # Fallback if /start wasn't called in this process lifetime
            flow = Flow.from_client_secrets_file(
                str(client_secret_path),
                scopes=_GOOGLE_SCOPES,
                redirect_uri=f"http://localhost:{settings.port}/auth/google/callback",
            )
        _pending_google_flow = None  # clear slot
        import asyncio
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(None, lambda: flow.fetch_token(code=code))
        except Exception as scope_exc:
            # google-auth-oauthlib raises ScopeChanged when Google returns extra
            # previously-granted scopes (e.g. YouTube). Our required scopes ARE
            # present — save the credentials and continue.
            if "scope" in str(scope_exc).lower() and "changed" in str(scope_exc).lower():
                logger.warning("Google returned extra scopes (OK — saving anyway): %s", scope_exc)
            else:
                raise
        credentials_path.write_text(flow.credentials.to_json())
        logger.info("Google credentials saved to %s", credentials_path)

        return HTMLResponse(content="""
        <html><body style="font-family:sans-serif;max-width:500px;margin:80px auto;text-align:center">
        <h2 style="color:#22c55e">✓ Google Connected!</h2>
        <p>Google Calendar and Gmail are now linked to Jarvis.</p>
        <p>You can now ask Jarvis to create meetings, check your calendar, send emails, and more.</p>
        <p><a href="/" style="color:#3b82f6">← Open Jarvis</a></p>
        <script>setTimeout(() => window.location.href='/', 3000);</script>
        </body></html>
        """)
    except Exception as e:
        logger.error("Google OAuth callback failed: %s", e)
        return HTMLResponse(content=f"<p>Failed to save credentials: {e}</p>", status_code=500)


@app.get("/auth/google/status")
async def google_auth_status():
    """Check Google connection status."""
    creds_path = BASE_DIR / "memory" / "google_credentials.json"
    if creds_path.exists():
        try:
            from google.oauth2.credentials import Credentials
            creds = Credentials.from_authorized_user_file(str(creds_path))
            return JSONResponse({
                "connected": creds.valid or bool(creds.refresh_token),
                "expired": creds.expired,
            })
        except Exception:
            pass
    return JSONResponse({"connected": False})


# ── Microsoft / OneDrive OAuth (Device Code Flow — no Azure portal needed) ──────

# Public client ID: Microsoft Graph Command Line Tools (official MS app)
# Supports device code flow + Files.ReadWrite.All for personal MSA accounts.
# No App Registration required — user just enters code at microsoft.com/devicelogin.
_MS_PUBLIC_CLIENT_ID   = "14d82eec-204b-4c2f-b7e8-296a70dab67e"
_MS_DEVICE_CODE_URL    = "https://login.microsoftonline.com/consumers/oauth2/v2.0/devicecode"
_MS_TOKEN_URL_CONSUMER = "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"
_MS_SCOPES_ONEDRIVE    = "https://graph.microsoft.com/Files.ReadWrite.All offline_access"

# In-memory state shared between /device and the background poller
_pending_device_auth: dict = {}


@app.get("/auth/microsoft/setup", include_in_schema=False)
async def microsoft_setup():
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/auth/microsoft/device")


@app.get("/auth/microsoft/start", include_in_schema=False)
async def microsoft_auth_start():
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/auth/microsoft/device")


@app.get("/auth/microsoft/device", include_in_schema=False)
async def microsoft_device_start():
    """
    Start OneDrive Device Code Flow.
    No Azure App Registration needed — uses Microsoft's own public client.
    User just enters a short code at microsoft.com/devicelogin.
    """
    import json as _json
    from fastapi.responses import HTMLResponse
    import httpx as _httpx

    # If already connected, show status
    creds_path = BASE_DIR / "memory" / "microsoft_credentials.json"
    if creds_path.exists():
        try:
            tokens = _json.loads(creds_path.read_text())
            if tokens.get("refresh_token"):
                return HTMLResponse(content="""
                <html><body style="font-family:sans-serif;text-align:center;padding:60px">
                <h2>✅ OneDrive Already Connected</h2>
                <p>Your OneDrive is linked. Jarvis can access your files.</p>
                <p><a href="/" style="color:#0078d4">← Back to Jarvis</a>
                &nbsp;|&nbsp;
                <a href="/auth/microsoft/device?reauth=1" style="color:#666">Re-authorize</a></p>
                </body></html>""")
        except Exception:
            pass

    # Request device code from Microsoft
    try:
        async with _httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(_MS_DEVICE_CODE_URL, data={
                "client_id": _MS_PUBLIC_CLIENT_ID,
                "scope":     _MS_SCOPES_ONEDRIVE,
            })
    except Exception as e:
        return HTMLResponse(f"<p>Connection error: {e}</p>", status_code=500)

    if resp.status_code != 200:
        return HTMLResponse(
            f"<p>Microsoft error {resp.status_code}: {resp.text[:400]}</p>",
            status_code=500,
        )

    data = resp.json()
    _pending_device_auth.clear()
    _pending_device_auth.update(data)

    user_code  = data.get("user_code", "")
    verify_uri = data.get("verification_uri", "https://microsoft.com/devicelogin")
    expires_in = data.get("expires_in", 900)

    # Start background polling task
    asyncio.create_task(_poll_device_token(data))

    return HTMLResponse(content=f"""
    <html>
    <head>
      <meta charset="UTF-8">
      <title>Connect OneDrive — Jarvis</title>
      <style>
        body {{ font-family: sans-serif; max-width: 520px; margin: 60px auto; padding: 20px; }}
        .code-box {{ background: #f0f4ff; border: 2px solid #0078d4; border-radius: 10px;
                    text-align: center; padding: 28px; margin: 24px 0; }}
        .code {{ font-size: 38px; font-weight: 700; letter-spacing: 6px;
                 color: #0078d4; font-family: monospace; }}
        .btn {{ display: inline-block; background: #0078d4; color: white;
                padding: 12px 28px; border-radius: 6px; text-decoration: none;
                font-weight: 600; font-size: 16px; margin-top: 8px; }}
        .status {{ margin-top: 20px; color: #555; font-size: 14px; }}
        #tick {{ color: green; display: none; font-size: 18px; margin-top: 16px; }}
      </style>
    </head>
    <body>
      <h2>🔷 Connect OneDrive — Step 1 of 2</h2>
      <p>No Azure portal needed. Just enter the code below at Microsoft's login page:</p>

      <div class="code-box">
        <div class="code">{user_code}</div>
        <p style="margin:12px 0 4px;color:#555">Copy this code, then click:</p>
        <a href="{verify_uri}" target="_blank" class="btn">Open microsoft.com/devicelogin →</a>
      </div>

      <p>Sign in with your <b>personal Microsoft account</b>, paste the code, and grant access to your OneDrive files.</p>
      <p class="status">⏳ Waiting for you to complete sign-in… (code expires in {expires_in // 60} minutes)</p>
      <div id="tick">✅ OneDrive connected! <a href="/">Go back to Jarvis →</a></div>

      <script>
        // Poll until tokens are saved
        const poll = setInterval(async () => {{
          const r = await fetch('/auth/microsoft/device/status');
          const d = await r.json();
          if (d.done) {{
            clearInterval(poll);
            document.querySelector('.status').style.display = 'none';
            document.getElementById('tick').style.display = 'block';
          }}
        }}, 3000);
      </script>
    </body>
    </html>
    """)


@app.get("/auth/microsoft/callback", include_in_schema=False)
async def microsoft_auth_callback(code: str = None, error: str = None):
    import json as _json, time as _time
    from fastapi.responses import HTMLResponse
    if error:
        return HTMLResponse(f"<p>OAuth error: {error}</p>", status_code=400)
    if not code:
        return HTMLResponse("<p>No authorization code received.</p>", status_code=400)

    app_path = BASE_DIR / "memory" / "microsoft_app.json"
    app_cfg  = _json.loads(app_path.read_text())
    redirect = f"http://localhost:{settings.port}/auth/microsoft/callback"

    import httpx as _httpx
    async with _httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(_MS_TOKEN_URL, data={
            "grant_type":    "authorization_code",
            "client_id":     app_cfg["client_id"],
            "client_secret": app_cfg.get("client_secret", ""),
            "code":          code,
            "redirect_uri":  redirect,
            "scope":         _MS_SCOPES,
        })

    if resp.status_code != 200:
        return HTMLResponse(f"<p>Token exchange failed: {resp.text[:400]}</p>", status_code=500)

    tokens = resp.json()
    tokens["expires_at"] = _time.time() + tokens.get("expires_in", 3600)
    creds_path = BASE_DIR / "memory" / "microsoft_credentials.json"
    creds_path.write_text(_json.dumps(tokens, indent=2))
    logger.info("Microsoft/OneDrive credentials saved.")
    return HTMLResponse(content="""
    <html><body style="font-family:sans-serif;text-align:center;padding:60px">
    <h2>✅ OneDrive Connected!</h2>
    <p>Jarvis can now access your OneDrive files.</p>
    <p><a href="/">← Back to Jarvis</a></p>
    </body></html>
    """)


@app.get("/auth/microsoft/status", include_in_schema=False)
async def microsoft_auth_status():
    creds_path = BASE_DIR / "memory" / "microsoft_credentials.json"
    if creds_path.exists():
        import json as _json, time as _time
        try:
            tokens  = _json.loads(creds_path.read_text())
            expired = tokens.get("expires_at", 0) < _time.time()
            has_rt  = bool(tokens.get("refresh_token"))
            return JSONResponse({"connected": True, "expired": expired and not has_rt})
        except Exception:
            pass
    return JSONResponse({"connected": False})


@app.get("/auth/microsoft/device/status", include_in_schema=False)
async def microsoft_device_status():
    """Frontend polls this every 3 s to detect when device code auth completes."""
    creds_path = BASE_DIR / "memory" / "microsoft_credentials.json"
    if creds_path.exists():
        import json as _json, time as _time
        try:
            tokens = _json.loads(creds_path.read_text())
            if tokens.get("refresh_token") and tokens.get("expires_at", 0) > _time.time() - 300:
                return JSONResponse({"done": True})
        except Exception:
            pass
    return JSONResponse({"done": False, "pending": bool(_pending_device_auth)})


async def _poll_device_token(device_data: dict) -> None:
    """
    Background task: polls Microsoft token endpoint until the user completes
    device login or the code expires. Saves tokens to microsoft_credentials.json.
    """
    import json as _json, time as _time
    import httpx as _httpx

    interval   = max(device_data.get("interval", 5), 5)
    expires_in = device_data.get("expires_in", 900)
    deadline   = _time.time() + expires_in

    while _time.time() < deadline:
        await asyncio.sleep(interval)
        try:
            async with _httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(_MS_TOKEN_URL_CONSUMER, data={
                    "grant_type":  "urn:ietf:params:oauth:grant-type:device_code",
                    "client_id":   _MS_PUBLIC_CLIENT_ID,
                    "device_code": device_data["device_code"],
                })
        except Exception as e:
            logger.warning("OneDrive poll error: %s", e)
            continue

        if resp.status_code == 200:
            tokens = resp.json()
            tokens["expires_at"] = _time.time() + tokens.get("expires_in", 3600)
            creds_path = BASE_DIR / "memory" / "microsoft_credentials.json"
            creds_path.write_text(_json.dumps(tokens, indent=2))
            _pending_device_auth.clear()
            logger.info("✓ OneDrive device code auth complete — tokens saved")
            return

        error = resp.json().get("error", "")
        if error == "authorization_pending":
            continue  # user hasn't entered the code yet — keep polling
        if error == "slow_down":
            interval += 5  # back off as instructed
            continue
        # access_denied, expired_token, or unknown error — give up
        logger.warning("OneDrive device auth failed: %s — %s", error, resp.json().get("error_description", ""))
        _pending_device_auth.clear()
        return

    logger.warning("OneDrive device code expired before user completed auth")
    _pending_device_auth.clear()


# ── WhatsApp QR endpoint (for cloud VPS — user scans from phone) ──────────────

@app.get("/wa/qr", include_in_schema=False)
async def serve_wa_qr():
    """
    Render WhatsApp QR code as a scannable page — for cloud VPS setup.
    Fetches raw QR data from the bridge and renders it via qrcode.js in the browser.
    """
    from fastapi.responses import HTMLResponse
    import httpx as _httpx
    try:
        async with _httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{settings.whatsapp_bridge_url}/qr")
        data = resp.json()
    except Exception:
        data = {}

    if not data.get("success") or not data.get("qr"):
        status = data.get("status", "unknown")
        if status in ("ready", "authenticated"):
            return HTMLResponse("""
            <html><body style="font-family:sans-serif;text-align:center;padding:60px">
            <h2>✅ WhatsApp Already Connected</h2>
            <p>No QR needed — session is active.</p>
            <p><a href="/">← Back to Jarvis</a></p>
            </body></html>""")
        return HTMLResponse(f"""
        <html><head><meta http-equiv="refresh" content="5"></head>
        <body style="font-family:sans-serif;text-align:center;padding:60px">
        <h2>⏳ Waiting for QR code…</h2>
        <p>Bridge status: <b>{status}</b></p>
        <p>This page refreshes every 5 seconds. WhatsApp bridge may still be starting.</p>
        </body></html>""")

    qr_data = data["qr"]
    return HTMLResponse(content=f"""
    <html>
    <head>
      <title>Jarvis — Scan WhatsApp QR</title>
      <script src="https://cdnjs.cloudflare.com/ajax/libs/qrcodejs/1.0.0/qrcode.min.js"></script>
      <meta http-equiv="refresh" content="30">
      <style>
        body {{ font-family: sans-serif; text-align: center; padding: 40px; background: #f9f9f9; }}
        #qrcode {{ display: inline-block; padding: 20px; background: white;
                   border-radius: 12px; box-shadow: 0 4px 20px rgba(0,0,0,.1); margin: 20px; }}
        #qrcode canvas, #qrcode img {{ display: block; }}
      </style>
    </head>
    <body>
      <h2>📱 Scan to connect Jarvis to WhatsApp</h2>
      <p>Open WhatsApp on your phone → <b>Linked Devices</b> → <b>Link a Device</b></p>
      <div id="qrcode"></div>
      <p style="color:#888;font-size:13px">Page auto-refreshes every 30 s. QR expires after ~60 s.</p>
      <script>
        new QRCode(document.getElementById("qrcode"), {{
          text: {repr(qr_data)},
          width: 300, height: 300,
          colorDark: "#000000", colorLight: "#ffffff",
          correctLevel: QRCode.CorrectLevel.M
        }});
      </script>
    </body>
    </html>
    """)


# ── WhatsApp incoming webhook ──────────────────────────────────

@app.post("/webhook/whatsapp", include_in_schema=False)
async def whatsapp_webhook(request: Request):
    """
    Called by the WhatsApp bridge when the user sends a Jarvis-triggered
    self-message. Processes in background and sends the full reply directly.
    """
    import asyncio

    data = await request.json()
    body    = data.get("body", "").strip()
    chat_id = data.get("chat_id", "")

    if not body or not chat_id:
        return JSONResponse({"status": "ignored"})

    # Strip trigger phrase — extract the actual user intent
    clean = re.sub(r'^hey\s+jarvis[,!\s]*', '', body, flags=re.IGNORECASE).strip()
    clean = re.sub(r'^jarvis[,!\s]*',        '', clean, flags=re.IGNORECASE).strip()
    if not clean:
        return JSONResponse({"status": "no_content"})

    # Process agent in background — return 200 immediately so bridge doesn't time out
    asyncio.create_task(_handle_wa_message(chat_id, clean))
    return JSONResponse({"status": "queued"})


async def _handle_wa_message(chat_id: str, message: str) -> None:
    """Run the Jarvis agent for a WhatsApp message and send the reply back."""
    import httpx
    from core.agent import agent

    logger.info("[WA] Processing: %s", message[:80])
    try:
        parts: list[str] = []
        async for event in agent.run("whatsapp_me", message):
            if event.get("type") == "text":
                parts.append(event.get("content", ""))

        reply = "".join(parts).strip() or "Done."

        async with httpx.AsyncClient(timeout=15) as http:
            await http.post(
                "http://127.0.0.1:3001/send-by-id",
                json={"chat_id": chat_id, "message": reply},
            )
        logger.info("[WA] Replied (%d chars)", len(reply))

        # Notify any open UI tabs that usage changed (so credits widget refreshes)
        try:
            from ui.websocket import manager as _ws_manager
            await _ws_manager.broadcast({"type": "usage_updated", "source": "whatsapp"})
        except Exception:
            pass  # best-effort — never block reply on UI broadcast failure

    except Exception as exc:
        logger.error("[WA] Handler error: %s", exc)
        try:
            async with httpx.AsyncClient(timeout=5) as http:
                await http.post(
                    "http://127.0.0.1:3001/send-by-id",
                    json={"chat_id": chat_id, "message": f"⚠️ Jarvis error: {str(exc)[:120]}"},
                )
        except Exception:
            pass


# ── WebSocket ──────────────────────────────────────────────────

@app.websocket("/ws/{session_id}")
async def websocket_route(websocket: WebSocket, session_id: str):
    from ui.websocket import websocket_endpoint
    await websocket_endpoint(websocket, session_id)


# ── Entry point ────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=settings.host,     # Always 127.0.0.1 — never 0.0.0.0
        port=settings.port,
        reload=False,
        log_config=None,        # Use our own logging config
        access_log=False,
    )
