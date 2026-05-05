#!/usr/bin/env python3
"""
Zomato MCP — one-time interactive setup.

Run this ONCE in a terminal (not from Claude Desktop) after cloning the project:

    python zomato_setup.py

What it does:
  1. Generates a stable ANDROID_ID UUID and writes it to .env
  2. Opens a headed Chromium window at accounts.zomato.com
  3. You log in with your phone number + OTP (manual, in the browser)
  4. On successful login it extracts your X-Zomato-Access-Token from cookies
     (or network request interception as fallback)
  5. Stores the token in Windows Credential Manager (DPAPI-encrypted)
  6. Writes ZOMATO_PHONE, ZOMATO_DEFAULT_LAT/LON/CITY_ID to .env
  7. Prints next steps

Prerequisites:
    pip install -r requirements.txt
    playwright install chromium
"""

import asyncio
import os
import sys
import uuid
from pathlib import Path
from typing import Optional

_HERE       = Path(__file__).parent
PROFILE_DIR = _HERE / "zomato_chromium_profile"
ENV_FILE    = _HERE / ".env"

# Bootstrap .env before importing our own modules
from dotenv import load_dotenv, set_key
load_dotenv(ENV_FILE)

from zomato_auth import store_token, store_cookies


# ── .env helpers ──────────────────────────────────────────────────────────────

def _ensure_env_key(key: str, value: str) -> None:
    set_key(str(ENV_FILE), key, value, quote_mode="never")


def _ensure_android_id() -> str:
    existing = os.getenv("ZOMATO_ANDROID_ID", "").strip()
    if existing:
        print(f"  ANDROID_ID already set: {existing}")
        return existing
    new_id = str(uuid.uuid4())
    _ensure_env_key("ZOMATO_ANDROID_ID", new_id)
    print(f"  Generated ANDROID_ID: {new_id}")
    return new_id


# ── Token extraction helpers ──────────────────────────────────────────────────

def _find_token_in_cookies(cookies: list) -> Optional[str]:
    """
    Search for the Zomato access token in a Playwright cookie list.

    IMPORTANT: Filter to .zomato.com domain FIRST.  The persistent Chromium
    profile may contain cookies from other logged-in sites (e.g. YouTube)
    whose names also contain 'token'.  Without the domain filter the script
    would grab YouTube's __Secure-ROLLOUT_TOKEN instead of Zomato's zat.

    Priority order (confirmed from Zomato web traffic):
      1. zat           — the primary Zomato session token (web + API cookie)
      2. x-access-token / zomato_access_token / _zat  — alternative names
      3. Any .zomato.com cookie with 'access' in the name (not 'csrf')
    """
    # ── Step 1: Scope to Zomato domains only ─────────────────────────────────
    zomato_cookies = [
        c for c in cookies
        if "zomato.com" in c.get("domain", "")
    ]

    # ── Step 2: Exact priority names ─────────────────────────────────────────
    priority = {"zat", "x-access-token", "zomato_access_token", "_zat"}
    for c in zomato_cookies:
        if c["name"].lower() in priority:
            return c["value"]

    # ── Step 3: Broader fallback (access in name, not csrf) ──────────────────
    for c in zomato_cookies:
        name = c["name"].lower()
        if "access" in name or ("token" in name and "csrf" not in name):
            return c["value"]

    return None


async def _extract_token_via_network(page) -> Optional[str]:
    """Intercept the first authenticated request and read the header value."""
    token_holder: list[str] = []

    def on_request(request):
        if not token_holder:
            tok = request.headers.get("x-zomato-access-token", "")
            if tok:
                token_holder.append(tok)

    page.on("request", on_request)
    # Trigger a navigation that will carry auth headers
    try:
        await page.goto("https://www.zomato.com/", timeout=15000, wait_until="domcontentloaded")
    except Exception:
        pass

    # Give pending requests up to 5 seconds to fire
    for _ in range(50):
        if token_holder:
            break
        await asyncio.sleep(0.1)

    return token_holder[0] if token_holder else None


# ── Main setup flow ───────────────────────────────────────────────────────────

