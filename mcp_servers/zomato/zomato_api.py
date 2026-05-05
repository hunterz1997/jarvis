#!/usr/bin/env python3
"""
Zomato MCP — private gateway API client.

Uses curl_cffi to impersonate Chrome's TLS fingerprint (Chrome 120), bypassing
Akamai Bot Manager's JA3/JA4 fingerprint checks that block plain httpx/requests.

Authentication strategy:
  - Login flow: GET www.zomato.com/login (seeds CSRF/session cookies), then POST OTP.
    On success Zomato sets a `zat` cookie which is stored as the access token.
  - Web routes (www.zomato.com/webroutes/*): Cookie header with stored cookies.
  - Mobile gateway (api.zomato.com/gw/*): X-Zomato-Access-Token: <zat>.
    The `zat` token is accepted by both web routes and the mobile gateway.

Rate-limiting guidelines (stay below Akamai burst thresholds):
  - Browse / read operations: ≤ 1 request/sec
  - Cart mutations: ≤ 1 request per 3 seconds
"""

import logging
import os
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

from curl_cffi.requests import AsyncSession
from dotenv import load_dotenv

from zomato_auth import (
    ZomatoAuthRequired,
    get_device_headers,
    get_token as _get_stored_token,
    get_web_cookie_header,
    store_cookies,
    store_token,
    with_auth_retry,
)

_HERE = Path(__file__).parent
load_dotenv(_HERE / ".env")

log = logging.getLogger(__name__)

API_BASE      = "https://api.zomato.com"
WEB_BASE      = "https://www.zomato.com"
ACCOUNTS_BASE = "https://accounts.zomato.com"
TIMEOUT       = 30

# ── Mobile API key (from jomato reverse engineering of Zomato Android APK) ────
ZOMATO_API_KEY   = "7749b19667964b87a3efc739e254ada2"   # X-Zomato-API-Key
ZOMATO_CLIENT_ID = "5276d7f1-910b-4243-92ea-d27e758ad02b"  # X-Zomato-Client-Id

# ── Module-level login session state ─────────────────────────────────────────
# api_login_start() creates these; api_login_verify() consumes and clears them.
# Safe because the MCP server is a single long-running process.
_pkce_session: Optional[Any] = None   # curl_cffi AsyncSession kept alive between the two calls
_login_message_uuid: str = ""          # message_uuid from initiate response (needed by verify)
_login_web_hdrs_post: dict = {}        # web headers to reuse in verify (same as initiate)


# ── Mobile API headers (Zomato Android app, reverse-engineered) ───────────────

def _mobile_headers(extra: Optional[dict] = None) -> dict:
    """
    Return the full Android mobile API header set required by accounts.zomato.com
    (auth flow) and api.zomato.com/gw/* (mobile gateway).

    Critical headers that the previous implementation was missing:
      X-Zomato-API-Key   — most critical; Zomato returns 400 without this
      X-Zomato-Client-Id — identifies the Android OAuth app registration
      X-Zomato-UUID      — device GUID, must match across requests
      package_name       — NOT a header; must be in the POST body for auth calls

    Source: jomato project reverse engineering of Zomato Android APK v19.3.1
    """
    base = get_device_headers()   # X-Client-Id, X-Android-Id, X-Zomato-App-Version, User-Agent
    headers = {
        **base,
        # Override User-Agent with the exact Android APK format (jomato-sourced)
        "User-Agent": (
            "&source=android_market&version=10"
            "&device_manufacturer=Google&device_brand=google"
            "&device_model=Android+SDK+built+for+x86_64"
            "&api_version=931&app_version=v19.3.1"
        ),
        # ── Mandatory for auth endpoints ──────────────────────────────────────
        "X-Zomato-API-Key":        ZOMATO_API_KEY,
        "X-Zomato-Client-Id":      ZOMATO_CLIENT_ID,
        "X-Zomato-UUID":           "b2691abb-5aac-48a5-9f0e-750349080dcb",
        "X-Zomato-App-Version-Code": "1710019310",
        # ── Content negotiation ───────────────────────────────────────────────
        "Accept":          "image/webp",
        "Accept-Encoding": "br, gzip",
        "Connection":      "Keep-Alive",
        "Content-Type":    "application/x-www-form-urlencoded",
        # ── Device/app metadata ────────────────────────────────────────────────
        "X-App-Language":    "&lang=en&android_language=en&android_country=",
        "X-Device-Language": "en",
        "X-Network-Type":    "mobile_UNKNOWN",
        "X-APP-APPEARANCE":  "LIGHT",
        "X-APP-THEME":       "default",
        "X-City-Id":         "-1",
        "X-O2-City-Id":      "-1",
        "X-Device-Height":   "2208",
        "X-Device-Width":    "1080",
        "X-Device-Pixel-Ratio": "2.75",
        "USER-BUCKET":       "0",
        "USER-HIGH-PRIORITY": "0",
        # ── Feature flags ─────────────────────────────────────────────────────
        "X-BLINKIT-INSTALLED":   "false",
        "X-DISTRICT-INSTALLED":  "false",
        "X-RIDER-INSTALLED":     "false",
        "X-Bluetooth-On":        "false",
        "X-VPN-Active":          "1",
        "X-SYSTEM-APPEARANCE":   "UNSPECIFIED",
        "X-Accessibility-Voice-Over-Enabled": "0",
        "X-Accessibility-Dynamic-Text-Scale-Factor": "1.0",
    }
    if extra:
        headers.update(extra)
    return headers


