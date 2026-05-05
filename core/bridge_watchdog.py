"""
WhatsApp Bridge Watchdog
========================
Periodically checks the WA bridge's deep-health endpoint (/health) which calls
client.getState() under the hood. If the bridge reports unhealthy on consecutive
checks, the watchdog kills the stale Node process and respawns it. The saved
WhatsApp session is reused (no QR re-scan needed).

Why we need this: the WA Web Chromium tab inside the bridge can silently drop
its connection without firing a 'disconnected' event. /status keeps reporting
"ready" while messages stop arriving — what users see as "Jarvis stopped
responding on WhatsApp". This watchdog detects + auto-recovers from that.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

BASE_DIR     = Path(__file__).resolve().parent.parent
BRIDGE_DIR   = BASE_DIR / "whatsapp_bridge"
BRIDGE_URL   = "http://127.0.0.1:3001"
CHECK_INTERVAL_SEC   = 300       # 5 minutes between checks
GRACE_AFTER_RESTART  = 90        # don't re-check for 90s after a restart
UNHEALTHY_THRESHOLD  = 2         # consecutive bad checks before restart
RESTART_COOLDOWN_SEC = 600       # don't restart more often than every 10 min
HEALTH_TIMEOUT_SEC   = 6.0


class BridgeWatchdog:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._consec_bad: int = 0
        self._last_restart_ts: float = 0.0
        self._stop_event = asyncio.Event()

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run())
        logger.info("✓  WA bridge watchdog started (check every %ds)", CHECK_INTERVAL_SEC)

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass

    async def _run(self) -> None:
        # Initial grace period — let the bridge finish booting before first check
        await asyncio.sleep(GRACE_AFTER_RESTART)
        while not self._stop_event.is_set():
            try:
                healthy = await self._check_once()
                if healthy:
                    self._consec_bad = 0
                else:
                    self._consec_bad += 1
                    logger.warning("[watchdog] bridge unhealthy (%d/%d)",
                                   self._consec_bad, UNHEALTHY_THRESHOLD)
                    if self._consec_bad >= UNHEALTHY_THRESHOLD:
                        if (time.time() - self._last_restart_ts) >= RESTART_COOLDOWN_SEC:
                            await self._restart_bridge()
                            self._consec_bad = 0
                            self._last_restart_ts = time.time()
                            await asyncio.sleep(GRACE_AFTER_RESTART)
                        else:
                            logger.info("[watchdog] in cooldown — skipping restart this round")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("[watchdog] iteration error (continuing): %s", e)
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=CHECK_INTERVAL_SEC)
            except asyncio.TimeoutError:
                pass

    async def _check_once(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=HEALTH_TIMEOUT_SEC) as client:
                r = await client.get(f"{BRIDGE_URL}/health")
                if r.status_code != 200:
                    return False
                data = r.json()
                return bool(data.get("healthy"))
        except Exception:
            return False

    async def _restart_bridge(self) -> None:
        logger.warning("[watchdog] restarting bridge — saved session reused, no QR")
        # 1. Kill any process holding port 3001 (the bridge)
        try:
            self._kill_bridge_process()
        except Exception as e:
            logger.warning("[watchdog] kill failed (continuing): %s", e)
        # 2. Brief pause to let the OS release the port
        await asyncio.sleep(2)
        # 3. Spawn a fresh detached Node process running the bridge
        try:
            self._spawn_bridge_process()
            logger.info("[watchdog] new bridge process spawned")
        except Exception as e:
            logger.error("[watchdog] respawn FAILED: %s", e)

    @staticmethod
    def _kill_bridge_process() -> None:
        """Find the Node process listening on 3001 and terminate it."""
        if sys.platform != "win32":
            # Generic Unix path (not used here but safe to keep)
            subprocess.run(["pkill", "-f", "whatsapp_bridge/server.js"], check=False)
            return
        # Windows: find PID via netstat, then taskkill
        try:
            out = subprocess.check_output(
                ["netstat", "-ano", "-p", "tcp"], text=True, timeout=5
            )
        except Exception:
            return
        target_pid = None
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 5 and ":3001" in parts[1] and parts[3].upper() == "LISTENING":
                try:
                    target_pid = int(parts[4])
                    break
                except ValueError:
                    continue
        if target_pid:
            subprocess.run(
                ["taskkill", "/PID", str(target_pid), "/F", "/T"],
                check=False, capture_output=True, timeout=5
            )

    @staticmethod
    def _spawn_bridge_process() -> None:
        """Start a detached Node process running the WA bridge."""
        node_exe = "node.exe" if sys.platform == "win32" else "node"
        log_path = BASE_DIR / "wa_bridge.log"
        err_path = BASE_DIR / "wa_bridge.log.err"
        # Append to existing log files so history is preserved
        stdout = open(log_path, "ab")
        stderr = open(err_path, "ab")
        kwargs: dict = {
            "cwd":    str(BRIDGE_DIR),
            "stdout": stdout,
            "stderr": stderr,
            "stdin":  subprocess.DEVNULL,
        }
        if sys.platform == "win32":
            # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP — survive parent exit, no console window
            kwargs["creationflags"] = 0x00000008 | 0x00000200
        else:
            kwargs["start_new_session"] = True
        subprocess.Popen([node_exe, "server.js"], **kwargs)


# Module-level singleton — main.py imports this
watchdog = BridgeWatchdog()
