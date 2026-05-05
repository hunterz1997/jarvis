#!/usr/bin/env python3
"""
Zomato MCP — Playwright persistent-profile browser automation layer.

Used for:
  1. Token recovery (rung 1 of the 401-retry ladder) — headless, silent
  2. Address CRUD (add/edit/delete) — no public API endpoints exist
  3. Table booking — Zomato Book has no documented public API
  4. Card payment — requires navigating Razorpay/3DS flows in a real browser

Profile persistence:
  A permanent Chromium user-data-dir is kept at ./zomato_chromium_profile
  (created by zomato_setup.py). This preserves cookies, IndexedDB, and service
  worker registrations across restarts, so most runs require no re-login.

Anti-detection posture:
  - --disable-blink-features=AutomationControlled
  - ignore_default_args=["--enable-automation"]
  - navigator.webdriver override via add_init_script
  - No header randomisation (Akamai fingerprints consistency, not diversity)
"""

import asyncio
import logging
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

_HERE       = Path(__file__).parent
PROFILE_DIR = _HERE / "zomato_chromium_profile"
ENV_FILE    = _HERE / ".env"
load_dotenv(ENV_FILE)

log = logging.getLogger(__name__)

ZOMATO_HOME  = "https://www.zomato.com"
ZOMATO_LOGIN = "https://www.zomato.com/login"   # accounts.zomato.com/login returns an error page

SETUP_REQUIRED = (
    "Browser profile not found. Run `python zomato_setup.py` once in a terminal "
    "to complete the first-time login. Then retry this tool."
)


# ── Context factory ───────────────────────────────────────────────────────────

async def _open_context(headless: bool = True):
    """
    Launch a persistent Chromium context.
    Returns (playwright_instance, browser_context).
    Always close both in a try/finally block.
    """
    from playwright.async_api import async_playwright
    p = await async_playwright().start()
    context = await p.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE_DIR),
        headless=headless,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--disable-http2",          # avoids ERR_HTTP2_PROTOCOL_ERROR on some networks
            "--disable-quic",           # disable QUIC/HTTP3 (also causes protocol errors)
        ],
        ignore_default_args=["--enable-automation"],
    )
    await context.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )
    return p, context


def _find_token_in_cookies(cookies: list) -> Optional[str]:
    """
    Find the Zomato access token (zat) in a Playwright cookie list.

    Always filters to .zomato.com domain first to avoid capturing tokens
    from other sites (e.g. YouTube's __Secure-ROLLOUT_TOKEN) that happen
    to share the same persistent Chromium profile.
    """
    zomato_cookies = [c for c in cookies if "zomato.com" in c.get("domain", "")]
    priority = {"zat", "x-access-token", "zomato_access_token", "_zat"}
    for c in zomato_cookies:
        if c["name"].lower() in priority:
            return c["value"]
    for c in zomato_cookies:
        name = c["name"].lower()
        if "access" in name or ("token" in name and "csrf" not in name):
            return c["value"]
    return None


# ── Token recovery (retry-ladder rung 1) ─────────────────────────────────────

async def browser_extract_token() -> Optional[str]:
    """
    Silently launch headless Chromium using the persistent profile, check if
    the Zomato session is still valid, and extract the access token from cookies.

    Returns the token string on success, None if the session has expired.
    """
    if not PROFILE_DIR.exists():
        log.warning("browser_extract_token: profile dir missing — setup not run")
        return None

    p, context = await _open_context(headless=True)
    try:
        page = await context.new_page()
        await page.goto(ZOMATO_HOME, timeout=20000, wait_until="domcontentloaded")
        await asyncio.sleep(2)

        cookies = await context.cookies()
        token = _find_token_in_cookies(cookies)

        if not token:
            # Intercept a network request to get the header
            token_holder: list[str] = []

            def on_req(req):
                if not token_holder:
                    t = req.headers.get("x-zomato-access-token", "")
                    if t:
                        token_holder.append(t)

            page.on("request", on_req)
            await page.goto(ZOMATO_HOME + "/order", timeout=15000, wait_until="domcontentloaded")
            for _ in range(30):
                if token_holder:
                    break
                await asyncio.sleep(0.2)
            if token_holder:
                token = token_holder[0]

        if token:
            log.info("browser_extract_token: token recovered")
            # Also refresh the cookie jar so web-session requests stay current
            try:
                from zomato_auth import store_cookies
                fresh = await context.cookies()
                zomato = [c for c in fresh if "zomato.com" in c.get("domain", "")]
                store_cookies(zomato)
                log.info("browser_extract_token: cookie jar updated (%d cookies)", len(zomato))
            except Exception as ck_exc:
                log.warning("browser_extract_token: cookie jar update failed: %s", ck_exc)
        else:
            log.info("browser_extract_token: session expired, token not found")

        return token
    except Exception as exc:
        log.error("browser_extract_token error: %s", exc)
        return None
    finally:
        await context.close()
        await p.stop()


