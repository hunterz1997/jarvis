#!/usr/bin/env python3
"""
Zomato MCP Server — 20 tools, dual transport.

Transport is controlled by ZOMATO_MCP_TRANSPORT env var:
  "stdio"           — Claude Desktop (default; spawned as subprocess)
  "streamable-http" — Jarvis / any HTTP MCP client (port 8765 by default)

First-time setup:
    python zomato_setup.py   (run ONCE in a terminal to log in and store token)

Then add to claude_desktop_config.json and restart Claude Desktop.
"""

import asyncio
import json
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

# ── Absolute paths ─────────────────────────────────────────────────────────────
_HERE = Path(__file__).parent

# ── Logging: file only — stdout is reserved for JSON-RPC frames ───────────────
_log_handler = RotatingFileHandler(
    _HERE / "zomato_mcp.log",
    maxBytes=5 * 1024 * 1024,
    backupCount=3,
    encoding="utf-8",
)
logging.basicConfig(
    handlers=[_log_handler],
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
# Suppress uvicorn banners (stderr is safe but noisy in service mode)
logging.getLogger("uvicorn").setLevel(logging.WARNING)
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
logging.getLogger("mcp").setLevel(logging.WARNING)

log = logging.getLogger(__name__)

# ── Load .env with explicit path BEFORE importing our modules ─────────────────
from dotenv import load_dotenv
load_dotenv(_HERE / ".env")

# Ensure sibling modules are importable regardless of CWD
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

# ── Project imports ───────────────────────────────────────────────────────────
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

import zomato_api as api
import zomato_browser as browser
from zomato_auth import (
    ZomatoAuthRequired,
    clear_token,
    store_token,
)

# ── MCP server instance ───────────────────────────────────────────────────────
# host/port must be passed at construction time — FastMCP passes them explicitly
# to pydantic-settings, so env-var fallback inside Settings() never fires.
# Reading from env here ensures the Task Scheduler batch file's
# FASTMCP_HOST/FASTMCP_PORT values are honoured.
mcp = FastMCP(
    "zomato_mcp",
    host=os.getenv("FASTMCP_HOST", "127.0.0.1"),
    port=int(os.getenv("FASTMCP_PORT", "8000")),
)

# Reusable "not set up" sentinel (mirrors LinkedIn pattern)
_NEEDS_BROWSER = browser.SETUP_REQUIRED


# ── Helpers ───────────────────────────────────────────────────────────────────

def _ok(data) -> str:
    if isinstance(data, (dict, list)):
        # default=str prevents TypeError on non-serialisable types (datetime, etc.)
        return json.dumps(data, ensure_ascii=False, indent=2, default=str)
    return str(data)


def _err(message: str) -> str:
    return json.dumps({"error": message})


def _auth_required_msg() -> str:
    return json.dumps({
        "auth_required": True,
        "action": "Call zomato_login_start(phone='+91XXXXXXXXXX') to begin OTP login.",
    })


# ═══════════════════════════════════════════════════════════════════════════════
# AUTH TOOLS
# ═══════════════════════════════════════════════════════════════════════════════

@mcp.tool()
async def zomato_login_start(
    phone: str = Field(description="Phone number with country code, e.g. +919876543210"),
    country_id: str = Field(default="1", description="Country code ID (1 = India)"),
    otp_pref: str = Field(default="sms", description="OTP delivery: 'sms', 'whatsapp', or 'call'"),
    force_otp: bool = Field(
        default=False,
        description=(
            "Set True to send OTP even if a web session already exists. "
            "Required to obtain the Android OAuth token needed for cart and ordering — "
            "the web session token alone is NOT accepted by the mobile API."
        ),
    ),
) -> str:
    """
    Initiate Zomato login with automatic session recovery.

    Priority order (no prompts unless the session is truly gone):
      0. Keyring check — if session is valid AND force_otp=False, return immediately.
      1. Silent headless profile recovery — if session expired.
      2. Direct HTTP OTP (PKCE) — sends OTP; needed once to get Android OAuth token.
      3. Headed browser OTP flow — fallback when HTTP path is blocked.

    Use force_otp=True when you need to place an order and have not yet obtained
    the Android OAuth access_token through a previous OTP login.
    """
    redacted = "*" * max(0, len(phone) - 4) + phone[-4:]
    log.info("login_start for %s via %s force_otp=%s", redacted, otp_pref, force_otp)

    # ── Step 0: keyring check — fastest path, no browser needed ──────────────
    # Skip this check when force_otp=True (caller explicitly wants a fresh PKCE token)
    from zomato_auth import get_token as _get_token
    existing_token = _get_token()
    if existing_token and not force_otp:
        try:
            await api._web_get("/webroutes/user/orders", params={"page": 1, "n": 1})
            log.info("login_start: existing session valid — no OTP needed")
            return json.dumps({
                "status": "already_logged_in",
                "method": "keyring",
                "message": (
                    "Your Zomato session is already active and valid. "
                    "No OTP or browser needed. You can use all Zomato tools directly. "
                    "If you need to place an order and it fails with auth error, "
                    "call zomato_login_start(phone=..., force_otp=True) to get the "
                    "Android OAuth token required by the mobile ordering API."
                ),
            }, ensure_ascii=False)
        except Exception as val_exc:
            log.info("login_start: stored token invalid (%s) — refreshing", val_exc)

    # ── Step 1: silent headless profile recovery (skip when force_otp) ──────────
    # When force_otp=True we want a fresh OTP → skip silent recovery.
    if not force_otp and browser.PROFILE_DIR.exists() and not browser._login_context:
        try:
            token = await browser.browser_extract_token()
            if token:
                store_token(token)
                log.info("login_start: token recovered from Chromium profile")
                return json.dumps({
                    "status": "already_logged_in",
                    "method": "profile_recovery",
                    "message": (
                        "Session recovered silently from your saved Chromium profile. "
                        "Token refreshed. No OTP needed."
                    ),
                }, ensure_ascii=False)
        except Exception as rec_exc:
            log.info("login_start: profile recovery failed (%s)", rec_exc)

    # ── Step 2: browser OTP flow (force_otp=True) OR HTTP fallback ────────────
    if force_otp:
        try:
            result = await browser.browser_login_start(phone)
            result["method"] = "browser"
            result.setdefault(
                "next_step",
                f"Call zomato_login_verify(phone='{phone}', otp='<6-digit OTP>')",
            )
            log.info("login_start: browser OTP flow started for force_otp request")
            return json.dumps(result, ensure_ascii=False, indent=2, default=str)
        except Exception as br_exc:
            log.warning("login_start: browser OTP failed (%s) — trying HTTP fallback", br_exc)

    # ── Step 3: HTTP OTP flow (quick, for auto-refresh; also force_otp fallback)
    try:
        result = await api.api_login_start(phone, country_id, otp_pref)
        if result.get("status") is True or result.get("status") == "true":
            log.info("login_start HTTP succeeded")
            return json.dumps({
                "status": "otp_sent",
                "method": "http",
                "phone": phone,
                "raw_response": result,
                "next_step": f"Call zomato_login_verify(phone='{phone}', otp='<6-digit OTP>')",
                "note": (
                    "OTP sent via HTTP. If zomato_login_verify fails, "
                    "call zomato_login_start again with force_otp=True to use "
                    "the browser path which handles OAuth context reliably."
                ),
            }, ensure_ascii=False)
        log.info("login_start HTTP returned status=false, trying browser OTP flow")
    except Exception as http_exc:
        log.info("login_start HTTP failed (%s) — using browser OTP flow", http_exc)

    # ── Step 4: browser OTP fallback (when HTTP path fails/returns false) ──────
    try:
        result = await browser.browser_login_start(phone)
        result["method"] = "browser"
        result.setdefault(
            "next_step",
            f"Call zomato_login_verify(phone='{phone}', otp='<6-digit OTP>')",
        )
        return json.dumps(result, ensure_ascii=False, indent=2, default=str)
    except Exception as br_exc:
        log.error("login_start browser error: %s", br_exc)
        return _err(
            f"All login paths failed. Browser error: {br_exc}. "
            f"Try running 'python zomato_setup.py' in a terminal to re-authenticate."
        )


@mcp.tool()
async def zomato_login_verify(
    phone: str = Field(description="Same phone number used in zomato_login_start"),
    otp: str = Field(description="6-digit OTP received on the phone"),
    country_id: str = Field(default="1"),
) -> str:
    """
    Verify the OTP and store the access token.

    If a browser window is still open from zomato_login_start (browser path),
    the OTP is filled there automatically.  Otherwise the HTTP PKCE flow is used.
    """
    # ── Path B: browser session in progress ───────────────────────────────────
    if browser._login_page is not None:
        try:
            result = await browser.browser_login_verify(otp)
            return json.dumps(result, ensure_ascii=False, indent=2, default=str)
        except Exception as br_exc:
            log.error("login_verify browser error: %s", br_exc)
            return _err(str(br_exc))

    # ── Path A: HTTP PKCE flow ─────────────────────────────────────────────────
    try:
        result = await api.api_login_verify(phone, otp, country_id)

        token = (
            result.get("access_token")
            or result.get("zat")
            or result.get("data", {}).get("access_token")
            or result.get("token")
            or result.get("data", {}).get("token")
            or result.get("auth", {}).get("access_token")
            or result.get("auth_token")
            or result.get("session_token")
            or result.get("user", {}).get("access_token")
        )
        if token:
            store_token(token)
            log.info("login_verify: token stored successfully")
            return json.dumps({
                "status": "logged_in",
                "message": "Token stored securely in Windows Credential Manager.",
                "phone": phone,
                "oauth_completed": result.get("oauth_completed", True),
            })
        else:
            log.warning("login_verify: no token; response keys: %s", list(result.keys()))
            return json.dumps({
                "status": "verify_response_received",
                "response_keys": list(result.keys()),
                "oauth_note": result.get("oauth_note", ""),
                "note": (
                    "OTP accepted but the token could not be extracted automatically. "
                    "Run 'python zomato_setup.py' in a terminal to complete login "
                    "through the browser and refresh stored credentials."
                ),
            }, ensure_ascii=False)
    except Exception as exc:
        log.error("login_verify error: %s", exc)
        return _err(str(exc))


@mcp.tool()
async def zomato_logout() -> str:
    """Clear the stored Zomato access token from Windows Credential Manager."""
    clear_token()
    return json.dumps({"status": "logged_out", "message": "Token cleared from Credential Manager."})


# ═══════════════════════════════════════════════════════════════════════════════
# DISCOVERY TOOLS
# ═══════════════════════════════════════════════════════════════════════════════

@mcp.tool()
async def zomato_search_restaurants(
    query: str = Field(description="Restaurant name, cuisine, or dish to search for"),
    lat: float = Field(description="Delivery latitude"),
    lon: float = Field(description="Delivery longitude"),
    max_results: int = Field(default=10, description="Maximum number of results to return"),
) -> str:
    """Search for restaurants near a location by name, cuisine, or dish."""
    try:
        data = await api.api_search(query, lat, lon, max_results)
        return _ok(data)
    except ZomatoAuthRequired:
        return _auth_required_msg()
    except Exception as api_exc:
        log.info("search_restaurants API path failed (%s) — using browser", api_exc)
        if browser.PROFILE_DIR.exists():
            try:
                data = await browser.browser_search_restaurants(query, lat, lon, max_results)
                return _ok(data)
            except Exception as br_exc:
                log.error("search_restaurants browser fallback error: %s", br_exc)
                return _err(str(br_exc))
        return _err(str(api_exc))


@mcp.tool()
async def zomato_get_menu_listing(
    restaurant_id: str = Field(description="Zomato restaurant ID (res_id)"),
) -> str:
    """Fetch the menu item listing for a restaurant (names and categories, fast)."""
    try:
        data = await api.api_restaurant_menu_listing(restaurant_id)
        return _ok(data)
    except ZomatoAuthRequired:
        return _auth_required_msg()
    except Exception as api_exc:
        log.info("get_menu_listing API failed (%s) — using browser", api_exc)
        if browser.PROFILE_DIR.exists():
            try:
                async def _browser_menu():
                    p, context = await browser._open_context(headless=True)
                    try:
                        page = await context.new_page()
                        await page.goto(
                            f"{browser.ZOMATO_HOME}/order/{restaurant_id}/menu",
                            timeout=30000, wait_until="domcontentloaded"
                        )
                        await asyncio.sleep(2)
                        data = await browser._extract_page_data(page)
                        return data or {"error": "No menu data found in page"}
                    finally:
                        await context.close()
                        await p.stop()
                data = await _browser_menu()
                return _ok(data)
            except Exception as br_exc:
                log.error("get_menu_listing browser error: %s", br_exc)
                return _err(str(br_exc))
        return _err(str(api_exc))


@mcp.tool()
async def zomato_get_menu_by_category(
    restaurant_id: str = Field(description="Zomato restaurant ID (res_id)"),
    category: Optional[str] = Field(default=None, description="Category name to filter by (optional)"),
) -> str:
    """Fetch the full menu with variants and add-ons for a restaurant or specific category."""
    try:
        data = await api.api_restaurant_menu_by_category(restaurant_id, category)
        return _ok(data)
    except ZomatoAuthRequired:
        return _auth_required_msg()
    except Exception as api_exc:
        log.info("get_menu_by_category API failed (%s) — using browser", api_exc)
        if browser.PROFILE_DIR.exists():
            try:
                async def _browser_menu_full():
                    p, context = await browser._open_context(headless=True)
                    try:
                        page = await context.new_page()
                        url = f"{browser.ZOMATO_HOME}/order/{restaurant_id}/menu"
                        if category:
                            url += f"?category={category}"
                        await page.goto(url, timeout=30000, wait_until="domcontentloaded")
                        await asyncio.sleep(2)
                        data = await browser._extract_page_data(page)
                        return data or {"error": "No menu data found in page"}
                    finally:
                        await context.close()
                        await p.stop()
                data = await _browser_menu_full()
                return _ok(data)
            except Exception as br_exc:
                log.error("get_menu_by_category browser error: %s", br_exc)
                return _err(str(br_exc))
        return _err(str(api_exc))


# ═══════════════════════════════════════════════════════════════════════════════
# ADDRESS TOOLS
# ═══════════════════════════════════════════════════════════════════════════════

@mcp.tool()
async def zomato_get_addresses() -> str:
    """Fetch all saved delivery addresses from your Zomato account."""
    try:
        data = await api.api_get_addresses()
        return _ok(data)
    except ZomatoAuthRequired:
        return _auth_required_msg()
    except Exception as api_exc:
        log.info("get_addresses API failed (%s) — using browser", api_exc)
        if browser.PROFILE_DIR.exists():
            try:
                data = await browser.browser_get_addresses_data()
                return _ok(data)
            except Exception as br_exc:
                log.error("get_addresses browser error: %s", br_exc)
                return _err(str(br_exc))
        return _err(str(api_exc))


@mcp.tool()
async def zomato_add_address(
    label: str = Field(description="Address label, e.g. 'Home', 'Work', 'Office'"),
    full_address: str = Field(description="Complete address string"),
    lat: float = Field(description="Latitude of the address"),
    lon: float = Field(description="Longitude of the address"),
) -> str:
    """Add a new delivery address. Requires browser profile (run zomato_setup.py first)."""
    if not browser.PROFILE_DIR.exists():
        return _NEEDS_BROWSER
    result = await browser.browser_add_address(label, full_address, lat, lon)
    return _ok(result)


@mcp.tool()
async def zomato_edit_address(
    address_id: str = Field(description="ID of the address to edit (from zomato_get_addresses)"),
    label: Optional[str] = Field(default=None, description="New label (optional)"),
    full_address: Optional[str] = Field(default=None, description="New address text (optional)"),
) -> str:
    """Edit an existing delivery address."""
    if not browser.PROFILE_DIR.exists():
        return _NEEDS_BROWSER
    result = await browser.browser_edit_address(address_id, label=label, full_address=full_address)
    return _ok(result)


@mcp.tool()
async def zomato_delete_address(
    address_id: str = Field(description="ID of the address to delete (from zomato_get_addresses)"),
) -> str:
    """Delete a saved delivery address."""
    if not browser.PROFILE_DIR.exists():
        return _NEEDS_BROWSER
    result = await browser.browser_delete_address(address_id)
    return _ok(result)


# ═══════════════════════════════════════════════════════════════════════════════
# CART TOOLS
# ═══════════════════════════════════════════════════════════════════════════════

@mcp.tool()
async def zomato_create_cart(
    restaurant_id: str = Field(description="Zomato restaurant ID (res_id)"),
    address_id: str = Field(description="Delivery address ID (from zomato_get_addresses)"),
) -> str:
    """Create a new cart for ordering from a restaurant."""
    try:
        data = await api.api_create_cart(restaurant_id, address_id)
        return _ok(data)
    except ZomatoAuthRequired:
        return _auth_required_msg()
    except Exception as exc:
        log.error("create_cart error: %s", exc)
        return _err(str(exc))


@mcp.tool()
async def zomato_add_to_cart(
    cart_id: str = Field(description="Cart ID from zomato_create_cart"),
    item_id: str = Field(description="Menu item ID from zomato_get_menu_by_category"),
    quantity: int = Field(default=1, description="Number of items to add"),
    variant_ids: Optional[list] = Field(default=None, description="Required for items with size/type variants. Get from menu listing."),
    addon_ids: Optional[list] = Field(default=None, description="Optional add-on IDs"),
) -> str:
    """Add an item to the cart. variant_ids may be required for items with size choices."""
    try:
        data = await api.api_add_to_cart(cart_id, item_id, quantity, variant_ids, addon_ids)
        return _ok(data)
    except ZomatoAuthRequired:
        return _auth_required_msg()
    except Exception as exc:
        err_str = str(exc)
        if "variant" in err_str.lower() or "400" in err_str:
            return _err(
                f"{err_str}\n\nHint: This item may require variant_ids. "
                "Call zomato_get_menu_by_category to see available variants and retry with variant_ids=[...]."
            )
        log.error("add_to_cart error: %s", exc)
        return _err(err_str)


@mcp.tool()
async def zomato_view_cart(
    cart_id: str = Field(description="Cart ID to view"),
) -> str:
    """View the current contents and total of a cart."""
    try:
        data = await api.api_view_cart(cart_id)
        return _ok(data)
    except ZomatoAuthRequired:
        return _auth_required_msg()
    except Exception as exc:
        log.error("view_cart error: %s", exc)
        return _err(str(exc))


@mcp.tool()
async def zomato_remove_from_cart(
    cart_id: str = Field(description="Cart ID"),
    item_id: str = Field(description="Item ID to remove"),
) -> str:
    """Remove an item from the cart."""
    try:
        data = await api.api_remove_from_cart(cart_id, item_id)
        return _ok(data)
    except ZomatoAuthRequired:
        return _auth_required_msg()
    except Exception as exc:
        log.error("remove_from_cart error: %s", exc)
        return _err(str(exc))


@mcp.tool()
async def zomato_get_cart_offers(
    cart_id: str = Field(description="Cart ID to fetch applicable offers for"),
) -> str:
    """Fetch available promo codes and offers applicable to the current cart."""
    try:
        data = await api.api_get_cart_offers(cart_id)
        return _ok(data)
    except ZomatoAuthRequired:
        return _auth_required_msg()
    except Exception as exc:
        log.error("get_cart_offers error: %s", exc)
        return _err(str(exc))


# ═══════════════════════════════════════════════════════════════════════════════
# ORDER TOOLS
# ═══════════════════════════════════════════════════════════════════════════════

@mcp.tool()
async def zomato_checkout(
    cart_id: str = Field(description="Cart ID to check out"),
    payment_method: str = Field(
        default="pay_later",
        description=(
            "Payment method: "
            "'pay_later' (post-pay, default — no credentials needed), "
            "'upi_qr' (returns QR code to scan), "
            "'card' (opens browser for card/3DS entry)"
        ),
    ),
) -> str:
    """
    Place the order. Use pay_later for immediate checkout with no payment hassle.
    A confirmation gate is always shown — Zomato will confirm the final bill before charging.
    """
    try:
        if payment_method == "card":
            if not browser.PROFILE_DIR.exists():
                return _NEEDS_BROWSER
            result = await browser.browser_pay_card(cart_id)
            return _ok(result)
        data = await api.api_checkout(cart_id, payment_method)
        return _ok(data)
    except ZomatoAuthRequired:
        return _auth_required_msg()
    except Exception as exc:
        log.error("checkout error: %s", exc)
        return _err(str(exc))


@mcp.tool()
async def zomato_track_order(
    order_id: str = Field(description="Order ID to track (from zomato_order_history or checkout response)"),
) -> str:
    """Get real-time tracking status and estimated delivery time for an order."""
    try:
        data = await api.api_track_order(order_id)
        summary = {}
        try:
            if "orderId" in data or "resInfo" in data:
                summary = {
                    "order_id":   data.get("orderId", order_id),
                    "date":       data.get("orderDate", ""),
                    "restaurant": data.get("resInfo", {}).get("name", ""),
                    "items":      data.get("dishString", ""),
                    "total":      data.get("totalCost", ""),
                    "status":     data.get("deliveryDetails", {}).get("deliveryLabel", ""),
                    "message":    data.get("deliveryDetails", {}).get("deliveryMessage", ""),
                    "address":    data.get("deliveryDetails", {}).get("deliveryAddress", ""),
                }
            else:
                od = data.get("response", data).get("order_details", {})
                hd = data.get("response", data).get("header_data", {})
                summary = {
                    "order_id":   od.get("tab_id", order_id),
                    "restaurant": od.get("res_name", ""),
                    "status":     hd.get("subtitle2", {}).get("text", ""),
                    "eta":        hd.get("pill_data", {}).get("left_data", {}).get("title", {}).get("text", ""),
                }
        except Exception:
            pass
        return json.dumps({"summary": summary, "raw": data}, ensure_ascii=False, indent=2, default=str)
    except ZomatoAuthRequired:
        return _auth_required_msg()
    except Exception as exc:
        log.error("track_order error: %s", exc)
        return _err(str(exc))


@mcp.tool()
async def zomato_order_history(
    limit: int = Field(default=10, description="Number of recent orders to return (max 50)"),
) -> str:
    """Fetch your recent Zomato order history."""
    try:
        data = await api.api_order_history(min(limit, 50))
        return _ok(data)
    except ZomatoAuthRequired:
        return _auth_required_msg()
    except Exception as api_exc:
        log.info("order_history API failed (%s) — using browser", api_exc)
        if browser.PROFILE_DIR.exists():
            try:
                data = await browser.browser_order_history(min(limit, 50))
                return _ok(data)
            except Exception as br_exc:
                log.error("order_history browser error: %s", br_exc)
                return _err(str(br_exc))
        return _err(str(api_exc))


@mcp.tool()
async def zomato_reorder(
    order_id: str = Field(description="Order ID to reorder (from zomato_order_history)"),
    address_id: str = Field(description="Delivery address ID for the new order"),
) -> str:
    """
    Rebuild a cart from a past order and return the new cart ID.
    After calling this, use zomato_view_cart and zomato_checkout to complete.
    """
    try:
        order_data = await api.api_order_details(order_id)
        raw = order_data.get("response", order_data)
        od = raw.get("order_details", {})
        res_id = od.get("res_id") or od.get("restaurant_id")

        if not res_id:
            return _err(f"Could not extract restaurant ID from order {order_id}. Raw: {json.dumps(od)[:300]}")

        cart_data = await api.api_create_cart(str(res_id), address_id)
        cart_id = cart_data.get("cart_id") or cart_data.get("data", {}).get("cart_id")

        if not cart_id:
            return _ok({
                "note": "Cart created but could not extract cart_id. Check raw response.",
                "raw_cart": cart_data,
                "order_details": od,
            })

        items_added = []
        items_failed = []
        order_items = od.get("items", []) or raw.get("items", [])
        for item in order_items:
            item_id = item.get("item_id") or item.get("id")
            qty = item.get("quantity", 1)
            variants = item.get("variant_ids", [])
            if not item_id:
                continue
            try:
                await api.api_add_to_cart(cart_id, str(item_id), qty, variants or None)
                items_added.append(item_id)
            except Exception as e:
                items_failed.append({"item_id": item_id, "error": str(e)})

        return json.dumps({
            "status": "cart_created",
            "cart_id": cart_id,
            "items_added": items_added,
            "items_failed": items_failed,
            "next_step": f"Call zomato_view_cart(cart_id='{cart_id}') to review, then zomato_checkout(cart_id='{cart_id}').",
        }, ensure_ascii=False, indent=2, default=str)
    except ZomatoAuthRequired:
        return _auth_required_msg()
    except Exception as exc:
        log.error("reorder error: %s", exc)
        return _err(str(exc))


# ═══════════════════════════════════════════════════════════════════════════════
# TABLE BOOKING
# ═══════════════════════════════════════════════════════════════════════════════

@mcp.tool()
async def zomato_book_table(
    restaurant_id: str = Field(description="Zomato restaurant ID"),
    date: str = Field(description="Date in YYYY-MM-DD format"),
    time: str = Field(description="Time in HH:MM (24-hour) format"),
    guests: int = Field(default=2, description="Number of guests"),
) -> str:
    """Book a table at a restaurant. Requires browser profile (run zomato_setup.py first)."""
    if not browser.PROFILE_DIR.exists():
        return _NEEDS_BROWSER
    result = await browser.browser_book_table(restaurant_id, date, time, guests)
    return _ok(result)


# ═══════════════════════════════════════════════════════════════════════════════
# Transport entry point
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    transport = os.getenv("ZOMATO_MCP_TRANSPORT", "stdio")
    log.info(
        "starting Zomato MCP server (transport=%s host=%s port=%s)",
        transport,
        mcp.settings.host,
        mcp.settings.port,
    )
    mcp.run(transport=transport)