async def run_setup():
    from playwright.async_api import async_playwright

    print("\n=== Zomato MCP — First-Time Setup ===\n")

    # Step 1: ensure stable Android ID
    print("[1/6] Setting up device identity...")
    _ensure_android_id()

    # Step 2: launch headed browser
    print("[2/6] Opening Zomato login page in Chromium...")
    print("      Complete the phone OTP login IN THE BROWSER WINDOW that opens.")
    print("      Do not close it — this script will detect when you're logged in.\n")

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
            ignore_default_args=["--enable-automation"],
        )
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

        page = await context.new_page()

        try:
            await page.goto("https://www.zomato.com/login", timeout=30000)
            print("[3/6] Waiting for you to complete login (up to 3 minutes)...")

            # Poll until URL leaves the login page (redirect to home / order page = success)
            for _ in range(180):
                await asyncio.sleep(1)
                current = page.url
                if "zomato.com" in current and "/login" not in current:
                    print(f"      Detected post-login redirect → {current}")
                    break
            else:
                print("      Timed out waiting for login. Please try again.")
                return

            # Step 4: extract token
            print("[4/6] Extracting access token...")
            await asyncio.sleep(2)  # let JS settle

            cookies = await context.cookies()
            token = _find_token_in_cookies(cookies)

            if not token:
                print("      Token not in cookies — intercepting network requests...")
                token = await _extract_token_via_network(page)

            if not token:
                print("\n[!] Could not auto-extract token.")
                print("    Open DevTools in the browser, go to Application > Cookies > api.zomato.com")
                print("    and copy the value of the token cookie, then paste it here:")
                token = input("    Token: ").strip()

            if not token:
                print("[!] No token provided. Setup aborted.")
                return

            # Step 5: store to Windows Credential Manager
            print("[5/6] Storing token in Windows Credential Manager (DPAPI-encrypted)...")
            store_token(token)

            # Also store the FULL Zomato cookie jar so web-session API calls
            # can send proper Cookie headers to www.zomato.com/webroutes/*
            try:
                all_cookies = await context.cookies()
                zomato_cookies = [c for c in all_cookies if "zomato.com" in c.get("domain", "")]
                store_cookies(zomato_cookies)
                print(f"      Stored {len(zomato_cookies)} Zomato cookies for web-session access.")
            except Exception as ck_exc:
                print(f"\n[!] Warning: could not store cookie jar in Windows Credential Manager.")
                print(f"    Error: {ck_exc}")
                print("    The access token WAS stored, so basic features (order history,")
                print("    addresses) will work. However, web-route calls that need the full")
                print("    cookie context may fail until you re-run this setup successfully.")
                print("    Tip: ensure you have write access to Windows Credential Manager")
                print("    (run this script as your normal user, not as Administrator).\n")

            # Step 6: collect user preferences
            print("[6/6] Saving preferences to .env...")
            phone = input("\n    Your Zomato phone number (e.g. +919876543210): ").strip()
            if phone:
                _ensure_env_key("ZOMATO_PHONE", phone)

            lat = input("    Default delivery latitude  (e.g. 19.0760 for Mumbai) [Enter to skip]: ").strip()
            lon = input("    Default delivery longitude (e.g. 72.8777): ").strip()
            city = input("    Default city ID (e.g. 3 for Mumbai, 4 for Delhi)  [Enter to skip]: ").strip()
            if lat:
                _ensure_env_key("ZOMATO_DEFAULT_LAT", lat)
            if lon:
                _ensure_env_key("ZOMATO_DEFAULT_LON", lon)
            if city:
                _ensure_env_key("ZOMATO_DEFAULT_CITY_ID", city)

        finally:
            await context.close()

    print("\n✓ Setup complete!\n")
    print("Next steps:")
    print("  1. Add the 'zomato' entry to claude_desktop_config.json (see plan).")
    print("  2. Fully restart Claude Desktop (quit from system tray, not just close window).")
    print("  3. Look for the hammer icon — you should see 20 Zomato tools.\n")
    print("  To verify your token is stored:")
    print("  python -c \"import keyring; print(keyring.get_password('zomato_mcp','access_token'))\"")


if __name__ == "__main__":
    asyncio.run(run_setup())