async def is_session_valid() -> bool:
    """Return True if the persistent browser profile has a valid Zomato login."""
    if not PROFILE_DIR.exists():
        return False
    p, context = await _open_context(headless=True)
    try:
        page = await context.new_page()
        await page.goto(ZOMATO_HOME, timeout=20000, wait_until="domcontentloaded")
        await asyncio.sleep(1)
        # Logged-in pages don't redirect to /login
        return "login" not in page.url and "accounts.zomato.com" not in page.url
    except Exception:
        return False
    finally:
        await context.close()
        await p.stop()


# ── Address CRUD (browser only) ───────────────────────────────────────────────

async def browser_add_address(label: str, full_address: str, lat: float, lon: float) -> dict:
    """Navigate the Zomato web address-add flow and return the new address details."""
    if not PROFILE_DIR.exists():
        return {"error": SETUP_REQUIRED}

    p, context = await _open_context(headless=False)
    try:
        page = await context.new_page()
        await page.goto(f"{ZOMATO_HOME}/account/addresses", timeout=30000)
        await asyncio.sleep(2)

        # Click "Add new address"
        add_btn = page.get_by_text("Add new address", exact=False)
        if await add_btn.count() == 0:
            add_btn = page.get_by_role("button", name="Add address")
        await add_btn.first.click()
        await asyncio.sleep(1)

        # Fill address search/text field
        address_input = page.get_by_placeholder("Enter your complete address")
        if await address_input.count() == 0:
            address_input = page.locator("input[type=text]").first
        await address_input.fill(full_address)
        await asyncio.sleep(1)

        # Select first suggestion if dropdown appears
        suggestion = page.locator(".suggestion-item, [class*='suggestion']").first
        if await suggestion.count() > 0:
            await suggestion.click()
            await asyncio.sleep(1)

        # Fill label (Home / Work / Other)
        label_input = page.get_by_placeholder("Label (Home, Work, etc)")
        if await label_input.count() > 0:
            await label_input.fill(label)

        # Submit — check existence after each fallback before clicking
        save_btn = page.get_by_role("button", name="Save")
        if await save_btn.count() == 0:
            save_btn = page.get_by_text("Save address", exact=False)
        if await save_btn.count() == 0:
            save_btn = page.get_by_role("button", name="Submit")
        if await save_btn.count() > 0:
            await save_btn.first.click()
        else:
            raise RuntimeError("Save button not found on address form — Zomato may have updated their UI")
        await asyncio.sleep(2)

        return {"status": "created", "label": label, "address": full_address,
                "note": "Verify in Zomato app. Address ID will appear in zomato_get_addresses()."}
    except Exception as exc:
        log.error("browser_add_address error: %s", exc)
        return {"error": str(exc)}
    finally:
        await context.close()
        await p.stop()


