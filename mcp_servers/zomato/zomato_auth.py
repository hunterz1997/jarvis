#!/usr/bin/env python3
"""
Zomato MCP — authentication layer.

Owns all credential lifecycle:
  - Token storage in Windows Credential Manager (DPAPI via keyring)
  - Device-identity headers (stable Android device fingerprint)
  - Three-rung 401-retry ladder used by every API call
"""

import asyncio
import json
import logging
import os
import uuid
from pathlib import Path
from typing import Optional

import keyring
from dotenv import load_dotenv

_HERE = Path(__file__).parent
load_dotenv(_HERE / ".env")

log = logging.getLogger(__name__)

# ── Keyring namespace ─────────────────────────────────────────────────────────
KEYRING_SERVICE = "zomato_mcp"
KEYRING_KEY     = "access_token"

# ── Token helpers ─────────────────────────────────────────────────────────────

def get_token() -> Optional[str]:
    """Return the active X-Zomato-Access-Token from the best available source."""
    # Priority 1: Windows Credential Manager (DPAPI-encrypted)
    try:
        tok = keyring.get_password(KEYRING_SERVICE, KEYRING_KEY)
        if tok:
            return tok.strip()
    except Exception as exc:
        log.warning("keyring read failed: %s", exc)

    # Priority 2: explicit env override (first-run or CI)
    env_tok = os.getenv("ZOMATO_ACCESS_TOKEN", "").strip()
    if env_tok:
        return env_tok

    return None


def store_token(token: str) -> None:
    """Persist token to Windows Credential Manager."""
    token = token.strip()
    try:
        keyring.set_password(KEYRING_SERVICE, KEYRING_KEY, token)
        log.info("token stored in Windows Credential Manager")
    except Exception as exc:
        log.error("keyring write failed: %s", exc)
        raise


def clear_token() -> None:
    """Wipe token from Credential Manager."""
    try:
        keyring.delete_password(KEYRING_SERVICE, KEYRING_KEY)
        log.info("token cleared from Windows Credential Manager")
    except keyring.errors.PasswordDeleteError:
        pass
    except Exception as exc:
        log.warning("keyring delete failed: %s", exc)


# ── Cookie-jar helpers (web-session authentication) ───────────────────────────
#
# The Zomato `zat` cookie is a WEB SESSION token for www.zomato.com.
# It must be sent as a Cookie header (not as X-Zomato-Access-Token) to
# www.zomato.com/webroutes/* endpoints.  The mobile gateway
# (api.zomato.com/gw/*) requires a different OAuth token obtained through
# the Android app flow — we do not use that gateway.
#
# After setup.py runs, the full Zomato cookie jar is stored here so that
# all web-session requests can be constructed without a browser launch.

COOKIE_KEY = "cookies_json"


def store_cookies(cookies: list) -> None:
    """
    Persist Zomato auth cookies to Windows Credential Manager.

    Windows Credential Manager caps the credential value at ~2 KB.  We avoid
    the limit by keeping only the cookies that are actually needed for web-
    session authentication: zat, PHPSESSID, csrf, and a short allowlist.
    The remaining 30+ tracking/analytics cookies are discarded.
    """
    ESSENTIAL_NAMES = {
        "zat", "phpsessid", "csrf", "cid", "fbcity", "rd", "zl",
        "zrt_3a5c99f7e944",  # device/session continuity token (name may vary)
    }
    # Keep cookies whose lowercased name matches the allowlist, OR any cookie
    # whose name starts with "zrt_" (Zomato's rotating token pattern).
    essential = [
        c for c in cookies
        if c.get("name", "").lower() in ESSENTIAL_NAMES
        or c.get("name", "").lower().startswith("zrt_")
    ]
    if not essential:
        # Fallback: take the first 8 cookies (won't happen if setup ran correctly)
        essential = cookies[:8]
    payload = json.dumps(essential)
    try:
        keyring.set_password(KEYRING_SERVICE, COOKIE_KEY, payload)
        log.info("cookie jar stored (%d essential cookies, %d total)", len(essential), len(cookies))
    except Exception as exc:
        log.error("cookie jar keyring write failed: %s", exc)
        raise  # propagate so callers know cookies were not persisted


