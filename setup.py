"""
Jarvis first-run setup server.
Runs a lightweight HTTP server that collects the API key,
validates it, writes .env, then launches main Jarvis.
"""

import asyncio
import io
import json
import os
import subprocess
import sys
import webbrowser
from pathlib import Path

# Force UTF-8 on Windows
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).parent
ENV_FILE = BASE_DIR / ".env"

SETUP_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>J.A.R.V.I.S — First Run Setup</title>
<style>
:root{--bg:#07091a;--card:#0d1123;--accent:#00d4ff;--text:#e8f4f8;--muted:#7a9ab0;--border:rgba(0,212,255,0.15);--danger:#ff4444;--success:#00ff88;}
*{box-sizing:border-box;margin:0;padding:0;}
body{background:var(--bg);color:var(--text);font-family:'Inter',system-ui,sans-serif;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:20px;background-image:linear-gradient(rgba(0,212,255,0.015) 1px,transparent 1px),linear-gradient(90deg,rgba(0,212,255,0.015) 1px,transparent 1px);background-size:40px 40px;}
.card{background:var(--card);border:1px solid var(--border);border-radius:20px;padding:48px;max-width:520px;width:100%;box-shadow:0 20px 60px rgba(0,0,0,0.5),0 0 80px rgba(0,212,255,0.05);}
.arc{position:relative;width:64px;height:64px;margin:0 auto 28px;}
.arc-ring{position:absolute;inset:0;border-radius:50%;border:2px solid var(--accent);opacity:.5;animation:spin 4s linear infinite;}
.arc-ring.r2{inset:10px;animation-duration:6s;animation-direction:reverse;opacity:.3;}
.arc-core{position:absolute;inset:28%;border-radius:50%;background:radial-gradient(circle,var(--accent),rgba(0,212,255,.3),transparent);box-shadow:0 0 16px var(--accent);animation:pulse 2s ease-in-out infinite alternate;}
@keyframes spin{to{transform:rotate(360deg)}}
@keyframes pulse{from{opacity:.6}to{opacity:1}}
h1{text-align:center;font-size:22px;font-weight:700;letter-spacing:.2em;color:var(--accent);text-shadow:0 0 20px rgba(0,212,255,.4);margin-bottom:6px;}
.subtitle{text-align:center;font-size:12px;letter-spacing:.12em;color:var(--muted);margin-bottom:32px;}
.section-title{font-size:11px;font-weight:600;letter-spacing:.14em;color:var(--muted);margin-bottom:10px;}
.step{display:flex;align-items:flex-start;gap:14px;margin-bottom:20px;padding:14px 16px;background:rgba(0,212,255,.04);border:1px solid var(--border);border-radius:10px;}
.step-num{width:26px;height:26px;border-radius:50%;background:var(--accent);color:#07091a;font-weight:700;font-size:12px;display:flex;align-items:center;justify-content:center;flex-shrink:0;}
.step-text{font-size:13px;line-height:1.6;color:#c8dde8;}
.step-text a{color:var(--accent);text-decoration:none;font-weight:500;}
.step-text a:hover{text-decoration:underline;}
.input-wrap{margin-bottom:20px;}
.key-input{width:100%;background:rgba(0,0,0,.3);border:1px solid var(--border);border-radius:10px;padding:13px 16px;color:var(--text);font-size:13.5px;font-family:'Consolas',monospace;transition:border-color .2s,box-shadow .2s;outline:none;}
.key-input:focus{border-color:var(--accent);box-shadow:0 0 0 3px rgba(0,212,255,.12);}
.key-input::placeholder{color:var(--muted);}
.btn{width:100%;padding:14px;background:var(--accent);color:#07091a;font-weight:700;font-size:14px;letter-spacing:.06em;border:none;border-radius:10px;cursor:pointer;transition:all .2s;display:flex;align-items:center;justify-content:center;gap:8px;}
.btn:hover:not(:disabled){background:#22e5ff;box-shadow:0 0 24px rgba(0,212,255,.4);}
.btn:disabled{opacity:.5;cursor:not-allowed;}
.status{margin-top:16px;padding:12px 16px;border-radius:8px;font-size:13px;display:none;}
.status.ok{background:rgba(0,255,136,.08);border:1px solid rgba(0,255,136,.25);color:var(--success);display:block;}
.status.err{background:rgba(255,68,68,.08);border:1px solid rgba(255,68,68,.25);color:var(--danger);display:block;}
.status.info{background:rgba(0,212,255,.08);border:1px solid var(--border);color:var(--accent);display:block;}
.spinner{width:18px;height:18px;border:2.5px solid rgba(7,9,26,.3);border-top-color:#07091a;border-radius:50%;animation:spin .7s linear infinite;}
</style>
</head>
<body>
<div class="card">
  <div class="arc">
    <div class="arc-ring"></div>
    <div class="arc-ring r2"></div>
    <div class="arc-core"></div>
  </div>
  <h1>J.A.R.V.I.S</h1>
  <div class="subtitle">FIRST RUN SETUP</div>

  <div class="section-title">WHAT YOU NEED</div>
  <div class="step">
    <div class="step-num">1</div>
    <div class="step-text">
      Go to <a href="https://console.anthropic.com/settings/keys" target="_blank">console.anthropic.com/settings/keys</a>
      and create a new API key (free account works).
    </div>
  </div>
  <div class="step">
    <div class="step-num">2</div>
    <div class="step-text">Paste your API key below. It starts with <code style="color:var(--accent);background:rgba(0,212,255,.1);padding:1px 5px;border-radius:3px">sk-ant-api03-</code></div>
  </div>
  <div class="step">
    <div class="step-num">3</div>
    <div class="step-text">Click Launch — Jarvis does the rest automatically.</div>
  </div>

  <div class="input-wrap">
    <input class="key-input" id="apiKey" type="password" placeholder="sk-ant-api03-..." autocomplete="off" spellcheck="false"/>
  </div>
  <button class="btn" id="launchBtn" onclick="launch()">
    <span id="btnText">Validate &amp; Launch Jarvis</span>
    <span class="spinner" id="btnSpinner" style="display:none"></span>
  </button>
  <div class="status" id="status"></div>
</div>

<script>
document.getElementById('apiKey').addEventListener('keydown', e => {
  if (e.key === 'Enter') launch();
});

async function launch() {
  const key = document.getElementById('apiKey').value.trim();
  const btn = document.getElementById('launchBtn');
  const spinner = document.getElementById('btnSpinner');
  const btnText = document.getElementById('btnText');
  const status = document.getElementById('status');

  if (!key) { showStatus('err', 'Please enter your API key.'); return; }
  if (!key.startsWith('sk-ant-')) { showStatus('err', 'API key should start with sk-ant-'); return; }

  btn.disabled = true;
  btnText.textContent = 'Validating...';
  spinner.style.display = 'block';
  showStatus('info', 'Validating API key with Anthropic...');

  try {
    const res = await fetch('/setup/validate', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({api_key: key})
    });
    const data = await res.json();

    if (data.valid) {
      showStatus('ok', 'API key validated! Writing .env and launching Jarvis...');
      btnText.textContent = 'Launching...';

      await fetch('/setup/save', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({api_key: key})
      });

      showStatus('ok', 'Done! Jarvis is starting... Redirecting in 3 seconds.');
      setTimeout(() => { window.location.href = 'http://localhost:8000'; }, 3000);
    } else {
      showStatus('err', data.error || 'Invalid API key. Check and try again.');
      btn.disabled = false;
      btnText.textContent = 'Validate & Launch Jarvis';
      spinner.style.display = 'none';
    }
  } catch (e) {
    showStatus('err', 'Connection error: ' + e.message);
    btn.disabled = false;
    btnText.textContent = 'Validate & Launch Jarvis';
    spinner.style.display = 'none';
  }
}

function showStatus(type, msg) {
  const el = document.getElementById('status');
  el.className = 'status ' + type;
  el.textContent = msg;
}
</script>
</body>
</html>"""


async def handle_request(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """Handle a single HTTP request."""
    try:
        raw = await asyncio.wait_for(reader.read(8192), timeout=5.0)
        request = raw.decode("utf-8", errors="replace")
        lines = request.split("\r\n")
        if not lines:
            return

        method, path_raw, *_ = lines[0].split(" ", 2) + ["", ""]
        path = path_raw.split("?")[0]

        body = ""
        if "\r\n\r\n" in request:
            body = request.split("\r\n\r\n", 1)[1]

        if path == "/" or path == "/setup":
            send_response(writer, 200, "text/html", SETUP_HTML)

        elif path == "/setup/validate" and method == "POST":
            try:
                data = json.loads(body)
                api_key = data.get("api_key", "").strip()
                valid, error = await validate_api_key(api_key)
                send_json(writer, {"valid": valid, "error": error})
            except Exception as e:
                send_json(writer, {"valid": False, "error": str(e)})

        elif path == "/setup/save" and method == "POST":
            try:
                data = json.loads(body)
                api_key = data.get("api_key", "").strip()
                write_env(api_key)
                send_json(writer, {"ok": True})
                # Launch main Jarvis after a short delay
                asyncio.create_task(launch_jarvis())
            except Exception as e:
                send_json(writer, {"ok": False, "error": str(e)})

        else:
            send_response(writer, 404, "text/plain", "Not found")

    except Exception:
        pass
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


def send_response(writer, status: int, content_type: str, body: str) -> None:
    encoded = body.encode("utf-8")
    resp = (
        f"HTTP/1.1 {status} OK\r\n"
        f"Content-Type: {content_type}; charset=utf-8\r\n"
        f"Content-Length: {len(encoded)}\r\n"
        f"Connection: close\r\n\r\n"
    ).encode() + encoded
    writer.write(resp)


def send_json(writer, data: dict) -> None:
    send_response(writer, 200, "application/json", json.dumps(data))


async def validate_api_key(api_key: str) -> tuple[bool, str]:
    """Validate the API key by making a minimal Anthropic API call."""
    if not api_key.startswith("sk-ant-"):
        return False, "Key must start with sk-ant-"
    import urllib.request, urllib.error
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps({
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 5,
            "messages": [{"role": "user", "content": "hi"}],
        }).encode(),
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
            return True, ""
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        if e.code == 401:
            return False, "Invalid API key — authentication failed."
        if e.code == 429:
            return True, ""  # Rate limited but key is valid
        return False, f"API error {e.code}: {body[:100]}"
    except Exception as e:
        return False, f"Connection error: {e}"


def write_env(api_key: str) -> None:
    """Write the API key to .env, preserving existing values."""
    if ENV_FILE.exists():
        lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
        new_lines = []
        found = False
        for line in lines:
            if line.startswith("ANTHROPIC_API_KEY="):
                new_lines.append(f"ANTHROPIC_API_KEY={api_key}")
                found = True
            else:
                new_lines.append(line)
        if not found:
            new_lines.insert(0, f"ANTHROPIC_API_KEY={api_key}")
        ENV_FILE.write_text("\n".join(new_lines), encoding="utf-8")
    else:
        example = BASE_DIR / ".env.example"
        if example.exists():
            import shutil
            shutil.copy(example, ENV_FILE)
        text = ENV_FILE.read_text(encoding="utf-8") if ENV_FILE.exists() else ""
        if "ANTHROPIC_API_KEY=" in text:
            lines = text.splitlines()
            new_lines = [f"ANTHROPIC_API_KEY={api_key}" if l.startswith("ANTHROPIC_API_KEY=") else l for l in lines]
            ENV_FILE.write_text("\n".join(new_lines), encoding="utf-8")
        else:
            ENV_FILE.write_text(f"ANTHROPIC_API_KEY={api_key}\n", encoding="utf-8")
    print(f"API key written to .env")


async def launch_jarvis() -> None:
    """Launch the main Jarvis process after setup completes."""
    await asyncio.sleep(2)
    venv_python = BASE_DIR / ".venv" / "Scripts" / "python.exe"
    py = str(venv_python) if venv_python.exists() else sys.executable
    print("Launching Jarvis main server...")
    subprocess.Popen([py, str(BASE_DIR / "main.py")], cwd=str(BASE_DIR))
    # Stop the setup server
    asyncio.get_event_loop().stop()


async def main() -> None:
    port = 8000
    server = await asyncio.start_server(handle_request, "127.0.0.1", port)
    print(f"\n  J.A.R.V.I.S Setup → http://localhost:{port}")
    print("  Opening browser...\n")
    await asyncio.sleep(0.5)
    webbrowser.open(f"http://localhost:{port}")
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