async def browser_delete_address(address_id: str) -> dict:
    """Navigate to address list and delete the given address_id."""
    if not PROFILE_DIR.exists():
        return {"error": SETUP_REQUIRED}

    p, context = await _open_context(headless=False)
    try:
        page = await context.new_page()
        await page.goto(f"{ZOMATO_HOME}/account/addresses", timeout=30000)
        await asyncio.sleep(2)

        # Try to find and click delete for this address
        delete_btn = page.locator(f"[data-address-id='{address_id}'] [aria-label='Delete']")
        if await delete_btn.count() == 0:
            delete_btn = page.locator(f"[data-id='{address_id}'] button").last
        if await delete_btn.count() > 0:
            await delete_btn.click()
            await asyncio.sleep(1)
            confirm = page.get_by_role("button", name="Delete")
            if await confirm.count() > 0:
                await confirm.click()
                await asyncio.sleep(1)
            return {"status": "deleted", "address_id": address_id}
        else:
            return {"error": f"Address {address_id} not found on the page. IDs may have changed."}
    except Exception as exc:
        log.error("browser_delete_address error: %s", exc)
        return {"error": str(exc)}
    finally:
        await context.close()
        await p.stop()


async def browser_edit_address(address_id: str, label: Optional[str] = None,
                                full_address: Optional[str] = None) -> dict:
    """Edit an existing address. Only label and full_address editing supported."""
    if not PROFILE_DIR.exists():
        return {"error": SETUP_REQUIRED}

    p, context = await _open_context(headless=False)
    try:
        page = await context.new_page()
        await page.goto(f"{ZOMATO_HOME}/account/addresses", timeout=30000)
        await asyncio.sleep(2)

        edit_btn = page.locator(f"[data-address-id='{address_id}'] [aria-label='Edit']")
        if await edit_btn.count() == 0:
            edit_btn = page.locator(f"[data-id='{address_id}']").get_by_text("Edit")
        if await edit_btn.count() == 0:
            return {"error": f"Address {address_id} edit button not found."}

        await edit_btn.first.click()
        await asyncio.sleep(1)

        if full_address:
            addr_input = page.get_by_placeholder("Enter your complete address")
            if await addr_input.count() > 0:
                await addr_input.fill(full_address)
                await asyncio.sleep(1)
        if label:
            label_input = page.get_by_placeholder("Label (Home, Work, etc)")
            if await label_input.count() > 0:
                await label_input.fill(label)

        save_btn = page.get_by_role("button", name="Save")
        if await save_btn.count() == 0:
            save_btn = page.get_by_text("Save address", exact=False)
        if await save_btn.count() > 0:
            await save_btn.first.click()
        else:
            raise RuntimeError("Save button not found on edit-address form")
        await asyncio.sleep(2)

        return {"status": "updated", "address_id": address_id}
    except Exception as exc:
        log.error("browser_edit_address error: %s", exc)
        return {"error": str(exc)}
    finally:
        await context.close()
        await p.stop()


# ── Table booking (browser only) ──────────────────────────────────────────────

async def browser_book_table(restaurant_id: str, date: str, time: str, guests: int = 2) -> dict:
    """
    Navigate to the restaurant's dining-out booking page and complete a reservation.

    date: "YYYY-MM-DD"
    time: "HH:MM" (24-hour)
    """
    if not PROFILE_DIR.exists():
        return {"error": SETUP_REQUIRED}

    p, context = await _open_context(headless=False)
    try:
        page = await context.new_page()
        # Construct booking URL — Zomato dining URL follows pattern /restaurant-name-city/book
        await page.goto(
            f"{ZOMATO_HOME}/-/book?res_id={restaurant_id}",
            timeout=30000, wait_until="domcontentloaded"
        )
        await asyncio.sleep(2)

        # Select date
        date_field = page.locator("input[type=date], [data-testid='date-picker']").first
        if await date_field.count() > 0:
            await date_field.fill(date)
            await asyncio.sleep(0.5)

        # Select time
        time_field = page.locator("input[type=time], [data-testid='time-picker']").first
        if await time_field.count() == 0:
            time_field = page.get_by_label("Time")
        if await time_field.count() > 0:
            await time_field.fill(time)
            await asyncio.sleep(0.5)

        # Set guest count
        guests_field = page.get_by_label("Guests")
        if await guests_field.count() == 0:
            guests_field = page.locator("select[name=guests], input[name=guests]").first
        if await guests_field.count() > 0:
            await guests_field.fill(str(guests))
            await asyncio.sleep(0.5)

        # Submit booking — check existence after each fallback
        book_btn = page.get_by_role("button", name="Book Table")
        if await book_btn.count() == 0:
            book_btn = page.get_by_role("button", name="Reserve")
        if await book_btn.count() == 0:
            book_btn = page.get_by_role("button", name="Confirm Booking")
        if await book_btn.count() > 0:
            await book_btn.first.click()
        else:
            raise RuntimeError("Book/Reserve button not found — Zomato may have updated their dining UI")
        await asyncio.sleep(3)

        return {
            "status": "submitted",
            "restaurant_id": restaurant_id,
            "date": date,
            "time": time,
            "guests": guests,
            "note": "Booking confirmation will appear in the browser and on your Zomato account.",
        }
    except Exception as exc:
        log.error("browser_book_table error: %s", exc)
        return {"error": str(exc)}
    finally:
        await context.close()
        await p.stop()