def get_cookies() -> list:
    """Retrieve the stored Playwright Zomato cookie list (returns [] on miss)."""
    try:
        raw = keyring.get_password(KEYRING_SERVICE, COOKIE_KEY)
        if raw:
            return json.loads(raw)
    except Exception as exc:
        log.warning("cookie jar keyring read failed: %s", exc)
    return []


def get_web_cookie_header() -> str:
    """
    Return a Cookie header string from the stored Zomato cookies.

    Falls back to constructing `zat=<token>` from the keyring token if the
    full cookie jar has not been stored yet (e.g. first run before re-setup).
    """
    cookies = get_cookies()
    if cookies:
        return "; ".join(f"{c['name']}={c['value']}" for c in cookies)
    # Minimal fallback: just the zat token
    tok = get_token()
    return f"zat={tok}" if tok else ""


def get_csrf_token() -> str:
    """Extract the CSRF token from the stored Zomato cookie jar."""
    for c in get_cookies():
        if c["name"].lower() == "csrf":
            return c["value"]
    return ""


# ── Device-identity headers ───────────────────────────────────────────────────

def _get_android_id() -> str:
    """Return a stable UUID that mimics an Android device ID.

    Generated once and stored in .env as ZOMATO_ANDROID_ID.
    Using a random UUID per-run would trigger Zomato's device-mismatch detection.
    """
    env_id = os.getenv("ZOMATO_ANDROID_ID", "").strip()
    if env_id:
        return env_id
    new_id = str(uuid.uuid4())
    log.warning("ZOMATO_ANDROID_ID not set — using ephemeral id %s (run zomato_setup.py to persist)", new_id)
    return new_id


def get_device_headers() -> dict:
    """Return the static Android device header block for every API request."""
    return {
        "X-Client-Id":          "zomato_android_v2",
        "X-Android-Id":         _get_android_id(),
        "X-Zomato-App-Version": "931",
        "User-Agent":           "OkHttp/4.12.0",
    }


def get_authed_headers(token: Optional[str] = None) -> dict:
    """Return device headers plus the access token header."""
    tok = token or get_token()
    headers = get_device_headers()
    if tok:
        headers["X-Zomato-Access-Token"] = tok
    return headers


# ── Three-rung 401-retry ladder ───────────────────────────────────────────────

class ZomatoAuthRequired(Exception):
    """Raised when all three retry rungs fail — caller must prompt for OTP login."""


async def with_auth_retry(coro_factory, *, max_rungs: int = 3):
    """
    Call coro_factory() up to three times, escalating auth recovery on each 401.

    coro_factory must be a zero-argument async callable that performs one API
    request and either returns the result or raises an exception whose string
    representation contains '401' or '403'.

    Rungs:
      0 — Re-read keyring (another process may have refreshed the token).
      1 — Silent Playwright browser probe to extract a fresh token.
      2 — Raise ZomatoAuthRequired (surface to Claude for OTP flow).
    """
    last_exc = None
    for rung in range(max_rungs):
        try:
            return await coro_factory()
        except Exception as exc:
            err = str(exc)
            if "401" not in err and "403" not in err:
                raise  # non-auth error — propagate immediately

            last_exc = exc
            log.warning("auth failure on rung %d: %s", rung, exc)

            if rung == 0:
                # Rung 0: re-read keyring (zero network cost)
                log.info("rung 0: re-reading keyring for fresh token")
                # Nothing to do explicitly — the next iteration of coro_factory
                # will call get_token() which always reads fresh from keyring.
                await asyncio.sleep(0.1)

            elif rung == 1:
                # Rung 1: silent browser session to recover token
                log.info("rung 1: launching headless browser to recover token")
                try:
                    from zomato_browser import browser_extract_token  # late import to avoid circular
                    new_token = await browser_extract_token()
                    if new_token:
                        store_token(new_token)
                        log.info("rung 1: token recovered from browser session")
                    else:
                        log.warning("rung 1: browser could not extract token")
                except Exception as browser_exc:
                    log.warning("rung 1: browser extraction failed: %s", browser_exc)

    raise ZomatoAuthRequired(
        "Authentication required. Please call zomato_login_start() with your phone number."
    ) from last_exc
