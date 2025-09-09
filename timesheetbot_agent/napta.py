# timesheetbot_agent/napta.py
from __future__ import annotations

import time
from pathlib import Path
from typing import Tuple, Optional

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError
import browser_cookie3

DEFAULT_APP_URL = "https://app.napta.io/timesheet"
# Where we persist your Napta session (cookies + localStorage) after one-time login
STORAGE_DIR = Path.home() / ".timesheetbot"
STORAGE_DIR.mkdir(parents=True, exist_ok=True)
STORAGE_STATE_PATH = STORAGE_DIR / "napta_storage.json"


class NaptaAuthError(RuntimeError):
    """Raised when SSO cookies are missing/expired."""
    pass


class NaptaClient:
    """
    Reuses your session to automate Napta.

    One-time:
      - device_login()  -> opens a headed browser, you log in once, we save storage_state

    Everyday (fast, no keychain):
      - save_current_week()
      - submit_current_week()
      - save_and_submit_current_week()

    Legacy no-ops (iso_week arg ignored, kept for CLI compatibility):
      - preview_week(iso_week, *, leave_details=None)
      - save_week(iso_week, *, leave_details=None)
      - submit_week(iso_week)
    """

    def __init__(self) -> None:
        self._cookie_ok: Optional[bool] = None

    # ---------- cookies ----------
    @staticmethod
    def _load_cookies_for_playwright(context) -> None:
        """Legacy fallback: read *.napta.io cookies from Chromium browsers (may trigger Keychain on macOS)."""
        jars = []
        for getter in (getattr(browser_cookie3, "chrome", None),
                       getattr(browser_cookie3, "edge", None),
                       getattr(browser_cookie3, "brave", None)):
            if not getter:
                continue
            try:
                cj = getter(domain_name=".napta.io")
                if cj:
                    jars.append(cj)
            except Exception:
                pass

        cookies = []
        now = time.time()

        for jar in jars:
            for c in jar:
                if "napta.io" not in getattr(c, "domain", ""):
                    continue
                exp = getattr(c, "expires", None)
                if exp not in (None, 0) and exp < now:
                    continue

                rest = getattr(c, "rest", None) or getattr(c, "_rest", None) or {}
                http_only = bool(
                    (hasattr(rest, "get") and rest.get("HttpOnly"))
                    or rest.get("httponly")
                )

                ck = {
                    "name": c.name,
                    "value": c.value,
                    "domain": c.domain,
                    "path": c.path or "/",
                    "secure": bool(getattr(c, "secure", False)),
                    "httpOnly": http_only,
                }
                if exp not in (None, 0):
                    ck["expires"] = int(exp)

                cookies.append(ck)

        if not cookies:
            raise NaptaAuthError(
                "No Napta cookies found. Run `login` once (recommended), or open https://app.napta.io, "
                "log in with SSO, then retry."
            )

        # Batch add (robust to single bad cookie)
        batch: list[dict] = []
        for ck in cookies:
            batch.append(ck)
            if len(batch) >= 50:
                context.add_cookies(batch)
                batch = []
        if batch:
            context.add_cookies(batch)

    # ---------- helpers ----------
    def status(self) -> str:
        if self._cookie_ok is None:
            return "Auth: will use stored session if available; else your browser’s SSO cookies."
        return "Auth: OK (stored session)." if self._cookie_ok else \
               "Auth: missing/expired session. Please run `login` once."

    def _new_context(self):
        """
        Prefer fast, keychain-free stored session (storage_state).
        Fallback to decrypting browser cookies if storage_state is missing.
        """
        p = sync_playwright().start()
        browser = p.chromium.launch(headless=True)
        kwargs = {"viewport": {"width": 1600, "height": 1000}}
        ctx = None

        if STORAGE_STATE_PATH.exists():
            # Fast path: reuse stored session (no keychain)
            ctx = browser.new_context(storage_state=str(STORAGE_STATE_PATH), **kwargs)
            self._cookie_ok = True
        else:
            # Fallback: load cookies from Chrome/Edge/Brave (may trigger macOS Keychain once)
            ctx = browser.new_context(**kwargs)
            try:
                self._load_cookies_for_playwright(ctx)
                self._cookie_ok = True
            except NaptaAuthError:
                self._cookie_ok = False
                browser.close()
                p.stop()
                raise

        # Reasonable defaults
        ctx.set_default_timeout(15000)
        ctx.set_default_navigation_timeout(90000)
        return p, browser, ctx

    def _wait_for_timesheet_ui(self, page) -> None:
        """Wait for a stable UI element without using 'networkidle'."""
        selectors = [
            'button:has-text("Save")',
            '[data-cy="PeriodNavigation_navRight"]',
            '[data-cy="PeriodNavigation_navLeft"]',
            'button:has-text("This week")',
        ]
        for sel in selectors:
            try:
                page.locator(sel).first.wait_for(state="visible", timeout=20000)
                return
            except Exception:
                continue
        try:
            page.wait_for_load_state("domcontentloaded", timeout=5000)
        except Exception:
            pass

    def _open_timesheet(self, page):
        # Avoid 'networkidle' (Napta long-polls). Use DOM ready + targeted waits.
        try:
            page.goto(DEFAULT_APP_URL, wait_until="domcontentloaded", timeout=60000)
        except Exception:
            page.goto(DEFAULT_APP_URL, timeout=60000)

        # Allow SSO redirect with stored cookies
        try:
            page.wait_for_load_state("load", timeout=10000)
        except Exception:
            pass

        # Normalize to “This week” if available
        try:
            page.get_by_role("button", name="This week").click(timeout=1200)
        except Exception:
            pass

        self._wait_for_timesheet_ui(page)

    def _robust_click_button(self, page, label: str) -> None:
        strategies = [
            lambda: page.get_by_role("button", name=label),
            lambda: page.locator(f"button:has-text('{label}')"),
            lambda: page.locator(f"xpath=//button[normalize-space()='{label}']"),
            lambda: page.locator(
                f"xpath=//*[normalize-space(text())='{label}']/ancestor::button[1]"
            ),
        ]
        for make in strategies:
            try:
                loc = make().first
                loc.wait_for(state="visible", timeout=2500)
                try:
                    loc.scroll_into_view_if_needed(timeout=500)
                except Exception:
                    pass
                loc.click(timeout=2500)
                return
            except Exception:
                continue

        clicked = page.evaluate(
            """(txt) => {
                const visible = el => !!el && el.offsetParent !== null && getComputedStyle(el).visibility !== 'hidden';
                const nodes = Array.from(document.querySelectorAll('button,[role="button"]')).filter(visible);
                const el = nodes.find(n => (n.textContent||'').trim() === txt);
                if (el) { el.click(); return true; }
                return false;
            }""",
            label,
        )
        if not clicked:
            raise PWTimeoutError(f"Could not find a clickable '{label}' button")

    def _saw_badge(self, page, text: str, tries: int = 24, sleep_s: float = 0.25) -> bool:
        for _ in range(tries):
            try:
                loc = page.locator(f"text={text}").first
                if loc.count() and loc.is_visible():
                    return True
            except Exception:
                pass
            time.sleep(sleep_s)
        return False

    # ---------- one-time, fast path creator ----------
    def device_login(self) -> Tuple[bool, str]:
        """
        Open a headed browser so you can log in once.
        We save the session to STORAGE_STATE_PATH and reuse it headlessly next time.
        """
        p = sync_playwright().start()
        browser = p.chromium.launch(headless=False, args=["--disable-dev-shm-usage"])
        ctx = browser.new_context(viewport={"width": 1600, "height": 1000})
        ctx.set_default_navigation_timeout(180000)
        page = ctx.new_page()
        page.goto(DEFAULT_APP_URL, wait_until="load", timeout=120000)

        # Tell the user to complete the login; we wait for the Save button to appear.
        ok = False
        err = None
        try:
            page.locator('button:has-text("Save")').first.wait_for(state="visible", timeout=180000)
            ok = True
        except Exception as e:
            err = e

        try:
            # Save cookies + localStorage for next time (fast + no keychain)
            ctx.storage_state(path=str(STORAGE_STATE_PATH))
        except Exception:
            pass

        try:
            browser.close()
        finally:
            p.stop()

        if ok:
            self._cookie_ok = True
            return True, f"✅ Login captured. Stored session at {STORAGE_STATE_PATH}. Future runs will be fast and won’t prompt for Keychain."
        return False, f"❌ Couldn’t detect a successful login (Save button not visible). Error: {err}"

    # ---------- public API (current week only) ----------
    def save_current_week(self) -> Tuple[bool, str]:
        try:
            p, browser, ctx = self._new_context()
        except NaptaAuthError as e:
            return False, f"❌ {e} Tip: run `login` once to store a session."

        try:
            page = ctx.new_page()
            try:
                self._open_timesheet(page)
            except Exception as e:
                return False, f"❌ Could not open Napta Timesheet (timeout). Open https://app.napta.io once and retry. ({e})"

            try:
                self._robust_click_button(page, "Save")
            except Exception as e:
                return False, f"❌ Could not click 'Save' ({e}). Maybe already saved."

            badge = self._saw_badge(page, "Saved")
            return True, "✅ Saved (draft)." if badge else "✅ Save clicked."
        finally:
            try:
                browser.close()
            finally:
                p.stop()

    def submit_current_week(self) -> Tuple[bool, str]:
        try:
            p, browser, ctx = self._new_context()
        except NaptaAuthError as e:
            return False, f"❌ {e} Tip: run `login` once to store a session."

        try:
            page = ctx.new_page()
            try:
                self._open_timesheet(page)
            except Exception as e:
                return False, f"❌ Could not open Napta Timesheet (timeout). Open https://app.napta.io once and retry. ({e})"

            try:
                self._robust_click_button(page, "Submit for approval")
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
                browser.close()
            finally:
                p.stop()

    def save_and_submit_current_week(self) -> Tuple[bool, str]:
        ok, msg1 = self.save_current_week()
        if not ok:
            return False, msg1
        ok2, msg2 = self.submit_current_week()
        if not ok2:
            return False, f"{msg1}\n{msg2}"
        return True, f"{msg1}\n{msg2}"

    # ---------- legacy no-ops ----------
    def preview_week(self, iso_week: str, *, leave_details=None):
        return True, "(preview) Using current week; nothing to preview.", None

    def save_week(self, iso_week: str, *, leave_details=None):
        return self.save_current_week()

    def submit_week(self, iso_week: str):
        return self.submit_current_week()