# ── Card payment (browser only) ───────────────────────────────────────────────
#
# Module-level holders keep the headed browser alive after browser_pay_card
# returns — the user needs the window open to complete 3DS authentication.
# The context/playwright instance are cleaned up on the NEXT call to
# browser_pay_card (or when the MCP process exits).
_pay_card_p = None        # type: ignore[assignment]
_pay_card_context = None  # type: ignore[assignment]


async def browser_pay_card(cart_id: str) -> dict:
    """
    Navigate to Zomato checkout for the cart and surface card payment.
    The browser window opens headed so the user can complete 3DS/OTP manually.
    The browser stays open after this function returns; it is cleaned up the
    next time browser_pay_card is called or when the MCP process exits.
    """
    global _pay_card_p, _pay_card_context

    if not PROFILE_DIR.exists():
        return {"error": SETUP_REQUIRED}

    # Close any previously dangling instance from a prior call
    if _pay_card_context is not None:
        try:
            await _pay_card_context.close()
        except Exception:
            pass
        _pay_card_context = None
    if _pay_card_p is not None:
        try:
            await _pay_card_p.stop()
        except Exception:
            pass
        _pay_card_p = None

    p, context = await _open_context(headless=False)
    # Store globally BEFORE entering try, so cleanup on error is possible
    _pay_card_p = p
    _pay_card_context = context

    try:
        page = await context.new_page()
        await page.goto(
            f"{ZOMATO_HOME}/order/cart?cart_id={cart_id}",
            timeout=30000, wait_until="domcontentloaded"
        )
        await asyncio.sleep(2)

        proceed_btn = page.get_by_role("button", name="Proceed to Pay")
        if await proceed_btn.count() > 0:
            await proceed_btn.click()
            await asyncio.sleep(2)

        # Return immediately — do NOT close the context; the user needs the window
        return {
            "status": "browser_opened",
            "note": (
                "Card payment page opened in a headed browser window. "
                "Complete the card details and 3DS verification there. "
                "The window will stay open until you close it or call this tool again."
            ),
            "cart_id": cart_id,
        }
    except Exception as exc:
        # On error we CAN close immediately (user has nothing to complete)
        _pay_card_p = None
        _pay_card_context = None
        try:
            await context.close()
        except Exception:
            pass
        try:
            await p.stop()
        except Exception:
            pass
        log.error("browser_pay_card error: %s", exc)
        return {"error": str(exc)}
    # Intentionally no finally block — context stays open for 3DS interaction


# ── Read-only data extraction (browser fallback for API tools) ─────────────────
#
# Zomato's web app is largely Server-Side Rendered (Next.js).  The initial page
# HTML already contains the full data set inside a
#   <script id="__NEXT_DATA__" type="application/json">…</script>
# tag that React's hydration reads on boot.  We also intercept any XHR/fetch
# JSON responses that fire during page load (for dynamic sections).
#
# These functions are called by zomato_mcp.py when the direct API path fails
# (e.g. because the mobile gateway requires OAuth tokens we don't have).


