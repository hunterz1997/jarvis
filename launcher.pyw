"""
J.A.R.V.I.S Desktop Launcher
────────────────────────────
Double-click this file (or the desktop shortcut) to start everything:
  • WhatsApp Bridge  (node server.js — background, no window)
  • Jarvis Server    (.venv Python — background, no window)
  • Opens browser   → http://localhost:8000

A tray icon appears in the system tray with Open / Restart / Quit options.
"""

import os
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────
BASE_DIR   = Path(__file__).parent.resolve()
VENV_PY    = BASE_DIR / ".venv" / "Scripts" / "pythonw.exe"
if not VENV_PY.exists():
    VENV_PY = BASE_DIR / ".venv" / "Scripts" / "python.exe"
WA_DIR     = BASE_DIR / "whatsapp_bridge"
JARVIS_URL = "http://localhost:8000"
WA_URL     = "http://localhost:3001/status"

# Suppress console on Windows for any child spawns
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

# ── Globals ────────────────────────────────────────────────────
_processes: dict[str, subprocess.Popen] = {}
_tray_icon = None


# ── Port check ────────────────────────────────────────────────
def _port_in_use(port: int) -> bool:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


# ── Process launchers ─────────────────────────────────────────
def _start_whatsapp():
    """Start the WhatsApp bridge node server if not already running."""
    if _port_in_use(3001):
        return  # already up
    proc = subprocess.Popen(
        ["node", "server.js"],
        cwd=str(WA_DIR),
        creationflags=_NO_WINDOW,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    _processes["whatsapp"] = proc


def _start_jarvis():
    """Start the Jarvis FastAPI server if not already running."""
    if _port_in_use(8000):
        return  # already up
    proc = subprocess.Popen(
        [str(VENV_PY), "main.py"],
        cwd=str(BASE_DIR),
        creationflags=_NO_WINDOW,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    _processes["jarvis"] = proc


def _open_as_app():
    """Open Jarvis as a standalone desktop window (no browser chrome)."""
    # Try Edge app mode first (pre-installed on Windows 11)
    edge_paths = [
        Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
    ]
    chrome_paths = [
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
    ]
    # --user-data-dir forces a separate browser process even when Chrome/Edge is
    # already open — without it the URL gets passed to the existing instance as a
    # regular tab instead of an app window.
    profile_dir = str(BASE_DIR / "browser_profile")
    app_args = [
        f"--app={JARVIS_URL}",
        "--window-size=1400,900",
        "--window-position=100,50",
        f"--user-data-dir={profile_dir}",
    ]
    # Prefer Chrome over Edge so all links from inside Jarvis open in Chrome
    for browser in chrome_paths + edge_paths:
        if browser.exists():
            subprocess.Popen(
                [str(browser)] + app_args,
                creationflags=_NO_WINDOW,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return
    # Fallback: regular browser
    webbrowser.open(JARVIS_URL)


def _open_browser_when_ready():
    """Poll until Jarvis is up, then open as desktop app."""
    for _ in range(60):          # wait up to 60 seconds
        if _port_in_use(8000):
            _open_as_app()
            return
        time.sleep(1)


def start_all():
    """Start everything in the right order."""
    _start_whatsapp()
    _start_jarvis()
    threading.Thread(target=_open_browser_when_ready, daemon=True).start()


def stop_all():
    """Terminate both services."""
    for name, proc in _processes.items():
        try:
            proc.terminate()
        except Exception:
            pass
    _processes.clear()


def restart_all():
    """Stop everything then start again."""
    stop_all()
    time.sleep(2)
    start_all()


# ── Tray icon ─────────────────────────────────────────────────
def _build_icon_image():
    """Draw a blue arc-reactor icon with Pillow."""
    from PIL import Image, ImageDraw
    sz = 64
    img = Image.new("RGBA", (sz, sz), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    cx = cy = sz // 2

    # Dark background circle
    d.ellipse([1, 1, sz - 2, sz - 2], fill=(10, 15, 30, 230))

    # Outer glow ring
    d.ellipse([3, 3, sz - 4, sz - 4], outline=(0, 140, 255), width=3)

    # Middle ring
    margin = 12
    d.ellipse([margin, margin, sz - margin, sz - margin],
              outline=(0, 200, 255), width=2)

    # Inner glowing core
    core = 18
    d.ellipse([core, core, sz - core, sz - core], fill=(0, 180, 255, 220))

    # Bright centre dot
    dot = 26
    d.ellipse([dot, dot, sz - dot, sz - dot], fill=(200, 235, 255, 255))

    # Hex spoke lines (6 spokes)
    import math
    spoke_outer = sz // 2 - 6
    spoke_inner = sz // 2 - 16
    for angle_deg in range(0, 360, 60):
        angle = math.radians(angle_deg)
        x0 = cx + spoke_inner * math.cos(angle)
        y0 = cy + spoke_inner * math.sin(angle)
        x1 = cx + spoke_outer * math.cos(angle)
        y1 = cy + spoke_outer * math.sin(angle)
        d.line([x0, y0, x1, y1], fill=(0, 210, 255), width=1)

    return img


def _build_tray():
    """Create and run the pystray system-tray icon (blocks until quit)."""
    import pystray

    icon_img = _build_icon_image()

    def on_open(icon, _item):
        _open_as_app()

    def on_restart(icon, _item):
        icon.notify("Restarting Jarvis…", "J.A.R.V.I.S")
        threading.Thread(target=restart_all, daemon=True).start()

    def on_quit(icon, _item):
        icon.visible = False
        stop_all()
        icon.stop()
        sys.exit(0)

    menu = pystray.Menu(
        pystray.MenuItem("Open Jarvis", on_open, default=True),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Restart", on_restart),
        pystray.MenuItem("Quit", on_quit),
    )

    global _tray_icon
    _tray_icon = pystray.Icon(
        "JARVIS",
        icon_img,
        "J.A.R.V.I.S — Running",
        menu,
    )
    _tray_icon.run()  # blocks here until quit


# ── Entry point ───────────────────────────────────────────────
if __name__ == "__main__":
    # Prevent duplicate instances
    if _port_in_use(8000):
        # Jarvis already running — open as app window (no browser chrome) and exit
        _open_as_app()
        sys.exit(0)

    start_all()

    try:
        _build_tray()
    except Exception:
        # pystray failed (e.g. no display) — fallback: just wait
        try:
            _processes.get("jarvis", subprocess.Popen(["echo"])).wait()
        except KeyboardInterrupt:
            stop_all()
