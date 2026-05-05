# J.A.R.V.I.S

**Just A Rather Very Intelligent System** — a fully local, privacy-first AI executive assistant for Windows.

[![Python](https://img.shields.io/badge/Python-3.11%2B-blue?logo=python&logoColor=white)](https://python.org)
[![Node.js](https://img.shields.io/badge/Node.js-18%2B-green?logo=node.js&logoColor=white)](https://nodejs.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115%2B-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/Platform-Windows%2010%2F11-0078D4?logo=windows&logoColor=white)](https://microsoft.com/windows)

---

Jarvis is a local AI agent that runs entirely on your Windows machine — no cloud subscription, no data leaving your device. It combines a Tony Stark-inspired browser UI with deep integrations into Gmail, Google Calendar, Google Drive, OneDrive, WhatsApp, LinkedIn, YouTube, and Zomato food ordering. Talk to it by typing or voice, and it executes real tasks: books meetings, sends emails, orders food, searches the web, and manages your files — all from a single chat interface.

Everything runs at `http://localhost:8000`. Your conversations, credentials, and API tokens never leave your machine.

---

## Table of Contents

1. [Architecture](#architecture)
2. [Features](#features)
3. [Prerequisites](#prerequisites)
4. [Quick Start](#quick-start)
5. [Configuration Reference](#configuration-reference)
6. [Integration Setup](#integration-setup)
   - [Google (Gmail, Calendar, Drive)](#google-gmail-calendar-drive)
   - [WhatsApp](#whatsapp)
   - [OneDrive](#onedrive)
   - [Zomato Food Ordering](#zomato-food-ordering)
   - [LinkedIn](#linkedin)
   - [YouTube](#youtube)
7. [Voice Mode](#voice-mode)
8. [LLM Backend Options](#llm-backend-options)
9. [Remote Access via Tailscale](#remote-access-via-tailscale)
10. [Auto-start on Boot](#auto-start-on-boot)
11. [Secrets Management](#secrets-management)
12. [Project Structure](#project-structure)
13. [Extending Jarvis](#extending-jarvis)
14. [Privacy](#privacy)
15. [License](#license)

---

## Architecture

```
+------------------------------------------------------------------+
|                    Browser  (localhost:8000)                      |
|          Arc Reactor UI  .  Voice Mode  .  Face Login            |
+---------------------------+--------------------------------------+
                            |  WebSocket  (streaming)
                            v
+------------------------------------------------------------------+
|                  FastAPI Server  (main.py)                        |
|   Lifespan manager  .  OAuth routes  .  WebSocket handler        |
|   WhatsApp webhook  .  Session/history API  .  Health check      |
+---------------------------+--------------------------------------+
                            |
                  +---------v----------+
                  |    Agent Loop      |   core/agent.py
                  |    (streaming)     |   iterates tool calls
                  |                    |   up to 15 rounds
                  +---------+----------+
                            |
               +------------v--------------+
               |       LLM Backend         |   core/llm.py
               |  Groq  |  Anthropic        |   Groq (default, free)
               |  Ollama                   |   claude-sonnet-4-5
               +------------+--------------+   Ollama (offline)
                            |  tool_use
   +------------------------v------------------------------------------+
   |                      Tool Router                                   |
   |                   core/tool_router.py                              |
   +----+--------+--------+--------+----------+-----------------------+-+
        |        |        |        |          |                       |
     Gmail   Calendar   Drive  WhatsApp   Zomato MCP (:8765)    YouTube
     OneDrive LinkedIn  Web    Computer   LinkedIn               Files
```

**Data flow for a typical request:**

1. User message arrives over WebSocket.
2. Agent adds it to SQLite history, builds full context, calls the LLM.
3. LLM responds with `tool_use` blocks.
4. Tool router dispatches each call to the relevant integration.
5. Results are fed back to the LLM for the next iteration.
6. Final text answer streams back to the browser token by token.

---

## Features

### AI

- **Streaming responses** — text appears word by word via WebSocket, no waiting for the full reply
- **Multi-backend LLM** — Groq (free, fast), Anthropic claude-sonnet-4-5, or fully-offline Ollama
- **Smart model routing** — automatically selects a more capable model for complex multi-step tasks
- **Persistent memory** — full conversation history in SQLite; sessions survive restarts
- **60+ tools** — web search, file ops, all Google Workspace, WhatsApp, LinkedIn, YouTube, Zomato
- **APScheduler** — time-based reminders and recurring tasks (e.g. "remind me every Monday at 9am")

### Integrations

| Integration | Capabilities |
|---|---|
| **Gmail** | Read, search, send, draft, label emails |
| **Google Calendar** | Create/edit/delete events, find free slots, respond to invites |
| **Google Drive** | List, read, upload, search, share files |
| **OneDrive** | List, read, upload files — no Azure App Registration needed |
| **WhatsApp** | Send/receive messages, push notifications to your phone |
| **LinkedIn** | Read profile, list/create posts, analytics, search people |
| **YouTube** | Search videos, get transcripts, channel analytics, upload |
| **Zomato** | Search restaurants, browse menus, place orders, track delivery |
| **Web** | DuckDuckGo search, full-page fetch and scraping |
| **Computer** | Read/write files, run commands, clipboard, screenshots, system info |

### UI

- **Arc Reactor theme** — animated UI inspired by Tony Stark's J.A.R.V.I.S
- **Boot animation** — cinematic startup sequence on first load
- **Face-scan login overlay** — cosmetic security screen rendered in pure CSS/JS
- **Voice mode** — speak your query, hear the reply (Web Speech API, browser-native, free)
- **Touch and gesture support** — swipe to clear, pull to refresh on mobile
- **PWA** — installable as a desktop or home-screen app via Web App Manifest
- **Multi-session** — separate conversation threads, switchable from the sidebar
- **Usage tracker** — live token count and estimated API cost display

### Privacy

- Runs entirely on `localhost` — no external server, no cloud relay
- Credentials stored in Windows Credential Manager (encrypted at rest)
- SQLite database lives in `memory/jarvis.db` on your machine
- Browser profile for WhatsApp stored locally under `browser_profile/`
- LLM calls go only to the provider you configure (Groq, Anthropic, or Ollama localhost)

---

## Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Windows | 10 or 11 | Required for Credential Manager, Task Scheduler integration |
| Python | 3.11+ | [python.org/downloads](https://python.org/downloads) |
| Node.js | 18+ | [nodejs.org](https://nodejs.org) — required for WhatsApp bridge |
| Groq API key | — | Free at [console.groq.com](https://console.groq.com) — default backend |
| OR Anthropic API key | — | Free $5 credit at [console.anthropic.com](https://console.anthropic.com) |

Google, WhatsApp, OneDrive, LinkedIn, and YouTube are all optional — Jarvis works without any of them.

---

## Quick Start

```bat
REM 1. Clone the repository
git clone https://github.com/YOUR_USERNAME/jarvis.git
cd jarvis

REM 2. Copy the environment template
copy .env.example .env
notepad .env
```

Set at minimum one LLM backend key in `.env`:

```env
# Option A: Groq (free, recommended for getting started)
LLM_BACKEND=groq
GROQ_API_KEY=gsk_your_key_here

# Option B: Anthropic
LLM_BACKEND=anthropic
ANTHROPIC_API_KEY=sk-ant-your_key_here

# Option C: Ollama (no API key needed, fully offline)
LLM_BACKEND=ollama
OLLAMA_MODEL=llama3.2
```

```bat
REM 3. Launch Jarvis
run.bat
```

`run.bat` handles everything on first run:

- Creates a Python virtual environment at `.venv\`
- Installs Python dependencies from `requirements.txt`
- Installs Node.js dependencies in `whatsapp_bridge\`
- Starts the WhatsApp bridge (`node whatsapp_bridge\server.js`)
- Starts the FastAPI server (`python main.py`)
- Opens `http://localhost:8000` in your default browser

**Subsequent runs** are fast — the venv and node_modules already exist.

For a **silent background launch** with no console window:

```powershell
powershell -ExecutionPolicy Bypass -File deploy\start_jarvis_silent.ps1
```

---

## Configuration Reference

All settings are loaded from `.env`. Copy `.env.example` as a starting point.

### LLM Backends

```env
# Which backend to use: groq | anthropic | ollama
LLM_BACKEND=groq

# Groq — free tier, llama-3.3-70b-versatile by default
GROQ_API_KEY=gsk_...
GROQ_MODEL=llama-3.3-70b-versatile

# Anthropic
ANTHROPIC_API_KEY=sk-ant-...

# Ollama — must be running separately via "ollama serve"
OLLAMA_URL=http://localhost:11434
OLLAMA_MODEL=llama3.2
```

### Server

```env
HOST=127.0.0.1    # Never set to 0.0.0.0 — use Tailscale for remote access
PORT=8000
```

### Integrations

```env
# WhatsApp bridge — default, no change needed
WHATSAPP_BRIDGE_URL=http://localhost:3001

# YouTube Data API v3
YOUTUBE_API_KEY=AIza...

# Location — used for Zomato restaurant search
USER_CITY=Mumbai
USER_LOCATION_LAT=19.0760
USER_LOCATION_LON=72.8777
```

### Logging and Memory

```env
LOG_LEVEL=INFO
MAX_HISTORY_MESSAGES=50    # Messages kept per session in context window
```

---

## Integration Setup

### Google (Gmail, Calendar, Drive)

A single OAuth2 flow authorizes all three Google services at once.

**Step 1 — Create a Google Cloud project**

1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Create a new project (e.g. "Jarvis")
3. Go to **APIs & Services → Library** and enable:
   - **Gmail API**
   - **Google Calendar API**
   - **Google Drive API**
4. Go to **APIs & Services → OAuth consent screen**
   - Choose **External**, fill in the app name, add your Gmail address as a test user
5. Go to **APIs & Services → Credentials**
   - Click **Create Credentials → OAuth 2.0 Client ID**
   - Application type: **Desktop app**
   - Click **Download JSON**
6. Save the downloaded file as:
   ```
   C:\Claude\Jarvis\memory\google_client_secret.json
   ```

**Step 2 — Authorize Jarvis**

With Jarvis running, open your browser and navigate to:
```
http://localhost:8000/auth/google/start
```

Complete the Google consent screen. After you approve, credentials are saved automatically to `memory/google_credentials.json`. Tokens refresh silently — you only need to do this once.

**Check status at any time:**
```
http://localhost:8000/auth/google/status
```

---

### WhatsApp

The WhatsApp bridge uses [whatsapp-web.js](https://github.com/pedroslopez/whatsapp-web.js) — a Node.js library that automates WhatsApp Web locally via Chromium. No WhatsApp Business API, no phone number rental, and no third-party gateway is involved.

**Step 1 — Start the bridge**

```bat
cd whatsapp_bridge
node server.js
```

Or just run `run.bat` which starts it automatically alongside the main server.

**Step 2 — Scan the QR code**

On first run, the bridge prints a QR code in the terminal. On headless or remote setups, navigate to:

```
http://localhost:8000/wa/qr
```

Jarvis renders the QR code in the browser for easy scanning.

**On your phone:** Open WhatsApp → Menu (three dots) → **Linked Devices** → **Link a Device** → scan the code.

**Step 3 — Enable push notifications (optional)**

Once connected, you can query Jarvis directly from WhatsApp by sending yourself a message that starts with `Hey Jarvis` or `Jarvis`:

```
Hey Jarvis, what's on my calendar tomorrow?
Jarvis, order me a pizza from Domino's
Hey Jarvis, summarize my unread emails
```

The bridge webhook receives the message, runs it through the full agent (with all tools), and sends the reply back to your WhatsApp.

**Session persistence:** The Chromium session is stored in `browser_profile/` and survives restarts. You will not need to scan the QR code again unless WhatsApp explicitly logs the device out.

---

### OneDrive

Jarvis uses Microsoft's public Device Code Flow. No Azure App Registration or Azure portal is required.

1. With Jarvis running, navigate to:
   ```
   http://localhost:8000/auth/microsoft/device
   ```
2. Copy the short alphanumeric code shown on the page (e.g. `A3B7XP9Q`)
3. Click the link to open [microsoft.com/devicelogin](https://microsoft.com/devicelogin)
4. Sign in with your **personal Microsoft account** and paste the code when prompted
5. Grant access to your OneDrive files
6. The Jarvis page polls for completion and automatically redirects you back when done

Credentials are saved to `memory/microsoft_credentials.json` with a refresh token. Re-authentication is not needed after this.

---

### Zomato Food Ordering

Zomato uses a custom local MCP server with phone-number OTP login. No OAuth credentials or paid API key are required.

**Step 1 — Install the Zomato MCP server**

The Zomato MCP is a separate project. Clone or download it to your machine:

```bash
git clone https://github.com/YOUR_USERNAME/zomato-mcp.git
# e.g. placed at: C:\Users\YourName\Apps\zomato_mcp.py
```

Install its dependencies in its own virtual environment:

```bash
cd zomato-mcp
pip install "mcp[cli]" curl-cffi playwright
playwright install chromium
```

**Step 2 — Configure the path in `main.py`**

Open `main.py` and update the two path constants near the top of the file:

```python
_ZOMATO_SCRIPT = Path(r"C:\path\to\your\zomato_mcp.py")
_ZOMATO_PYTHON = Path(r"C:\path\to\your\python.exe")
```

Jarvis auto-starts this script on port 8765 at startup and shuts it down cleanly on exit.

**Step 3 — Log in via OTP**

Start Jarvis and ask it to log you in:

```
Jarvis, log me into Zomato with my number +91-XXXXXXXXXX
```

Jarvis calls `zomato_login_start`, you receive an OTP on your phone, and you provide it:

```
The OTP is 284716
```

Jarvis calls `zomato_login_verify` to complete the session. The session persists across restarts.

**Ordering food:**

```
Jarvis, find biryani restaurants near me
Jarvis, show me the menu for Paradise Biryani
Jarvis, order one Chicken Dum Biryani and pay on delivery
Jarvis, where is my order?
```

---

### LinkedIn

The LinkedIn integration forwards tool calls to a locally-running LinkedIn MCP server.

**Step 1 — Set up the LinkedIn MCP**

Follow the setup guide for whichever LinkedIn MCP you are using (e.g. the Claude Desktop LinkedIn connector). It handles authentication via the LinkedIn browser extension or OAuth.

**Step 2 — Verify it is running**

The integration at `integrations/linkedin.py` connects to the MCP server. Ensure the MCP is running before starting Jarvis.

**Available tools:** read profile, list posts, create post, delete post, get post analytics, search people, search content, get network size, get comments, reply to comment, update cache.

---

### YouTube

**Step 1 — Get a YouTube Data API v3 key**

1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. In your project, go to **APIs & Services → Library** and enable **YouTube Data API v3**
3. Go to **Credentials → Create Credentials → API key**
4. Copy the key

**Step 2 — Add to `.env`**

```env
YOUTUBE_API_KEY=AIza...
```

Restart Jarvis. The key is validated at startup.

**Available tools:** search videos, get video info, get transcript, get comments, post comment, reply to comment, get channel info, get channel analytics, list your videos, upload video, update video.

---

## Voice Mode

Voice mode uses the browser's built-in **Web Speech API** — no external service, no API key, no additional cost.

**Browser support:**

| Browser | Speech Recognition | Text-to-Speech |
|---|---|---|
| Chrome (desktop) | Yes | Yes |
| Edge (desktop) | Yes | Yes |
| Firefox | No (flag required) | Yes |
| Safari (iOS) | Yes | Yes |
| Chrome (Android) | Yes | Yes |

**To activate:**

1. Click the microphone icon in the Jarvis UI
2. Allow microphone access when the browser prompts
3. Speak your query — Jarvis listens, transcribes locally, sends the message, then reads the reply aloud

**Voice settings** (gear icon in the top-right corner):

- **Voice** — choose from installed system TTS voices
- **Rate / Pitch** — adjust speaking speed and tone
- **Auto-speak** — toggle whether Jarvis always reads responses aloud

**Note on privacy:** Chrome and Edge speech recognition sends audio to Google's servers for transcription. If you require fully offline voice, run a local [Whisper](https://github.com/openai/whisper) server and update the WebSocket endpoint in `ui/static/voice-mode.js`.

---

## LLM Backend Options

Switch backends by changing `LLM_BACKEND` in `.env` and restarting Jarvis.

### Groq (Default — Recommended)

```env
LLM_BACKEND=groq
GROQ_API_KEY=gsk_...
GROQ_MODEL=llama-3.3-70b-versatile
```

Groq provides free API access to Meta's Llama and Mistral models running on custom LPU hardware. Extremely fast (100+ tokens/second). The free tier is generous enough for personal use. Get a key at [console.groq.com](https://console.groq.com).

Other Groq models worth trying:

| Model | Speed | Context | Notes |
|---|---|---|---|
| `llama-3.3-70b-versatile` | Fast | 128k | Best overall for tool use |
| `llama-3.1-8b-instant` | Very fast | 128k | Light tasks, quick queries |
| `mixtral-8x7b-32768` | Fast | 32k | Good for long documents |

### Anthropic

```env
LLM_BACKEND=anthropic
ANTHROPIC_API_KEY=sk-ant-...
```

Uses `claude-sonnet-4-5` for all requests. Best tool-use accuracy and reasoning quality. New accounts receive $5 in free credits at [console.anthropic.com](https://console.anthropic.com).

Monitor spend with the built-in usage endpoint:

```
http://localhost:8000/usage
```

You can set your total credit balance via the UI so Jarvis displays remaining credits.

### Ollama (Fully Offline)

```env
LLM_BACKEND=ollama
OLLAMA_URL=http://localhost:11434
OLLAMA_MODEL=llama3.2
```

Requires [Ollama](https://ollama.ai) installed and running (`ollama serve`). Pull a model first:

```bash
ollama pull llama3.2          # 2B — runs on any modern machine
ollama pull qwen2.5:7b        # 7B — better reasoning, requires ~8 GB RAM
ollama pull mistral           # 7B — good tool-use support
ollama pull qwen2.5:0.5b      # 0.5B — ultra-light for low-end hardware
```

Tool-use accuracy varies by model size. `llama3.2`, `qwen2.5:7b`, and `mistral` are the most reliable for multi-step tool chains. Models under 3B may struggle with complex sequences.

---

## Remote Access via Tailscale

Tailscale creates a private WireGuard mesh between your devices. Once set up, you can access Jarvis from your phone or laptop anywhere in the world over a stable HTTPS URL — no port forwarding, no dynamic DNS, no VPN configuration.

**Step 1 — Install Tailscale**

Download from [tailscale.com/download](https://tailscale.com/download) and sign in with Google, GitHub, or email.

**Step 2 — Issue a TLS certificate for your machine**

```powershell
tailscale cert your-machine-name.ts.net
```

This uses Let's Encrypt to issue a certificate for your Tailscale hostname and saves two files (`.crt` and `.key`) in the current directory. Move them to `C:\Claude\Jarvis\`.

**Step 3 — Configure Jarvis to bind on all interfaces**

In `.env`:
```env
HOST=0.0.0.0
PORT=8443
```

**Step 4 — Launch with TLS**

Update the uvicorn call at the bottom of `main.py`:

```python
uvicorn.run(
    "main:app",
    host=settings.host,
    port=settings.port,
    ssl_certfile="your-machine.ts.net.crt",
    ssl_keyfile="your-machine.ts.net.key",
    reload=False,
    log_config=None,
    access_log=False,
)
```

Or run directly:

```bat
.venv\Scripts\uvicorn main:app ^
  --host 0.0.0.0 --port 8443 ^
  --ssl-certfile your-machine.ts.net.crt ^
  --ssl-keyfile  your-machine.ts.net.key
```

**Step 5 — Open the Windows firewall port**

```powershell
New-NetFirewallRule -DisplayName "Jarvis HTTPS" `
  -Direction Inbound -Protocol TCP -LocalPort 8443 -Action Allow
```

**Access from any Tailscale-connected device:**

```
https://your-machine-name.ts.net:8443
```

The face-scan login overlay, voice mode, and PWA install all work correctly over HTTPS on mobile.

---

## Auto-start on Boot

Jarvis includes a PowerShell watchdog that starts both the WhatsApp bridge and the FastAPI server at Windows login and automatically restarts either process if it crashes.

**Install the startup shortcut:**

```bat
cd deploy
setup_autostart.bat
```

This creates a `.lnk` shortcut in your Windows Startup folder (`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup`) that runs `start_jarvis_silent.ps1` on every login.

**What the watchdog does:**

- Starts the WhatsApp bridge (port 3001) and Jarvis API (port 8000) immediately
- Waits 4 seconds between bridge and API start to ensure the bridge is ready
- Checks every 60 seconds whether each process is still listening
- Restarts any process that has exited
- Logs all events with timestamps to `logs/autostart.log`
- Runs with no console window (`-WindowStyle Hidden`)

**Test without rebooting:**

```powershell
powershell -ExecutionPolicy Bypass -File deploy\start_jarvis_silent.ps1
```

**Remove auto-start:**

```bat
deploy\setup_autostart.bat remove
```

**Opening the UI after a silent boot:**

The watchdog starts the server in the background but does not open a browser window (by design — you may not always want a browser at login). To open Jarvis:

- Double-click `launcher.pyw` (silent Python launcher, no console window)
- Or navigate to `http://localhost:8000` in any browser

---

## Secrets Management

By default, Jarvis reads API keys from `.env`. For better security, migrate them to the **Windows Credential Vault** — encrypted at rest, accessible only under your Windows login session, and never written to disk in plaintext.

```powershell
# Migrate ANTHROPIC_API_KEY and GROQ_API_KEY from .env into the vault
.venv\Scripts\python.exe deploy\manage_secrets.py migrate

# List vault contents (values are never printed, only presence confirmed)
.venv\Scripts\python.exe deploy\manage_secrets.py list

# Set a single key interactively (input is hidden as you type)
.venv\Scripts\python.exe deploy\manage_secrets.py set ANTHROPIC_API_KEY

# Remove a key from the vault (Jarvis falls back to .env value)
.venv\Scripts\python.exe deploy\manage_secrets.py delete ANTHROPIC_API_KEY
```

Once migrated, vault values override `.env` on every startup. You can safely delete or blank out the migrated lines from `.env`.

---

## Project Structure

```
Jarvis/
|-- main.py                        FastAPI entry point, lifespan, OAuth routes
|-- config.py                      Pydantic settings, keyring integration
|-- requirements.txt               Python dependencies
|-- run.bat                        Dev launcher (console window, auto-opens browser)
|-- launcher.pyw                   Silent launcher (no console window)
|-- setup.py                       First-run setup wizard
|-- .env.example                   Template — copy to .env
|
|-- core/
|   |-- agent.py                   Main agent loop — streams tool calls
|   |-- llm.py                     Multi-backend LLM (Groq / Anthropic / Ollama)
|   |-- memory.py                  SQLite conversation history and preferences
|   |-- scheduler.py               APScheduler — reminders, recurring tasks
|   |-- tool_registry.py           All 60+ tool definitions (Anthropic format)
|   |-- tool_router.py             Dispatches tool calls to integrations
|   |-- system_prompt.py           Jarvis personality and capabilities
|   |-- model_router.py            Smart model selection by task complexity
|   +-- bridge_watchdog.py         WhatsApp bridge auto-recovery
|
|-- integrations/
|   |-- gmail.py                   Gmail — read, search, send, draft
|   |-- calendar.py                Google Calendar — events, free slots
|   |-- google_drive.py            Google Drive — list, read, upload, share
|   |-- onedrive.py                Microsoft OneDrive — Device Code Flow
|   |-- linkedin.py                LinkedIn — profile, posts, analytics
|   |-- whatsapp.py                WhatsApp bridge REST client
|   |-- zomato.py                  Zomato MCP client (port 8765)
|   |-- youtube.py                 YouTube Data API v3
|   |-- web.py                     DuckDuckGo search + BeautifulSoup scraping
|   +-- computer.py                Files, clipboard, shell commands, screenshots
|
|-- ui/
|   |-- websocket.py               WebSocket handler — streams agent events
|   +-- static/
|       |-- index.html             Main chat UI — arc reactor theme
|       |-- style.css              All UI styles
|       |-- app.js                 Chat, streaming, session management
|       |-- face-login.js          Boot animation + face-scan overlay
|       |-- voice-mode.js          Web Speech API controller
|       |-- gestures.js            Touch and gesture support
|       |-- sw.js                  Service worker (PWA offline shell)
|       +-- manifest.webmanifest   PWA manifest
|
|-- whatsapp_bridge/
|   |-- server.js                  Express + whatsapp-web.js REST API
|   +-- package.json
|
|-- deploy/
|   |-- start_jarvis_silent.ps1    Boot launcher + 60-second watchdog loop
|   |-- setup_autostart.bat        Install or remove Windows Startup shortcut
|   |-- _install_shortcut.ps1      Helper: creates the .lnk file
|   +-- manage_secrets.py          Migrate .env keys to Windows Credential Vault
|
|-- memory/                        Created at runtime — not committed to git
|   |-- jarvis.db                  SQLite — conversations, preferences, tasks
|   |-- google_credentials.json    Google OAuth tokens (auto-refreshed)
|   |-- google_client_secret.json  Your Google OAuth app credentials
|   +-- microsoft_credentials.json OneDrive Device Code tokens
|
+-- logs/                          Created at runtime — not committed to git
    |-- jarvis.log                 Rotating application log (10 MB, 5 backups)
    +-- autostart.log              Boot watchdog events
```

---

## Extending Jarvis

Adding a new integration takes four steps.

### 1. Create the integration module

```python
# integrations/myservice.py

import httpx

class MyServiceClient:
    async def do_something(self, param: str) -> dict:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://api.myservice.com/endpoint",
                params={"q": param},
            )
            return resp.json()

my_service = MyServiceClient()
```

### 2. Register the tool

Add an entry to the `TOOLS` list in `core/tool_registry.py`:

```python
{
    "name": "myservice_do_something",
    "description": "Does something useful with My Service. Use when the user asks about X.",
    "input_schema": {
        "type": "object",
        "properties": {
            "param": {
                "type": "string",
                "description": "The input parameter",
            },
        },
        "required": ["param"],
    },
},
```

### 3. Route the tool call

Add a handler in `core/tool_router.py`:

```python
elif tool_name == "myservice_do_something":
    from integrations.myservice import my_service
    result = await my_service.do_something(tool_input["param"])
    return result
```

### 4. Update the system prompt (optional but recommended)

Add a line to `core/system_prompt.py` so the LLM knows when to reach for the new tool:

```python
"- My Service: myservice_do_something — use when the user asks about X"
```

That is all. The agent loop, streaming, error handling, and retry logic are already in place. The tool is available on the next restart with no other changes.

---

## Privacy

Jarvis is a local-first system. There is no telemetry, no usage reporting, and no external relay of any kind.

- **All processing runs on your machine.** The agent loop, memory, scheduling, tool routing, and UI server all run locally.
- **LLM calls are direct.** Your messages go from your machine directly to your chosen provider (Groq, Anthropic, or Ollama at localhost). No proxy, no middleman.
- **Credentials are encrypted.** Google and Microsoft OAuth tokens are stored in `memory/` as JSON files protected by filesystem permissions. API keys can be migrated to the Windows Credential Vault (AES-256 encrypted, accessible only under your Windows login session).
- **The server binds to `127.0.0.1` by default.** No other machine on your network can reach Jarvis unless you deliberately configure Tailscale or change the HOST setting.
- **The WhatsApp bridge runs locally.** `whatsapp-web.js` automates a local Chromium instance. No third-party WhatsApp gateway, relay server, or cloud service is involved.
- **No analytics, no crash reporting, no external pings.** The only outbound network traffic is LLM API calls and the integration endpoints you explicitly enable (Google, Microsoft, Groq, etc.).

---

## License

MIT License

Copyright (c) 2025

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