async def _extract_page_data(page) -> dict:
    """
    Extract structured data from a Zomato page after navigation.

    Strategy (in order):
      1. window.__NEXT_DATA__ — Next.js SSR payload (most reliable)
      2. window.__INITIAL_STATE__ / __INITIAL_DATA__ — older React pattern
      3. Inline <script> tag search for the JSON blob
    Returns the parsed dict, or {} if nothing found.
    """
    for js_var in ("__NEXT_DATA__", "__INITIAL_STATE__", "__INITIAL_DATA__"):
        try:
            val = await page.evaluate(f"() => window['{js_var}'] || null")
            if val:
                log.debug("_extract_page_data: found %s on %s", js_var, page.url)
                return val
            log.debug("_extract_page_data: %s is null/undefined on %s", js_var, page.url)
        except Exception as exc:
            log.debug("_extract_page_data: evaluate(%s) failed on %s: %s", js_var, page.url, exc)

    # Last-resort: grep the page source for the __NEXT_DATA__ JSON string
    try:
        html = await page.content()
        import re, json as _json
        m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
        if m:
            log.debug("_extract_page_data: found __NEXT_DATA__ via HTML regex on %s", page.url)
            return _json.loads(m.group(1))
        log.debug("_extract_page_data: no __NEXT_DATA__ script tag found on %s", page.url)
    except Exception as exc:
        log.debug("_extract_page_data: HTML regex fallback failed on %s: %s", page.url, exc)

    return {}


async def browser_order_history(limit: int = 10) -> dict:
    """
    Navigate to Zomato's order-history page and return structured order data.

    Tries three extraction methods in order:
      1. Intercept JSON fetch/XHR responses during page load
      2. Extract window.__NEXT_DATA__ (Next.js SSR payload)
      3. DOM text scrape of order cards as a last resort

    Returns a dict:
      {
        "orders": [...],          # list of order objects (from API or parsed SSR)
        "source": "xhr|ssr|dom",  # how the data was obtained
        "raw": {...}              # full raw payload (useful for debugging)
      }
    """
    if not PROFILE_DIR.exists():
        return {"error": SETUP_REQUIRED}

    p, context = await _open_context(headless=True)
    try:
        page = await context.new_page()
        xhr_captures: list = []

        async def on_response(resp):
            try:
                if resp.status != 200:
                    return
                if "zomato.com" not in resp.url:
                    return
                ct = resp.headers.get("content-type", "")
                if "json" not in ct:
                    return
                body = await resp.json()
                xhr_captures.append({"url": resp.url, "data": body})
            except Exception:
                pass

        page.on("response", on_response)

        # Try /my/orders first; fall back to /user/order
        for url_path in ("/my/orders", "/user/order", "/order"):
            try:
                await page.goto(ZOMATO_HOME + url_path, timeout=30000,
                                wait_until="domcontentloaded")
                await asyncio.sleep(2)
                break
            except Exception:
                continue

        # ── Method 1: XHR captures ────────────────────────────────────────────
        if xhr_captures:
            return {"source": "xhr", "captures": xhr_captures,
                    "note": "Inspect 'captures[].data' for order data."}

        # ── Method 2: __NEXT_DATA__ SSR payload ───────────────────────────────
        page_data = await _extract_page_data(page)
        if page_data:
            # Navigate down to order list (common Next.js pageProps shape)
            orders = (
                page_data.get("props", {}).get("pageProps", {}).get("orders")
                or page_data.get("props", {}).get("pageProps", {}).get("orderHistory")
                or page_data.get("props", {}).get("pageProps", {})
            )
            return {"source": "ssr", "orders": orders, "raw": page_data}

        # ── Method 3: DOM text scrape ─────────────────────────────────────────
        order_texts = []
        try:
            cards = await page.locator(
                "[class*='order'], [data-testid*='order'], [class*='Order']"
            ).all()
            for card in cards[:limit]:
                try:
                    order_texts.append(await card.inner_text())
                except Exception:
                    pass
        except Exception:
            pass

        title = await page.title()
        return {"source": "dom", "title": title, "order_texts": order_texts,
                "note": "DOM scrape — structured parsing may be needed."}

    except Exception as exc:
        log.error("browser_order_history error: %s", exc)
        return {"error": str(exc)}
    finally:
        await context.close()
        await p.stop()


