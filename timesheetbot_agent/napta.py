# timesheetbot_agent/napta.py
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Tuple, Optional, Iterable, Dict, Any

from playwright.sync_api import (
    sync_playwright,
    TimeoutError as PWTimeoutError,
    Error as PWError,
)
import browser_cookie3

DEFAULT_APP_URL = "https://app.napta.io/timesheet"

_CACHE_DIR = Path(os.path.expanduser("~/.cache/timesheetbot"))
_CACHE_DIR.mkdir(parents=True, exist_ok=True)
_COOKIE_CACHE = _CACHE_DIR / "napta_cookies.json"

_ANALYTICS_HOSTS = (
    "googletagmanager.com",
    "google-analytics.com",
    "segment.io",
    "sentry.io",
    "plausible.io",
    "fullstory.com",
    "intercom.io",
    "hotjar.com",
)

class NaptaAuthError(RuntimeError):
    """Raised when SSO cookies are missing/expired."""
    pass


class NaptaClient:
    """
    Fast Napta automation that reuses your browser's SSO cookies.
    - Reuses a single Playwright/browser/context across commands.
    - Blocks heavy assets.
    - Uses DOMContentLoaded & tight timeouts for speed.
    """

    def __init__(self) -> None:
        self._cookie_ok: Optional[bool] = None
        self._p = None
        self._browser = None
        self._ctx = None

    # ---------- lifecycle ----------

    def _ensure_running(self):
        """Start Playwright + browser + context if not running."""
        if self._ctx:
            return

        self._p = sync_playwright().start()
        # headless browser; keep minimal footprint
        self._browser = self._p.chromium.launch(headless=True)
        self._ctx = self._browser.new_context(
            viewport={"width": 1440, "height": 900},
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/122.0 Safari/537.36",
        )
        self._ctx.set_default_timeout(7000)  # tight default timeouts

        # Block heavy/unnecessary stuff to speed up page
        def _route(route):
            req = route.request
            rtype = req.resource_type
            url = req.url
            if rtype in ("image", "media", "font"):
                return route.abort()
            if any(host in url for host in _ANALYTICS_HOSTS):
                return route.abort()
            return route.continue_()
        self._ctx.route("**/*", _route)

        # Load cookies (cache first, then keychain)
        try:
            if not self._load_cookies_from_cache():
                self._load_cookies_from_keychain_and_cache()
            self._cookie_ok = True
        except NaptaAuthError:
            self._cookie_ok = False
            self.close()
            raise

    def close(self):
        try:
            if self._browser:
                self._browser.close()
        finally:
            try:
                if self._p:
                    self._p.stop()
            finally:
                self._p = self._browser = self._ctx = None

    # ---------- cookies ----------

    def status(self) -> str:
        if self._cookie_ok is None:
            return "Auth: will use your browser’s SSO cookies (not checked yet)."
        return "Auth: OK (browser SSO cookies)." if self._cookie_ok else \
               "Auth: missing/expired cookies. Please login to Napta once in your browser."

    def _load_cookies_for_playwright(self, cookies: Iterable[dict]) -> None:
        batch: list[dict] = []
        for ck in cookies:
            batch.append(ck)
            if len(batch) >= 100:
                self._ctx.add_cookies(batch)
                batch = []
        if batch:
            self._ctx.add_cookies(batch)

    def _load_cookies_from_cache(self) -> bool:
        if not _COOKIE_CACHE.exists():
            return False
        try:
            data = json.loads(_COOKIE_CACHE.read_text())
        except Exception:
            return False

        now = time.time()
        cookies = []
        for c in data:
            exp = c.get("expires", None)
            if exp in (None, 0) or exp > now:
                cookies.append(c)
        if not cookies:
            return False

        self._load_cookies_for_playwright(cookies)
        return True

    def _load_cookies_from_keychain_and_cache(self) -> None:
        # Pull once from Chrome keychain, then save to local cache
        cj = browser_cookie3.chrome(domain_name=".napta.io")
        cookies = []
        now = time.time()

        for c in cj:
            if "napta.io" not in c.domain:
                continue
            exp = getattr(c, "expires", None)
            # keep if no expiry or future expiry
            if exp not in (None, 0) and exp < now:
                continue
            cookies.append({
                "name": c.name,
                "value": c.value,
                "domain": c.domain,
                "path": c.path or "/",
                "secure": bool(getattr(c, "secure", False)),
                "httpOnly": bool(getattr(getattr(c, "_rest", {}), "get", lambda *_: False)("httponly")),
                **({"expires": int(exp)} if exp not in (None, 0) else {}),
            })

        if not cookies:
            raise NaptaAuthError(
                "No Napta cookies found. Please open https://app.napta.io in your browser, "
                "login once with SSO, then retry."
            )

        self._load_cookies_for_playwright(cookies)

        # cache for later runs (avoids Keychain prompts)
        try:
            _COOKIE_CACHE.write_text(json.dumps(cookies, indent=2))
        except Exception:
            pass

    # ---------- page helpers ----------

    def _open_timesheet(self, page):
        # domcontentloaded is enough; we'll wait for specific UI after.
        page.goto(DEFAULT_APP_URL, wait_until="domcontentloaded", timeout=12000)

        # If a stray overlay/dialog is present, try to escape quickly
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass

        # Click “This week” if the pill exists (keeps us predictable)
        try:
            page.get_by_role("button", name="This week").click(timeout=1200)
        except Exception:
            pass

    def _robust_click_button(self, page, label: str) -> None:
        # Fast strategy list; short waits for each
        strategies = [
            lambda: page.get_by_role("button", name=label),
            lambda: page.locator(f"button:has-text('{label}')"),
            lambda: page.locator(f"xpath=//button[normalize-space()='{label}']"),
            lambda: page.locator(f"xpath=//*[normalize-space(text())='{label}']/ancestor::button[1]"),
        ]
        for make in strategies:
            try:
                loc = make().first
                loc.wait_for(state="visible", timeout=1500)
                try:
                    loc.scroll_into_view_if_needed(timeout=400)
                except Exception:
                    pass
                loc.click(timeout=1500)
                return
            except Exception:
                continue

        # final JS try
        clicked = page.evaluate(
            """(txt) => {
               const ok = el => el && el.offsetParent !== null;
               const nodes = Array.from(document.querySelectorAll('button,[role="button"]'));
               const el = nodes.find(n => ok(n) && (n.textContent||'').trim() === txt);
               if (el) { el.click(); return true; }
               return false;
            }""",
            label,
        )
        if not clicked:
            raise PWTimeoutError(f"Could not find a clickable '{label}' button")

    def _saw_badge(self, page, text: str, tries: int = 10, sleep_s: float = 0.15) -> bool:
        for _ in range(tries):
            try:
                loc = page.locator(f"text={text}").first
                if loc.count() and loc.is_visible():
                    return True
            except Exception:
                pass
            time.sleep(sleep_s)
        return False

    # ---------- public API (fast paths) ----------

    def save_current_week(self) -> Tuple[bool, str]:
        try:
            self._ensure_running()
        except NaptaAuthError as e:
            return False, f"❌ {e}"

        page = self._ctx.new_page()
        try:
            self._open_timesheet(page)

            # If already submitted, don't try to save
            if self._saw_badge(page, "Pending approval") or self._saw_badge(page, "Approval pending"):
                return True, "ℹ️ Skipped Save: week already submitted (Approval pending)."

            # Quick save
            try:
                self._robust_click_button(page, "Save")
            except Exception as e:
                return False, f"❌ Could not click 'Save' ({e}). Maybe already saved."

            badge = self._saw_badge(page, "Saved")
            return True, "✅ Saved (draft)." if badge else "✅ Save clicked."
        finally:
            try:
                page.close()
            except Exception:
                pass

    def submit_current_week(self) -> Tuple[bool, str]:
        try:
            self._ensure_running()
        except NaptaAuthError as e:
            return False, f"❌ {e}"

        page = self._ctx.new_page()
        try:
            self._open_timesheet(page)

            try:
                self._robust_click_button(page, "Submit for approval")
                # Some builds show a confirm "Submit" dialog
                try:
                    self._robust_click_button(page, "Submit")
                except Exception:
                    pass
            except Exception as e:
                return False, f"❌ Could not click 'Submit for approval' ({e})."

            badge = self._saw_badge(page, "Pending approval") or self._saw_badge(page, "Approval pending")
            return True, "✅ Submitted for approval." if badge else "✅ Submit clicked."
        finally:
            try:
                page.close()
            except Exception:
                pass

    def save_and_submit_current_week(self) -> Tuple[bool, str]:
        ok, msg1 = self.save_current_week()
        if not ok:
            return False, msg1
        ok2, msg2 = self.submit_current_week()
        if not ok2:
            return False, f"{msg1}\n{msg2}"
        return True, f"{msg1}\n{msg2}"

    # Kept for CLI compatibility
    def preview_week(self, iso_week: str, *, leave_details=None):
        return True, "(preview) Using current week; nothing to preview.", None

    def save_week(self, iso_week: str, *, leave_details=None):
        return self.save_current_week()

    def submit_week(self, iso_week: str):
        return self.submit_current_week()