# ── HTTP session factories ────────────────────────────────────────────────────

def _mobile_session(token: Optional[str] = None) -> AsyncSession:
    """
    Create a curl_cffi session for api.zomato.com/gw/* mobile gateway calls.

    Uses the Android OAuth access_token from the PKCE flow (stored in keyring).
    This is DIFFERENT from the web `zat` cookie used for webroutes — the mobile
    gateway validates a proper OAuth bearer token, not a session cookie.

    Falls back gracefully: if no token is stored, requests will return 401/403
    which triggers the auth-retry ladder.
    """
    tok = token or _get_stored_token()
    headers = _mobile_headers()
    headers.pop("Content-Type", None)   # GET requests shouldn't set Content-Type
    if tok:
        headers["X-Zomato-Access-Token"] = tok
    return AsyncSession(impersonate="chrome120", headers=headers, timeout=TIMEOUT)


def _web_session() -> AsyncSession:
    """
    Create a curl_cffi session for www.zomato.com web-route calls.

    Authentication: Cookie header using the stored Zomato session cookies
    (zat + PHPSESSID + csrf + …).  The `zat` cookie IS the Zomato web session
    token; it must be sent as a Cookie, not as a custom header.

    Falls back to `zat=<token>` if the full cookie jar has not been stored
    yet (e.g. first run before re-running zomato_setup.py).
    """
    cookie_str = get_web_cookie_header()
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.zomato.com/",
        "X-Requested-With": "XMLHttpRequest",
    }
    if cookie_str:
        headers["Cookie"] = cookie_str
    return AsyncSession(impersonate="chrome120", headers=headers, timeout=TIMEOUT)


def _loc_headers(lat: Optional[float], lon: Optional[float]) -> dict:
    """Build location headers, falling back to .env defaults."""
    lat = lat or float(os.getenv("ZOMATO_DEFAULT_LAT", "0"))
    lon = lon or float(os.getenv("ZOMATO_DEFAULT_LON", "0"))
    return {
        "x-zomato-lat": str(lat),
        "x-zomato-lon": str(lon),
        "X-User-Defined-Lat": str(lat),
        "X-User-Defined-Long": str(lon),
        "X-Present-Lat": str(lat),
        "X-Present-Long": str(lon),
        "X-City-Id": os.getenv("ZOMATO_DEFAULT_CITY_ID", "-1"),
    }


async def _web_get_multi(path: str, params: list) -> Any:
    """
    GET request to www.zomato.com with params as a list of (key, value) tuples.
    Needed for endpoints that accept repeated keys, e.g.
      order_id=X&order_id=Y&order_id=Z
    which a plain dict cannot express.
    """
    url = f"{WEB_BASE}{path}"
    async with _web_session() as s:
        r = await s.get(url, params=params, allow_redirects=False)
        if r.status_code in (301, 302):
            location = r.headers.get("location", "")
            if "login" in location or "accounts" in location:
                raise RuntimeError(f"403 session expired — redirect to {location}")
        if r.status_code in (401, 403):
            raise RuntimeError(f"403 from web GET {path}: {r.text[:300]}")
        r.raise_for_status()
        ct = r.headers.get("content-type", "")
        if "json" in ct:
            return r.json()
        raise RuntimeError(
            f"web GET {path} returned HTML (SSR), not JSON — use browser fallback"
        )