async def browser_get_addresses_data() -> dict:
    """
    Navigate to Zomato account/addresses and return the list of saved addresses.
    Uses __NEXT_DATA__ extraction; falls back to DOM scrape.
    """
    if not PROFILE_DIR.exists():
        return {"error": SETUP_REQUIRED}

    p, context = await _open_context(headless=True)
    try:
        page = await context.new_page()
        xhr_captures: list = []

        async def on_response(resp):
            try:
                if resp.status != 200 or "zomato.com" not in resp.url:
                    return
                if "json" not in resp.headers.get("content-type", ""):
                    return
                body = await resp.json()
                xhr_captures.append({"url": resp.url, "data": body})
            except Exception:
                pass

        page.on("response", on_response)
        await page.goto(f"{ZOMATO_HOME}/account/addresses", timeout=30000,
                        wait_until="domcontentloaded")
        await asyncio.sleep(2)

        if xhr_captures:
            return {"source": "xhr", "captures": xhr_captures}

        page_data = await _extract_page_data(page)
        if page_data:
            addresses = (
                page_data.get("props", {}).get("pageProps", {}).get("addresses")
                or page_data.get("props", {}).get("pageProps", {})
            )
            return {"source": "ssr", "addresses": addresses, "raw": page_data}

        # DOM fallback
        addr_texts = []
        try:
            cards = await page.locator(
                "[class*='address'], [data-testid*='address']"
            ).all()
            for card in cards:
                try:
                    addr_texts.append(await card.inner_text())
                except Exception:
                    pass
        except Exception:
            pass

        return {"source": "dom", "address_texts": addr_texts}

    except Exception as exc:
        log.error("browser_get_addresses_data error: %s", exc)
        return {"error": str(exc)}
    finally:
        await context.close()
        await p.stop()


async def browser_search_restaurants(query: str, lat: float, lon: float,
                                      limit: int = 10) -> dict:
    """
    Navigate to Zomato's search page for the given query + location and
    extract restaurant results via XHR capture or __NEXT_DATA__.
    """
    if not PROFILE_DIR.exists():
        return {"error": SETUP_REQUIRED}

    import urllib.parse
    p, context = await _open_context(headless=True)
    try:
        page = await context.new_page()
        xhr_captures: list = []

        async def on_response(resp):
            try:
                if resp.status != 200 or "zomato.com" not in resp.url:
                    return
                if "json" not in resp.headers.get("content-type", ""):
                    return
                body = await resp.json()
                xhr_captures.append({"url": resp.url, "data": body})
            except Exception:
                pass

        page.on("response", on_response)

        # Zomato search URL pattern
        encoded_q = urllib.parse.quote(query)
        search_url = (
            f"{ZOMATO_HOME}/search?q={encoded_q}"
            f"&lat={lat}&lon={lon}&dishes=0"
        )
        await page.goto(search_url, timeout=30000, wait_until="domcontentloaded")
        await asyncio.sleep(3)

        if xhr_captures:
            return {"source": "xhr", "captures": xhr_captures}

        page_data = await _extract_page_data(page)
        if page_data:
            results = (
                page_data.get("props", {}).get("pageProps", {}).get("restaurants")
                or page_data.get("props", {}).get("pageProps", {}).get("searchResults")
                or page_data.get("props", {}).get("pageProps", {})
            )
            return {"source": "ssr", "results": results, "raw": page_data}

        # DOM fallback
        restaurant_texts = []
        try:
            cards = await page.locator(
                "[class*='result'], [class*='restaurant'], [data-testid*='restaurant']"
            ).all()
            for card in cards[:limit]:
                try:
                    restaurant_texts.append(await card.inner_text())
                except Exception:
                    pass
        except Exception:
            pass

        title = await page.title()
        return {"source": "dom", "title": title, "results": restaurant_texts}

    except Exception as exc:
        log.error("browser_search_restaurants error: %s", exc)
        return {"error": str(exc)}
    finally:
        await context.close()
        await p.stop()


# ── Browser-based login (headed, module-level globals) ────────────────────────
#
# Zomato's login endpoint requires a JavaScript-initialized PKCE OAuth context
# that curl_cffi cannot replicate without running the JS.  The browser approach
# is the only reliable path: open a headed Playwright window, auto-fill the
# phone, wait for OTP, then verify.  Module-level globals keep the browser alive
# between the login_start and login_verify MCP tool calls (same process).

_login_p = None        # type: ignore[assignment]
_login_context = None  # type: ignore[assignment]
_login_page = None     # type: ignore[assignment]


def _normalize_phone_local(phone: str) -> str:
    """Strip +91 prefix → bare 10-digit national number."""
    p = phone.strip().replace("-", "").replace(" ", "")
    if p.startswith("+91"):
        return p[3:]
    if p.startswith("91") and len(p) == 12:
        return p[2:]
    return p


async def browser_login_start(phone: str) -> dict:
    """
    Open a headed Chromium window on the Zomato login page and auto-fill the
    phone number + click 'Send OTP'.

    The browser stays open (via module-level globals) waiting for the user to
    receive the OTP.  Call browser_login_verify(otp='XXXXXX') to complete.
    """
    global _login_p, _login_context, _login_page

    # Clean up any previous dangling login browser
    for attr, obj in [("_login_context", _login_context), ("_login_p", _login_p)]:
        if obj is not None:
            try:
                if attr == "_login_context":
                    await obj.close()
                else:
                    await obj.stop()
            except Exception:
                pass
    _login_p = None
    _login_context = None
    _login_page = None

    bare = _normalize_phone_local(phone)

    # Use _open_context so --disable-http2 / --disable-quic flags are included
    p, context = await _open_context(headless=False)

    # Store globally BEFORE try so cleanup on error works
    _login_p = p
    _login_context = context

    try:
        page = await context.new_page()
        _login_page = page

        await page.goto(ZOMATO_LOGIN, timeout=30000, wait_until="domcontentloaded")
        await asyncio.sleep(3)

        # Auto-fill phone
        phone_filled = False
        for selector in [
            "input[type=tel]",
            "input[name=phoneNo]",
            "input[placeholder*='phone' i]",
            "input[placeholder*='mobile' i]",
            "input[placeholder*='number' i]",
            "input[autocomplete=tel]",
        ]:
            try:
                el = page.locator(selector).first
                if await el.count() > 0:
                    await el.click()
                    await el.fill(bare)
                    phone_filled = True
                    log.info("browser_login_start: filled phone in %s", selector)
                    await asyncio.sleep(1)
                    break
            except Exception:
                pass

        # Click Send OTP
        otp_requested = False
        if phone_filled:
            for btn_name in ["Continue", "Send OTP", "Get OTP", "Next"]:
                try:
                    btn = page.get_by_role("button", name=btn_name).first
                    if await btn.count() > 0:
                        await btn.click()
                        otp_requested = True
                        log.info("browser_login_start: clicked '%s'", btn_name)
                        await asyncio.sleep(3)
                        break
                except Exception:
                    pass

        return {
            "status": "browser_opened",
            "phone_filled": phone_filled,
            "otp_requested": otp_requested,
            "note": (
                "A browser window has opened. "
                + (
                    "Phone auto-filled and OTP requested. "
                    if otp_requested
                    else "Please enter your phone number and click Send OTP manually. "
                )
                + "Once you receive the OTP, call zomato_login_verify(otp='XXXXXX')."
            ),
        }
    except Exception as exc:
        # Error: clean up immediately
        _login_page = None
        _login_p = None
        _login_context = None
        try:
            await context.close()
        except Exception:
            pass
        try:
            await p.stop()
        except Exception:
            pass
        log.error("browser_login_start error: %s", exc)
        return {"error": str(exc)}
    # NO finally — browser stays open for OTP input