async def _web_get(path: str, params: dict = None) -> Any:
    """
    GET request to www.zomato.com using web-session cookie auth.

    Returns the JSON body, or raises RuntimeError so the caller can fall back
    to the Playwright browser layer.
    """
    url = f"{WEB_BASE}{path}"
    async with _web_session() as s:
        r = await s.get(url, params=params or {}, allow_redirects=False)
        if r.status_code in (301, 302):
            location = r.headers.get("location", "")
            if "login" in location or "accounts" in location:
                raise RuntimeError(f"403 session expired — redirect to {location}")
        if r.status_code in (401, 403):
            raise RuntimeError(f"403 from web GET {path}: {r.text[:300]}")
        r.raise_for_status()
        ct = r.headers.get("content-type", "")
        if "json" in ct:
            return r.json()
        raise RuntimeError(
            f"web GET {path} returned HTML (SSR), not JSON — use browser fallback"
        )


async def _get(path: str, params: dict = None, extra_headers: dict = None) -> Any:
    """Authenticated GET to api.zomato.com mobile gateway with auto 401-retry."""
    url = f"{API_BASE}{path}"

    async def _call():
        async with _mobile_session() as s:
            hdrs = {**(extra_headers or {})}
            r = await s.get(url, params=params or {}, headers=hdrs)
            if r.status_code in (401, 403):
                raise RuntimeError(f"401 from GET {path}: {r.text[:200]}")
            r.raise_for_status()
            return r.json()

    return await with_auth_retry(_call)


async def _post(path: str, json_body: dict = None, form_body: dict = None,
                base: str = None, extra_headers: dict = None) -> Any:
    """
    Authenticated POST to api.zomato.com mobile gateway with auto 401-retry.

    json_body  → sends as application/json (cart/order mutations)
    form_body  → sends as application/x-www-form-urlencoded (auth calls)
    """
    url = f"{base or API_BASE}{path}"

    async def _call():
        async with _mobile_session() as s:
            hdrs = dict(extra_headers or {})
            if form_body is not None:
                r = await s.post(url, data=form_body, headers=hdrs)
            else:
                hdrs.setdefault("Content-Type", "application/json")
                r = await s.post(url, json=json_body or {}, headers=hdrs)
            if r.status_code in (401, 403):
                raise RuntimeError(f"401 from POST {path}: {r.text[:200]}")
            r.raise_for_status()
            return r.json()

    return await with_auth_retry(_call)


# ── Auth endpoints ────────────────────────────────────────────────────────────

def _normalize_phone(phone: str) -> str:
    """
    Return the bare national number for Zomato's login endpoint.

    accounts.zomato.com/login/phone expects just the 10-digit national number
    with no country code prefix.  Examples:
        +919408839111  → 9408839111
        919408839111   → 9408839111  (12-digit starting with 91)
        9408839111     → 9408839111  (already bare)
    """
    p = phone.strip().replace("-", "").replace(" ", "")
    if p.startswith("+91"):
        return p[3:]
    if p.startswith("91") and len(p) == 12:
        return p[2:]
    return p