async def browser_login_verify(otp: str) -> dict:
    """
    Fill the OTP in the existing login browser and complete authentication.
    Must be called while browser_login_start()'s window is still open.
    Extracts + stores the zat cookie, then closes the browser.
    """
    global _login_p, _login_context, _login_page

    if _login_page is None or _login_context is None:
        return {
            "error": (
                "No active login browser session found. "
                "Call zomato_login_start(phone='+91XXXXXXXXXX') first."
            )
        }

    page = _login_page
    try:
        # ── Fast path: already logged in (browser landed on home / order page) ─
        current_url = page.url
        if ("login" not in current_url and "accounts.zomato.com" not in current_url
                and "zomato.com" in current_url):
            log.info("browser_login_verify: page already past login (%s) — extracting cookies", current_url)
            cookies = await _login_context.cookies()
            zomato_cookies = [c for c in cookies if "zomato.com" in c.get("domain", "")]
            token = _find_token_in_cookies(zomato_cookies)
            if token:
                from zomato_auth import store_token, store_cookies as _store_cookies
                store_token(token)
                _store_cookies(zomato_cookies)
                log.info("browser_login_verify: session already active — stored token + %d cookies", len(zomato_cookies))
                return {
                    "status": "logged_in",
                    "message": "Session already active in browser. Token stored in Windows Credential Manager.",
                    "cookies_stored": len(zomato_cookies),
                    "final_url": current_url,
                }

        # ── Fill OTP ──────────────────────────────────────────────────────────
        otp_filled = False
        for selector in [
            "[autocomplete=one-time-code]",
            "input[type=tel]",
            "input[name=otp]",
            "input[placeholder*='otp' i]",
            "input[placeholder*='code' i]",
            "input[placeholder*='verif' i]",
        ]:
            try:
                el = page.locator(selector).first
                if await el.count() > 0:
                    await el.click()
                    await el.fill(otp)
                    otp_filled = True
                    log.info("browser_login_verify: filled OTP in %s", selector)
                    await asyncio.sleep(1)
                    break
            except Exception:
                pass

        if not otp_filled:
            return {
                "error": (
                    "OTP input field not found in browser. "
                    "If you already entered the OTP manually in the browser and it's showing "
                    "the home page, call zomato_login_verify again with any value — "
                    "the tool will extract your session token from the browser cookies."
                ),
                "current_url": page.url,
                "browser_still_open": True,
            }

        # Click verify/submit
        for btn_name in ["Verify", "Submit", "Login", "Continue", "Confirm"]:
            try:
                btn = page.get_by_role("button", name=btn_name).first
                if await btn.count() > 0:
                    await btn.click()
                    log.info("browser_login_verify: clicked '%s'", btn_name)
                    await asyncio.sleep(4)
                    break
            except Exception:
                pass

        # Wait for post-login redirect (up to 30 s)
        for _ in range(30):
            url = page.url
            if "login" not in url and "accounts.zomato.com" not in url:
                break
            await asyncio.sleep(1)

        # Extract and store cookies
        cookies = await _login_context.cookies()
        zomato_cookies = [c for c in cookies if "zomato.com" in c.get("domain", "")]
        token = _find_token_in_cookies(zomato_cookies)

        if token:
            from zomato_auth import store_token, store_cookies as _store_cookies
            store_token(token)
            _store_cookies(zomato_cookies)
            log.info("browser_login_verify: stored token + %d cookies", len(zomato_cookies))
            return {
                "status": "logged_in",
                "message": "Login successful. Token stored in Windows Credential Manager.",
                "cookies_stored": len(zomato_cookies),
                "final_url": page.url,
            }
        else:
            return {
                "status": "login_attempted",
                "message": "OTP submitted but zat token not found — may still be redirecting.",
                "current_url": page.url,
                "cookie_names": [c["name"] for c in zomato_cookies][:15],
                "hint": "If logged in visually in the browser, try calling zomato_login_start again.",
            }

    except Exception as exc:
        log.error("browser_login_verify error: %s", exc)
        return {"error": str(exc)}
    finally:
        # Always close the login browser when done
        _login_page = None
        if _login_context is not None:
            try:
                await _login_context.close()
            except Exception:
                pass
            _login_context = None
        if _login_p is not None:
            try:
                await _login_p.stop()
            except Exception:
                pass
            _login_p = None