async def api_login_start(phone: str, country_id: str = "1", otp_pref: str = "sms") -> dict:
    """
    Initiate OTP login via Zomato's phone-number flow.

    Flow (matches what the jomato project confirmed working):
      Step 1: GET www.zomato.com/login  → initialises PHP/CSRF session cookies
      Step 2: POST accounts.zomato.com/login/phone  type=initiate  → OTP sent

    The session object (_pkce_session) is kept alive across the two MCP tool calls
    so api_login_verify() can reuse the same cookie jar for the verify POST.

    otp_pref: "sms" | "whatsapp" | "call"
    Returns the raw Zomato response dict (status, message, etc.).
    """
    global _pkce_session, _login_message_uuid, _login_web_hdrs_post

    # ── Clean up any prior session ────────────────────────────────────────────
    if _pkce_session is not None:
        try:
            await _pkce_session.close()
        except Exception:
            pass
        _pkce_session = None
    _login_message_uuid = ""
    _login_web_hdrs_post = {}

    bare = _normalize_phone(phone)

    # Create a persistent session kept alive between start and verify
    _pkce_session = AsyncSession(impersonate="chrome120", timeout=TIMEOUT)

    # ── Web (browser-style) headers for the ENTIRE OTP flow ────────────────────
    # CRITICAL: Using mobile headers (X-Zomato-API-Key, X-Zomato-Client-Id) for the
    # OTP POST triggers Zomato's OAUTH path (is_oauth_enabled: true). This path
    # requires a pre-established PKCE session — without it, /login/phone type=verify
    # returns {"status":false,"redirect_to":"zoauth/nf"} every time.
    #
    # Using browser/web headers routes through the simpler WEB login path
    # (is_oauth_enabled: false), which sets the zat cookie on success without
    # requiring any OAuth session context.
    web_hdrs_get = {
        "User-Agent": (
            "Mozilla/5.0 (Linux; Android 10; Android SDK built for x86_64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/91.0.4472.120 Mobile Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }
    web_hdrs_post = {
        **web_hdrs_get,
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept":        "application/json, text/plain, */*",
        "Origin":        ACCOUNTS_BASE,
        "Referer":       f"{WEB_BASE}/login",
    }

    try:
        # ── Step 1: Visit login page to seed the session cookie jar ──────────
        # Seeds PHPSESSID + CSRF cookies.  Without these, the OTP verify may
        # reject the session.
        try:
            r_page = await _pkce_session.get(
                f"{WEB_BASE}/login",
                headers=web_hdrs_get,
                allow_redirects=True,
            )
            log.debug("login page seed: status=%d cookies=%d",
                      r_page.status_code,
                      len(list(_pkce_session.cookies.jar)))
        except Exception as seed_exc:
            log.warning("login page seed failed (continuing anyway): %s", seed_exc)

        # ── Step 2: Send OTP using WEB headers (not mobile) ──────────────────
        payload = {
            "number":            bare,
            "country_id":        country_id,
            "lc":                "en",
            "type":              "initiate",
            "verification_type": otp_pref,
            "package_name":      "com.application.zomato",
            "message_uuid":      "",
        }
        r_otp = await _pkce_session.post(
            f"{ACCOUNTS_BASE}/login/phone",
            data=payload,
            headers=web_hdrs_post,
        )
        log.debug("send_otp: status=%d body=%s", r_otp.status_code, r_otp.text[:300])

        if not r_otp.ok:
            raise RuntimeError(
                f"login_start HTTP {r_otp.status_code}: {r_otp.text[:400]}"
            )

        result = r_otp.json()
        log.info("api_login_start: OTP initiate status=%s is_oauth_enabled=%s keys=%s",
                 result.get("status"), result.get("is_oauth_enabled"), list(result.keys()))

        # Persist state needed by api_login_verify()
        _login_message_uuid = result.get("message_uuid", "")
        _login_web_hdrs_post = web_hdrs_post   # reuse exact same headers for verify

        return result

    except Exception:
        # Clean up session on failure so the caller can retry cleanly
        try:
            await _pkce_session.close()
        except Exception:
            pass
        _pkce_session = None
        _login_message_uuid = ""
        _login_web_hdrs_post = {}
        raise


async def api_login_verify(phone: str, otp: str, country_id: str = "1") -> dict:
    """
    Submit the OTP and extract the Zomato access token (zat).

    After the verify POST succeeds:
      • Zomato sets a `zat` cookie in the session → stored as the access token
      • If redirect_to is provided, we follow it (may set more cookies / return token)
      • Token is persisted to Windows Credential Manager via store_token()

    The stored `zat` value is used as X-Zomato-Access-Token for both:
      - www.zomato.com/webroutes/* (web cookie auth)
      - api.zomato.com/gw/*        (mobile gateway — accepts the same token)

    Requires api_login_start() to have been called first in the same process.
    """
    global _pkce_session, _login_message_uuid, _login_web_hdrs_post

    if _pkce_session is None:
        raise RuntimeError(
            "No active login session. Call zomato_login_start(phone) first. "
            "The session expires if the MCP server was restarted between calls."
        )

    bare = _normalize_phone(phone)

    # Use the SAME web headers that were used for initiate — same session context
    hdrs = _login_web_hdrs_post or {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept":        "application/json, text/plain, */*",
        "User-Agent": (
            "Mozilla/5.0 (Linux; Android 10; Android SDK built for x86_64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/91.0.4472.120 Mobile Safari/537.36"
        ),
    }

    try:
        # ── Submit OTP ────────────────────────────────────────────────────────
        # Include message_uuid so the server can find the OTP session
        payload = {
            "number":            bare,
            "country_id":        country_id,
            "otp":               str(otp).strip(),
            "type":              "verify",
            "trust_this_device": "true",
            "package_name":      "com.application.zomato",
            "message_uuid":      _login_message_uuid,
        }
        r_verify = await _pkce_session.post(
            f"{ACCOUNTS_BASE}/login/phone",
            data=payload,
            headers=hdrs,
        )
        log.debug("submit_otp: status=%d body=%s",
                  r_verify.status_code, r_verify.text[:400])

        if not r_verify.ok:
            raise RuntimeError(
                f"login_verify HTTP {r_verify.status_code}: {r_verify.text[:400]}"
            )

        verify_result: dict = r_verify.json()
        log.info("api_login_verify: OTP verify status=%s keys=%s",
                 verify_result.get("status"), list(verify_result.keys()))

        # ── Extract token (1): check session cookies ──────────────────────────
        token = _extract_token_from_session(_pkce_session)
        log.debug("token from cookies: %s", "found" if token else "not found")

        # ── Extract token (2): check response body ────────────────────────────
        if not token:
            token = (
                verify_result.get("access_token")
                or verify_result.get("zat")
                or verify_result.get("token")
                or verify_result.get("auth_token")
                or (verify_result.get("auth") or {}).get("access_token")
                or (verify_result.get("data") or {}).get("access_token")
                or (verify_result.get("user") or {}).get("access_token")
            )
            if token:
                log.debug("token from response body")

        # ── Follow redirect_to if provided (may set more cookies) ─────────────
        redirect_to = verify_result.get("redirect_to", "")
        if redirect_to and redirect_to.startswith("http"):
            try:
                r_redir = await _pkce_session.get(
                    redirect_to, headers=mobile_hdrs, allow_redirects=True
                )
                log.debug("redirect_to GET: status=%d url=%s",
                          r_redir.status_code, str(getattr(r_redir, "url", ""))[:100])

                # Re-check cookies after redirect (login success often sets zat here)
                if not token:
                    token = _extract_token_from_session(_pkce_session)
                if not token:
                    try:
                        redir_body = r_redir.json()
                        token = (
                            redir_body.get("access_token")
                            or redir_body.get("zat")
                            or redir_body.get("token")
                        )
                    except Exception:
                        pass
            except Exception as redir_exc:
                log.debug("redirect_to follow failed (non-fatal): %s", redir_exc)

        # ── Store token and cookies ───────────────────────────────────────────
        if token:
            try:
                store_token(token)
                log.info("api_login_verify: access token stored in Credential Manager")
            except Exception as te:
                log.warning("store_token failed: %s", te)
            _try_store_session_cookies(_pkce_session)
        else:
            log.warning("api_login_verify: no token found — OTP may have been wrong or session expired")

        return {
            **verify_result,
            "access_token":    token,
            "oauth_completed": bool(token),
        }

    finally:
        # Session is single-use — always clean up
        try:
            await _pkce_session.close()
        except Exception:
            pass
        _pkce_session = None
        _login_message_uuid = ""
        _login_web_hdrs_post = {}


# ── Login flow helpers ────────────────────────────────────────────────────────

def _extract_token_from_session(session) -> Optional[str]:
    """
    Find the Zomato access token in the curl_cffi session cookie jar.

    After a successful OTP verify, Zomato sets the `zat` cookie on
    .zomato.com (or accounts.zomato.com). We check priority names first,
    then fall back to any cookie with 'access' or 'token' in its name.
    """
    priority = {"zat", "x-access-token", "zomato_access_token", "_zat"}
    try:
        jar = list(session.cookies.jar)
    except Exception:
        return None

    # Priority pass
    for ck in jar:
        name_l = ck.name.lower()
        domain = ck.domain or ""
        if name_l in priority and "zomato.com" in domain:
            return ck.value

    # Broader fallback (any zomato.com cookie with access/token in the name)
    for ck in jar:
        name_l = ck.name.lower()
        domain = ck.domain or ""
        if "zomato.com" in domain:
            if "access" in name_l or ("token" in name_l and "csrf" not in name_l):
                return ck.value

    return None


def _try_store_session_cookies(session) -> None:
    """Best-effort: capture zat + essential cookies from session jar for webroutes."""
    try:
        cookie_list = []
        priority = {"zat", "phpsessid", "csrf", "cid", "fbcity", "rd", "zl"}
        for ck in session.cookies.jar:
            name_l = ck.name.lower()
            if name_l in priority or name_l.startswith("zrt_"):
                cookie_list.append({
                    "name":   ck.name,
                    "value":  ck.value,
                    "domain": ck.domain or ".zomato.com",
                    "path":   ck.path or "/",
                })
        if cookie_list:
            store_cookies(cookie_list)
            log.info("Captured %d session cookies for web routes", len(cookie_list))
    except Exception as ce:
        log.debug("_try_store_session_cookies: %s", ce)


# ── Discovery endpoints ───────────────────────────────────────────────────────

async def api_search(query: str, lat: float, lon: float, limit: int = 10) -> dict:
    """
    Search restaurants via the mobile gateway.

    Requires an Android OAuth access_token (from PKCE OTP login).
    Falls back to trying the web search endpoint if the mobile gateway fails.

    Raises RuntimeError with a user-friendly message when no valid token is stored.
    """
    loc = _loc_headers(lat, lon)

    # Primary: mobile gateway
    try:
        return await _get(
            "/gw/tabbed-home",
            params={"q": query, "limit": limit},
            extra_headers=loc,
        )
    except Exception as gw_exc:
        log.debug("mobile search failed (%s) — trying web search", gw_exc)

    # Fallback: web search
    try:
        return await _web_get(
            "/webroutes/search/results",
            params={"q": query, "lat": lat, "lon": lon, "count": limit},
        )
    except Exception as web_exc:
        raise RuntimeError(
            f"Restaurant search failed. Mobile gateway: {gw_exc}. Web: {web_exc}. "
            "Ensure you have called zomato_login_start + zomato_login_verify to obtain "
            "the Android OAuth token, or that the web session cookie is still valid."
        ) from web_exc


def _slug_from_name_locality(name: str, locality: str) -> str:
    """Derive the Zomato URL slug from a restaurant name + locality string."""
    import re
    area = locality.split(",")[0].strip()
    combined = f"{name} {area}".lower()
    slug = re.sub(r"[^a-z0-9]+", "-", combined).strip("-")
    return slug


async def _get_restaurant_page_url(res_id: str) -> Optional[str]:
    """
    Look up a restaurant's web URL path from its numeric res_id using
    /webroutes/restaurant/info.  Returns a path like
    '/ahmedabad/chinese-wok-express-bodakdev/order' or None on failure.
    """
    try:
        r = await _web_get("/webroutes/restaurant/info", params={"res_id": res_id})
        sections = r.get("page_data", {}).get("sections", {})
        basic    = sections.get("SECTION_BASIC_INFO", {})
        header   = sections.get("SECTION_RES_HEADER_DETAILS", {})

        name     = basic.get("name", "")
        locality_obj = header.get("LOCALITY", {})
        locality_url = locality_obj.get("url", "")
        parsed_path = urlparse(locality_url).path   # "/ahmedabad/bodakdev-restaurants"
        parts = [p for p in parsed_path.split("/") if p]
        city_slug = parts[0] if parts else os.getenv("ZOMATO_DEFAULT_CITY_SLUG", "ahmedabad")

        locality_text = locality_obj.get("text", "")
        if not name:
            log.debug("_get_restaurant_page_url: empty name for res_id=%s", res_id)
            return None
        slug = _slug_from_name_locality(name, locality_text)
        return f"/{city_slug}/{slug}/order"
    except Exception as exc:
        log.debug("_get_restaurant_page_url failed for res_id=%s: %s", res_id, exc)
        return None


async def api_restaurant_menu_listing(res_id: str) -> dict:
    """
    Fetch the menu for a restaurant.

    Strategy:
      1. Resolve res_id → URL slug via /webroutes/restaurant/info
      2. Fetch menu via /webroutes/getPage?page_url=/{city}/{slug}/order
      3. Extract page_data.order.menuList (confirmed structure)
    Falls back to the mobile gateway (/gw/restaurant/menu) if slug lookup fails.
    """
    page_path = await _get_restaurant_page_url(res_id)
    if page_path:
        try:
            r = await _web_get("/webroutes/getPage", params={"page_url": page_path})
            order = r.get("page_data", {}).get("order", {})
            if order.get("menuList"):
                return {
                    "res_id": res_id,
                    "menu":   order["menuList"],
                    "source": "webroutes_getpage",
                }
        except Exception as exc:
            log.debug("menu via getPage %s failed: %s", page_path, exc)

    # Fallback: mobile gateway (works if user has done OTP login)
    try:
        return await _get("/gw/restaurant/menu",
                          params={"res_id": res_id, "type": "listing"})
    except Exception as exc:
        raise RuntimeError(
            f"Could not fetch menu for res_id={res_id}: {exc}. "
            "Call zomato_login_start to obtain the Android API token."
        ) from exc


async def api_restaurant_menu_by_category(res_id: str, category: Optional[str] = None) -> dict:
    """
    Fetch the full menu (with variants and add-ons) for a restaurant.
    Same resolution strategy as api_restaurant_menu_listing.
    """
    page_path = await _get_restaurant_page_url(res_id)
    if page_path:
        try:
            r = await _web_get("/webroutes/getPage", params={"page_url": page_path})
            order = r.get("page_data", {}).get("order", {})
            if order.get("menuList"):
                menus = order["menuList"].get("menus", [])
                if category:
                    menus = [
                        m for m in menus
                        if category.lower() in m.get("menu", {}).get("name", "").lower()
                    ]
                return {
                    "res_id":   res_id,
                    "category": category,
                    "menus":    menus,
                    "source":   "webroutes_getpage",
                }
        except Exception as exc:
            log.debug("menu by category via getPage %s failed: %s", page_path, exc)

    # Fallback: mobile gateway
    try:
        params: dict = {"res_id": res_id, "type": "full"}
        if category:
            import urllib.parse
            params["category"] = urllib.parse.quote(str(category))
        return await _get("/gw/restaurant/menu", params=params)
    except Exception as exc:
        raise RuntimeError(
            f"Could not fetch menu for res_id={res_id}: {exc}. "
            "Call zomato_login_start to obtain the Android API token."
        ) from exc


# ── Address endpoints ─────────────────────────────────────────────────────────

async def api_get_addresses() -> dict:
    """
    Fetch saved delivery addresses.
    Confirmed working endpoint: /webroutes/user/address (cookie auth).
    """
    return await _web_get("/webroutes/user/address")


# ── Cart endpoints (mobile gateway — requires Android OAuth token) ─────────────

async def api_create_cart(restaurant_id: str, address_id: str) -> dict:
    """Create a new cart for the given restaurant and delivery address."""
    return await _post("/gw/cart/create", json_body={
        "res_id":     restaurant_id,
        "address_id": address_id,
    })


async def api_add_to_cart(cart_id: str, item_id: str, quantity: int = 1,
                           variant_ids: Optional[list] = None,
                           addon_ids: Optional[list] = None) -> dict:
    """Add an item to an existing cart."""
    body: dict = {
        "cart_id":  cart_id,
        "item_id":  item_id,
        "quantity": quantity,
    }
    if variant_ids:
        body["variant_ids"] = variant_ids
    if addon_ids:
        body["addon_ids"] = addon_ids
    return await _post("/gw/cart/add", json_body=body)


async def api_view_cart(cart_id: str) -> dict:
    return await _get("/gw/cart", params={"cart_id": cart_id})


async def api_remove_from_cart(cart_id: str, item_id: str) -> dict:
    return await _post("/gw/cart/remove", json_body={"cart_id": cart_id, "item_id": item_id})


async def api_get_cart_offers(cart_id: str) -> dict:
    """Fetch applicable promo codes for the cart."""
    return await _get("/gw/cart/offers", params={"cart_id": cart_id})


# ── Checkout & order endpoints ────────────────────────────────────────────────

async def api_checkout(cart_id: str, payment_method: str = "pay_later") -> dict:
    """
    Initiate checkout.

    payment_method options:
      "pay_later" — post-pay (no card/UPI details needed; default)
      "upi_qr"    — returns a QR code PNG URL the user scans
      "card"      — use browser fallback (zomato_browser.browser_pay_card)
    """
    return await _post("/gw/order/checkout", json_body={
        "cart_id":        cart_id,
        "payment_method": payment_method,
    })


async def api_track_order(order_id: str) -> dict:
    """
    Fetch order details/tracking.

    Strategy:
      1. Try mobile gateway /v2/order/crystal_v2 — works for live/active orders
      2. Fallback to /webroutes/user/orders entity call — works for all orders.
    """
    # Rung 1: mobile gateway (live orders only)
    try:
        return await _get("/v2/order/crystal_v2", params={"order_id": order_id})
    except Exception as mobile_exc:
        log.debug("api_track_order: mobile gateway failed for %s (%s) — trying web", order_id, mobile_exc)

    # Rung 2: web entity endpoint (completed + live orders)
    return await api_order_details_web(order_id)


async def api_order_history(limit: int = 10) -> dict:
    """
    Fetch past orders with full details (restaurant, items, amount, status).

    Two-call strategy (both calls use cookie auth on www.zomato.com):
      Call 1: GET /webroutes/user/orders?page=1&n=10
              → returns paginated list of order IDs only
      Call 2: GET /webroutes/user/orders?order_id=X&order_id=Y&…
              → returns entities.ORDER.<id> with full order card data
    """
    page_size    = min(limit, 10)
    pages_needed = max(1, (limit + page_size - 1) // page_size)
    all_ids: list = []
    r: dict = {}

    # ── Call 1: collect order IDs ─────────────────────────────────────────────
    for pg in range(1, pages_needed + 1):
        r = await _web_get("/webroutes/user/orders", params={"page": pg, "n": page_size})
        hist = r.get("sections", {}).get("SECTION_USER_ORDER_HISTORY", {})
        for entity in hist.get("entities", []):
            if entity.get("entity_type") == "ORDER":
                all_ids.extend(entity.get("entity_ids", []))
        total_pages = hist.get("totalPages") or 1
        if pg >= total_pages:
            break

    total_reported = (
        r.get("sections", {})
         .get("SECTION_USER_ORDER_HISTORY", {})
         .get("count", len(all_ids))
    )
    trimmed_ids = all_ids[:limit]

    # ── Call 2: batch-fetch full details for all collected IDs ────────────────
    order_details: list = []
    if trimmed_ids:
        try:
            params = [("order_id", str(oid)) for oid in trimmed_ids]
            detail_r = await _web_get_multi("/webroutes/user/orders", params=params)
            orders_map: dict = detail_r.get("entities", {}).get("ORDER", {})
            for oid in trimmed_ids:
                od = orders_map.get(str(oid)) or orders_map.get(oid)
                if od:
                    order_details.append({
                        "order_id":        od.get("orderId"),
                        "date":            od.get("orderDate"),
                        "restaurant":      od.get("resInfo", {}).get("name"),
                        "restaurant_url":  od.get("resInfo", {}).get("resUrl"),
                        "items":           od.get("dishString"),
                        "total":           od.get("totalCost"),
                        "status":          od.get("deliveryDetails", {}).get("deliveryLabel"),
                        "delivery_msg":    od.get("deliveryDetails", {}).get("deliveryMessage"),
                        "address":         od.get("deliveryDetails", {}).get("deliveryAddress"),
                        "rating":          od.get("rating"),
                        "can_reorder":     od.get("showReorderButton", False),
                    })
                else:
                    order_details.append({"order_id": oid, "note": "details not available"})
        except Exception as exc:
            log.warning("batch order detail fetch failed (%s) — returning IDs only", exc)
            order_details = [{"order_id": oid} for oid in trimmed_ids]

    return {
        "orders":       order_details,
        "total_orders": total_reported,
    }


async def api_order_details_web(order_id: str) -> dict:
    """
    Fetch full details for a single past order via the web entity endpoint.
    Works for completed orders (unlike the mobile gateway which only handles live orders).
    """
    detail_r = await _web_get_multi(
        "/webroutes/user/orders", params=[("order_id", str(order_id))]
    )
    orders_map: dict = detail_r.get("entities", {}).get("ORDER", {})
    od = orders_map.get(str(order_id)) or orders_map.get(order_id)
    if od:
        return od
    return {"order_id": order_id, "raw": detail_r,
            "note": "Order ID not found in entity map — may be too old or ID is wrong."}


async def api_order_details(order_id: str) -> dict:
    """Alias for api_track_order — same endpoint, used for reorder context."""
    return await api_track_order(order_id)
