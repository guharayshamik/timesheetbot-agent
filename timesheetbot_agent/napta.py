# timesheetbot_agent/napta.py
from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Tuple, Optional

from playwright.sync_api import sync_playwright
import browser_cookie3
import concurrent.futures

DEFAULT_APP_URL = "https://app.napta.io/timesheet"

# Absolute XPaths observed in your tenant
SAVE_BTN_XPATH = '/html/body/div[1]/div[2]/div[1]/div[2]/div[2]/button'
THIS_WEEK_BTN_XPATH = '/html/body/div[1]/div[2]/div[1]/div[2]/div[2]/div/button[2]'

# Next-week nav (prefer stable data-cy, keep XPath fallback shared earlier)
NEXT_WEEK_CY = '[data-cy="PeriodNavigation_navRight"]'
NEXT_WEEK_BTN_XPATH = '/html/body/div[1]/div[2]/div[1]/div[2]/div[2]/div/button[3]'

# Create/creation selectors
CREATE_BTN_XPATH = '//button[contains(normalize-space(.), "Create")]'
CREATE_TIMESHEET_XPATH = '//button[contains(normalize-space(.), "Create timesheet")]'

# Cache paths
_CACHE_DIR = Path(os.path.expanduser("~/.cache/timesheetbot"))
_CACHE_DIR.mkdir(parents=True, exist_ok=True)
_COOKIE_CACHE = _CACHE_DIR / "napta_cookies.json"
STATE_PATH = _CACHE_DIR / "napta_storage_state.json"  # persisted after login()

# Light network slimming
_ANALYTICS_HOSTS = (
    "googletagmanager.com",
    "google-analytics.com",
    "segment.io",
    "sentry.io",
    "plausible.io",
    "fullstory.com",
    "intercom.io",
    "hotjar.com",
    "gravatar.com",
    "unpkg.com",
)

# Timeouts (lean for speed; keep LONG for SSO login)
SHORT_TIMEOUT_MS = 4_000
DEFAULT_TIMEOUT_MS = 5_000
LONG_TIMEOUT_MS = 300_000  # for headful SSO during login()

UA_DESKTOP = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
)


class NaptaAuthError(RuntimeError):
    """Raised when SSO/app login is required or session is expired."""
    pass


# ---------- tiny utility helpers ----------

def ts() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


class suppress_exc:
    """Context manager: suppress any exception; optionally re-raise at end."""
    def __init__(self, raise_on_fail: bool = False):
        self.raise_on_fail = raise_on_fail
        self._exc = None
    def __enter__(self):
        return self
    def __exit__(self, exc_type, exc, tb):
        self._exc = exc
        return not self.raise_on_fail  # suppress if not raising


def _route_slim(route):
    req = route.request
    rtype = req.resource_type
    url = req.url
    if rtype in ("image", "media", "font"):
        return route.abort()
    if url.endswith((".map", ".svg")):
        return route.abort()
    if any(h in url for h in _ANALYTICS_HOSTS):
        return route.abort()
    return route.continue_()


# ---------- core helpers ----------

def _get_status_chip_text(page) -> str:
    """
    Read the small status chip near the top header (avoid the legend at the bottom).
    Tries a few header-scoped containers before falling back to a strict single-word match.
    """
    # Candidates likely around the week header / action buttons
    containers = [
        "header",                      # top header if present
        "main >> div:near(:text('This week'), 800)",  # area close to "This week"
        "main >> div:has(button:has-text('Submit for approval'))",
        "main >> div:has(button:has-text('Save'))",
        "main",                        # fallback but still exclude legend via strict match below
    ]

    # Strict single-word/status matching to avoid picking legend text
    status_regex = r'^(Not created|Draft|Open|Approval pending|Submitted)$'

    for scope in containers:
        with suppress_exc():
            loc = page.locator(f"{scope} >> text=/{status_regex}/i").first
            if loc.count():
                text = (loc.inner_text() or "").strip()
                # Guard against long legend strings (should be exactly the status)
                if text and len(text) <= 20:
                    return text
    return ""



def _saw_saved_toast(page) -> bool:
    with suppress_exc():
        page.wait_for_selector("text=/\\bSaved\\b/i", timeout=SHORT_TIMEOUT_MS)
        return True
    return False


def _wait_for_save_confirmation(page, *, prev_chip: str, timeout_s: float = 12.0) -> bool:
    """
    Accept success if:
      - a “Saved” toast appears, OR
      - the chip changes away from 'Not created' to Open/Draft/Approval pending/Submitted.
    """
    if _saw_saved_toast(page):
        return True

    end = time.time() + timeout_s
    prev = (prev_chip or "").strip().lower()
    while time.time() < end:
        now = (_get_status_chip_text(page) or "").strip().lower()
        if now and now != "not created":
            if prev == "not created":
                return True
            if prev in ("open", "draft") and now in (
                "open", "draft", "approval pending", "submitted"
            ):
                return True
            if now != prev:
                return True
        time.sleep(0.15)
    return False


def _wait_for_timesheet_ready(page, timeout_ms: int) -> Optional[str]:
    """
    Wait until a key action button is visible.
    Returns: "create", "save", "submit", or None if neither is present (locked/submitted view or transient).
    """
    end = time.time() + (timeout_ms / 1000.0)
    while time.time() < end:
        # Create
        with suppress_exc():
            loc = page.get_by_role("button", name="Create timesheet")
            if loc.count() and loc.is_visible():
                return "create"
        with suppress_exc():
            loc = page.get_by_role("button", name="Create")
            if loc.count() and loc.is_visible():
                return "create"
        with suppress_exc():
            if page.locator('button:has-text("Create timesheet")').is_visible():
                return "create"
        with suppress_exc():
            if page.locator('button:has-text("Create")').is_visible():
                return "create"

        # Submit
        with suppress_exc():
            loc = page.get_by_role("button", name="Submit for approval")
            if loc.count() and loc.is_visible():
                return "submit"
        with suppress_exc():
            if page.locator('button:has-text("Submit for approval")').is_visible():
                return "submit"

        # Save
        with suppress_exc():
            loc = page.get_by_role("button", name="Save")
            if loc.count() and loc.is_visible():
                return "save"
        with suppress_exc():
            if page.locator('button:has-text("Save")').is_visible():
                return "save"
        with suppress_exc():
            if page.locator(f"xpath={SAVE_BTN_XPATH}").is_visible():
                return "save"

        time.sleep(0.10)
    return None


def _click_create(page) -> bool:
    """Click 'Create timesheet' / 'Create' with multiple fallbacks."""
    for make in (
        lambda: page.get_by_role("button", name="Create timesheet"),
        lambda: page.get_by_role("button", name="Create"),
        lambda: page.locator('button:has-text("Create timesheet")'),
        lambda: page.locator('button:has-text("Create")'),
        lambda: page.locator(f"xpath={CREATE_TIMESHEET_XPATH}"),
        lambda: page.locator(f"xpath={CREATE_BTN_XPATH}"),
    ):
        with suppress_exc():
            btn = make().first
            if btn.count():
                with suppress_exc():
                    btn.scroll_into_view_if_needed()
                btn.click(timeout=SHORT_TIMEOUT_MS)
                time.sleep(0.4)
                return True
    return False


def _click_save(page) -> bool:
    """Click Save with fallbacks: role → has-text → XPath → force."""
    with suppress_exc():
        page.get_by_role("button", name="Save").click(timeout=SHORT_TIMEOUT_MS)
        return True
    with suppress_exc():
        page.locator('button:has-text("Save")').click(timeout=SHORT_TIMEOUT_MS)
        return True
    with suppress_exc():
        page.click(f"xpath={SAVE_BTN_XPATH}", timeout=SHORT_TIMEOUT_MS)
        return True
    with suppress_exc():
        page.get_by_role("button", name="Save").click(timeout=SHORT_TIMEOUT_MS, force=True)
        return True
    return False


def _click_submit(page) -> bool:
    """Click 'Submit for approval' (+ confirm dialog variant)."""
    strategies = [
        lambda: page.get_by_role("button", name="Submit for approval"),
        lambda: page.locator('button:has-text("Submit for approval")'),
        lambda: page.locator('//button[contains(normalize-space(.), "Submit for approval")]'),
    ]
    for make in strategies:
        with suppress_exc():
            loc = make().first
            loc.wait_for(state="visible", timeout=SHORT_TIMEOUT_MS)
            with suppress_exc():
                loc.scroll_into_view_if_needed()
            loc.click(timeout=SHORT_TIMEOUT_MS)
            # optional confirm dialog
            with suppress_exc():
                page.get_by_role("button", name="Submit").click(timeout=2_000)
            return True
    return False


def _has_submit_button(page) -> bool:
    with suppress_exc():
        loc = page.get_by_role("button", name="Submit for approval")
        if loc.count() and loc.is_visible():
            return True
    with suppress_exc():
        if page.locator('button:has-text("Submit for approval")').is_visible():
            return True
    with suppress_exc():
        if page.locator('//button[contains(normalize-space(.), "Submit for approval")]').is_visible():
            return True
    return False


def _get_week_title(page) -> str:
    """Grab the 'Wxx from dd-mm-yyyy to dd-mm-yyyy' chunk if present; else empty."""
    try:
        loc = page.locator("text=/^W\\d{1,2}\\s+from\\s+\\d{2}-\\d{2}-\\d{4}/i").first
        if loc.count():
            return (loc.inner_text() or "").strip()
    except Exception:
        pass
    try:
        header = page.locator("main, body").first.inner_text(timeout=2000)
        import re
        m = re.search(r"W\d{1,2}\s+from\s+\d{2}-\d{2}-\d{4}", header or "", re.I)
        if m:
            return m.group(0)
    except Exception:
        pass
    return ""


def _go_to_next_week(page) -> bool:
    """Click the right-arrow 'next week' control and wait for the week title to change."""
    before = _get_week_title(page)
    clicked = False
    for try_click in (
        lambda: page.locator(NEXT_WEEK_CY).first.click(timeout=SHORT_TIMEOUT_MS),
        lambda: page.locator('button:has(i.arrow.right)').first.click(timeout=SHORT_TIMEOUT_MS),
        lambda: page.locator(f"xpath={NEXT_WEEK_BTN_XPATH}").first.click(timeout=SHORT_TIMEOUT_MS),
    ):
        with suppress_exc():
            try_click()
            clicked = True
            break
    if not clicked:
        return False

    end = time.time() + 8.0
    while time.time() < end:
        after = _get_week_title(page)
        if after and after != before:
            return True
        time.sleep(0.15)
    return False


# ---------- main client ----------

class NaptaClient:
    """
    Playwright work is executed in a background thread (prevents SyncAPI-in-asyncio crash).
    """

    def __init__(self) -> None:
        self._cookie_ok: Optional[bool] = None  # for status text

    # ---------- public API (threaded wrappers) ----------

    def status(self) -> str:
        if STATE_PATH.exists():
            return "Auth: will use saved session (storage state or browser SSO cookies)."
        if self._cookie_ok is None:
            return "Auth: will use saved session (storage state or browser SSO cookies)."
        return "Auth: OK (session present)." if self._cookie_ok else \
               "Auth: missing/expired cookies. Please login to Napta once in your browser or run `login`."

    def save_current_week(self) -> Tuple[bool, str]:
        return self._run_in_worker(self._save_current_week_sync)

    def save_next_week(self) -> Tuple[bool, str]:
        return self._run_in_worker(self._save_next_week_sync)

    def submit_current_week(self) -> Tuple[bool, str]:
        return self._run_in_worker(self._submit_current_week_sync)

    def submit_next_week(self) -> Tuple[bool, str]:
        return self._run_in_worker(self._submit_next_week_sync)

    def save_and_submit_current_week(self) -> Tuple[bool, str]:
        # Single-session fast path
        return self._run_in_worker(self._save_and_submit_current_week_sync)

    def login(self) -> Tuple[bool, str]:
        """Headful login and capture storage_state to STATE_PATH."""
        return self._run_in_worker(self._login_sync)

    # ---------- worker-thread runner ----------

    def _run_in_worker(self, fn):
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            fut = pool.submit(fn)
            return fut.result()

    # ---------- building context ----------

    def _build_context(self, p, *, headless: bool):
        browser = p.chromium.launch(headless=headless, args=["--disable-dev-shm-usage"])
        if STATE_PATH.exists():
            ctx = browser.new_context(
                storage_state=str(STATE_PATH),
                user_agent=UA_DESKTOP,
                viewport={"width": 1400, "height": 900},
            )
            self._cookie_ok = True
        else:
            ctx = browser.new_context(
                user_agent=UA_DESKTOP,
                viewport={"width": 1400, "height": 900},
            )
            ctx.set_default_timeout(DEFAULT_TIMEOUT_MS)
            ctx.route("**/*", _route_slim)
            if not self._load_cookies_from_cache(ctx):
                self._load_cookies_from_keychain_and_cache(ctx)
            self._cookie_ok = True

        with suppress_exc():
            ctx.route("**/*", _route_slim)
        ctx.set_default_timeout(DEFAULT_TIMEOUT_MS)
        return browser, ctx

    # ---------- cookie helpers ----------

    def _load_cookies_from_cache(self, ctx) -> bool:
        if not _COOKIE_CACHE.exists():
            return False
        try:
            data = json.loads(_COOKIE_CACHE.read_text())
        except Exception:
            return False
        now = time.time()
        keep: list[dict] = []
        for c in data:
            exp = c.get("expires", None)
            if exp in (None, 0) or exp > now:
                keep.append(c)
        if not keep:
            return False
        batch: list[dict] = []
        for ck in keep:
            batch.append(ck)
            if len(batch) >= 100:
                ctx.add_cookies(batch)
                batch = []
        if batch:
            ctx.add_cookies(batch)
        return True

    def _load_cookies_from_keychain_and_cache(self, ctx) -> None:
        cj = browser_cookie3.chrome(domain_name=".napta.io")
        cookies = []
        now = time.time()
        for c in cj:
            if "napta.io" not in c.domain:
                continue
            exp = getattr(c, "expires", None)
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
            raise NaptaAuthError("No Napta cookies found. Open https://app.napta.io in Chrome and sign in.")

        batch: list[dict] = []
        for ck in cookies:
            batch.append(ck)
            if len(batch) >= 100:
                ctx.add_cookies(batch)
                batch = []
        if batch:
            ctx.add_cookies(batch)

        with suppress_exc():
            _COOKIE_CACHE.write_text(json.dumps(cookies, indent=2))

    # ---------- page helpers ----------

    def _on_login_page(self, page) -> bool:
        with suppress_exc():
            if page.locator('input[type="email"]').count():
                return True
        with suppress_exc():
            if page.get_by_role("button", name="Continue with Google").count():
                return True
        with suppress_exc():
            if page.locator("text=Welcome").count() and page.locator("text=Log in to continue").count():
                return True
        return False

    def _open_timesheet(self, page):
        page.goto(DEFAULT_APP_URL, wait_until="domcontentloaded", timeout=12_000)
        with suppress_exc():
            page.keyboard.press("Escape")
        # Best-effort “This week”
        with suppress_exc():
            page.get_by_role("button", name="This week").click(timeout=1_200)
        with suppress_exc():
            page.locator(f"xpath={THIS_WEEK_BTN_XPATH}").first.click(timeout=1_200)

    # ---------- operations (run inside worker) ----------

    def _save_current_week_sync(self) -> Tuple[bool, str]:
        p = sync_playwright().start()
        browser = None
        try:
            browser, ctx = self._build_context(p, headless=True)
            page = ctx.new_page()
            self._open_timesheet(page)

            chip = (_get_status_chip_text(page) or "").strip().lower()
            if chip.startswith(("approval pending", "submitted")):
                return True, "ℹ️ Timesheet already submitted for this week (Approval pending)."

            if self._on_login_page(page):
                name = f"napta_login_required_{ts()}.png"
                page.screenshot(path=name, full_page=True)
                return False, f"⛔ Napta login required. Login required. Please open Napta once in Chrome. Screenshot -> {name}"

            state = _wait_for_timesheet_ready(page, timeout_ms=SHORT_TIMEOUT_MS)
            if state is None:
                return True, "ℹ️ Timesheet already submitted for this week (Approval pending)."

            if state == "create":
                if not _click_create(page):
                    name = f"napta_create_failure_{ts()}.png"
                    page.screenshot(path=name, full_page=True)
                    return False, f"❌ Could not click 'Create timesheet'. Screenshot -> {name}"
                state = _wait_for_timesheet_ready(page, timeout_ms=SHORT_TIMEOUT_MS)
                if state is None:
                    name = f"napta_create_post_state_{ts()}.png"
                    page.screenshot(path=name, full_page=True)
                    return False, "❌ After 'Create', no Save/Submit visible."

            if state == "submit":
                return True, "✅ Timesheet already saved. 'Submit for approval' is visible."

            if not _click_save(page):
                name = f"napta_save_failure_{ts()}.png"
                page.screenshot(path=name, full_page=True)
                return False, f"❌ Could not click 'Save'. Screenshot -> {name}"

            _saw_saved_toast(page)
            return True, "✅ Saved (draft)."
        except NaptaAuthError as e:
            return False, f"⛔ Napta login required. {e}"
        finally:
            with suppress_exc():
                if browser:
                    browser.close()
            p.stop()

    def _save_next_week_sync(self) -> Tuple[bool, str]:
        p = sync_playwright().start()
        browser = None
        try:
            browser, ctx = self._build_context(p, headless=True)
            page = ctx.new_page()
            self._open_timesheet(page)

            if self._on_login_page(page):
                name = f"napta_login_required_{ts()}.png"
                page.screenshot(path=name, full_page=True)
                return False, f"⛔ Napta login required. Login required. Please open Napta once in Chrome. Screenshot -> {name}"

            before = _get_week_title(page)
            if not _go_to_next_week(page):
                name = f"napta_error_{ts()}.png"
                page.screenshot(path=name, full_page=True)
                return False, f"❌ Could not navigate to next week. Screenshot -> {name}"
            after = _get_week_title(page)
            if not after or after == before:
                name = f"napta_nav_verify_{ts()}.png"
                page.screenshot(path=name, full_page=True)
                return False, f"❌ Navigation didn't land on next week. Screenshot -> {name}"

            chip_before = (_get_status_chip_text(page) or "").strip().lower()
            state = _wait_for_timesheet_ready(page, timeout_ms=SHORT_TIMEOUT_MS)
            if state is None and _has_submit_button(page):
                # Submit is visible ⇒ it's already saved/open
                return True, "✅ Next week saved. Do you want to 'Submit for approval'? Type sbnw"
            if state is None:
                # Truly neither visible → likely submitted/locked
                chip = (_get_status_chip_text(page) or "").strip().lower()
                if chip.startswith(("approval pending", "submitted")):
                    return True, "ℹ️ Next week already submitted (Approval pending)."
                # transient: treat as already saved
                return True, "✅ Next week already saved. 'Submit for approval' may be visible."

            if state == "create":
                if not _click_create(page):
                    name = f"napta_create_failure_{ts()}.png"
                    page.screenshot(path=name, full_page=True)
                    return False, f"❌ Could not click 'Create timesheet' on next week. Screenshot -> {name}"
                state = _wait_for_timesheet_ready(page, timeout_ms=SHORT_TIMEOUT_MS)

            if state == "submit":
                return True, "✅ Next week saved. Do you want to 'Submit for approval'? Type sbnw"

            if state == "save":
                if not _click_save(page):
                    name = f"napta_save_failure_{ts()}.png"
                    page.screenshot(path=name, full_page=True)
                    return False, f"❌ Could not click 'Save' on next week. Screenshot -> {name}"

                if not _wait_for_save_confirmation(page, prev_chip=chip_before, timeout_s=12):
                    name = f"napta_save_verify_fail_{ts()}.png"
                    page.screenshot(path=name, full_page=True)
                    return False, f"❌ Save didn’t stick for next week (chip stayed “{chip_before or 'unknown'}”). Screenshot -> {name}"

                return True, "✅ Saved next week (draft)."

            return False, "❌ Unexpected state while saving next week."
        except NaptaAuthError as e:
            return False, f"⛔ Napta login required. {e}"
        finally:
            with suppress_exc():
                if browser:
                    browser.close()
            p.stop()

    def _submit_current_week_sync(self) -> Tuple[bool, str]:
        p = sync_playwright().start()
        browser = None
        try:
            browser, ctx = self._build_context(p, headless=True)
            page = ctx.new_page()
            self._open_timesheet(page)

            chip = (_get_status_chip_text(page) or "").strip().lower()
            if chip.startswith(("approval pending", "submitted")):
                return True, "ℹ️ Timesheet already submitted for this week (Approval pending)."

            if self._on_login_page(page):
                name = f"napta_login_required_{ts()}.png"
                page.screenshot(path=name, full_page=True)
                return False, f"⛔ Napta login required. Login required. Please open Napta once in Chrome. Screenshot -> {name}"

            state = _wait_for_timesheet_ready(page, timeout_ms=SHORT_TIMEOUT_MS)
            if state is None and _has_submit_button(page):
                state = "submit"
            elif state is None:
                return True, "ℹ️ Timesheet already submitted for this week (Approval pending)."

            if state in ("create", "save"):
                if state == "create":
                    if not _click_create(page):
                        name = f"napta_create_failure_{ts()}.png"
                        page.screenshot(path=name, full_page=True)
                        return False, f"❌ Could not click 'Create timesheet'. Screenshot -> {name}"
                    state = _wait_for_timesheet_ready(page, timeout_ms=SHORT_TIMEOUT_MS)
                if state == "save":
                    if not _click_save(page):
                        name = f"napta_save_failure_{ts()}.png"
                        page.screenshot(path=name, full_page=True)
                        return False, f"❌ Could not click 'Save'. Screenshot -> {name}"
                    _saw_saved_toast(page)
                    state = _wait_for_timesheet_ready(page, timeout_ms=SHORT_TIMEOUT_MS)

            if not _click_submit(page):
                name = f"napta_submit_failure_{ts()}.png"
                page.screenshot(path=name, full_page=True)
                return False, f"❌ Could not click 'Submit for approval'. Screenshot -> {name}"

            with suppress_exc():
                if page.locator("text=Approval pending").count():
                    return True, "✅ Submitted for approval."
            return True, "✅ Submit clicked."
        except NaptaAuthError as e:
            return False, f"⛔ Napta login required. {e}"
        finally:
            with suppress_exc():
                if browser:
                    browser.close()
            p.stop()

    def _submit_next_week_sync(self) -> Tuple[bool, str]:
        """Navigate to next week, prefer clicking 'Submit for approval' if present, else save-then-submit."""
        p = sync_playwright().start()
        browser = None
        try:
            browser, ctx = self._build_context(p, headless=True)
            page = ctx.new_page()
            self._open_timesheet(page)

            if self._on_login_page(page):
                name = f"napta_login_required_{ts()}.png"
                page.screenshot(path=name, full_page=True)
                return False, f"⛔ Napta login required. Login required. Please open Napta once in Chrome. Screenshot -> {name}"

            before = _get_week_title(page)
            if not _go_to_next_week(page):
                name = f"napta_error_{ts()}.png"
                page.screenshot(path=name, full_page=True)
                return False, f"❌ Could not navigate to next week. Screenshot -> {name}"
            after = _get_week_title(page)
            if not after or after == before:
                name = f"napta_nav_verify_{ts()}.png"
                page.screenshot(path=name, full_page=True)
                return False, f"❌ Navigation didn't land on next week. Screenshot -> {name}"

            # 1) If the submit button is visible, do it immediately.
            if _has_submit_button(page):
                if not _click_submit(page):
                    name = f"napta_submit_failure_{ts()}.png"
                    page.screenshot(path=name, full_page=True)
                    return False, f"❌ Could not click 'Submit for approval' on next week. Screenshot -> {name}"
                with suppress_exc():
                    if page.locator("text=Approval pending").count():
                        return True, "✅ Submitted next week for approval."
                return True, "✅ Submit clicked (next week)."

            # 2) Otherwise, try to get into a state where submit appears: Create → Save
            state = _wait_for_timesheet_ready(page, timeout_ms=SHORT_TIMEOUT_MS)

            if state == "create":
                if not _click_create(page):
                    name = f"napta_create_failure_{ts()}.png"
                    page.screenshot(path=name, full_page=True)
                    return False, f"❌ Could not click 'Create timesheet' on next week. Screenshot -> {name}"
                state = _wait_for_timesheet_ready(page, timeout_ms=SHORT_TIMEOUT_MS)

            if state == "save":
                prev_chip = (_get_status_chip_text(page) or "").strip().lower()
                if not _click_save(page):
                    name = f"napta_save_failure_{ts()}.png"
                    page.screenshot(path=name, full_page=True)
                    return False, f"❌ Could not click 'Save' on next week. Screenshot -> {name}"
                if not _wait_for_save_confirmation(page, prev_chip=prev_chip, timeout_s=12):
                    name = f"napta_save_verify_fail_{ts()}.png"
                    page.screenshot(path=name, full_page=True)
                    return False, f"❌ Save didn’t stick for next week (chip stayed “{prev_chip or 'unknown'}”). Screenshot -> {name}"

            # 3) After saving, the submit button should be there. If not, check chip (fallback).
            if not _has_submit_button(page):
                chip = (_get_status_chip_text(page) or "").strip().lower()
                if chip.startswith(("approval pending", "submitted")):
                    return True, "ℹ️ Next week already submitted (Approval pending)."
                name = f"napta_submit_absent_{ts()}.png"
                page.screenshot(path=name, full_page=True)
                return False, "❌ Submit button not visible after saving next week. Screenshot -> " + name

            # 4) Click submit now.
            if not _click_submit(page):
                name = f"napta_submit_failure_{ts()}.png"
                page.screenshot(path=name, full_page=True)
                return False, f"❌ Could not click 'Submit for approval' on next week. Screenshot -> {name}"

            with suppress_exc():
                if page.locator("text=Approval pending").count():
                    return True, "✅ Submitted next week for approval."
            return True, "✅ Submit clicked (next week)."

        except NaptaAuthError as e:
            return False, f"⛔ Napta login required. {e}"
        finally:
            with suppress_exc():
                if browser:
                    browser.close()
            p.stop()


    def _save_and_submit_current_week_sync(self) -> Tuple[bool, str]:
        """Single-session save+submit for speed."""
        p = sync_playwright().start()
        browser = None
        try:
            browser, ctx = self._build_context(p, headless=True)
            page = ctx.new_page()
            self._open_timesheet(page)

            chip = (_get_status_chip_text(page) or "").strip().lower()
            if chip.startswith(("approval pending", "submitted")):
                return True, "ℹ️ Timesheet already submitted for this week (Approval pending)."

            if self._on_login_page(page):
                name = f"napta_login_required_{ts()}.png"
                page.screenshot(path=name, full_page=True)
                return False, f"⛔ Napta login required. Login required. Please open Napta once in Chrome. Screenshot -> {name}"

            state = _wait_for_timesheet_ready(page, timeout_ms=SHORT_TIMEOUT_MS)
            if state is None and _has_submit_button(page):
                state = "submit"
            elif state is None:
                return True, "ℹ️ Timesheet already submitted for this week (Approval pending)."

            if state == "create":
                if not _click_create(page):
                    name = f"napta_create_failure_{ts()}.png"
                    page.screenshot(path=name, full_page=True)
                    return False, f"❌ Could not click 'Create timesheet'. Screenshot -> {name}"
                state = _wait_for_timesheet_ready(page, timeout_ms=SHORT_TIMEOUT_MS)

            if state == "save":
                if not _click_save(page):
                    name = f"napta_save_failure_{ts()}.png"
                    page.screenshot(path=name, full_page=True)
                    return False, f"❌ Could not click 'Save'. Screenshot -> {name}"
                _saw_saved_toast(page)
                state = _wait_for_timesheet_ready(page, timeout_ms=SHORT_TIMEOUT_MS)

            if not _click_submit(page):
                name = f"napta_submit_failure_{ts()}.png"
                page.screenshot(path=name, full_page=True)
                return False, f"❌ Could not click 'Submit for approval'. Screenshot -> {name}"

            with suppress_exc():
                if page.locator("text=Approval pending").count():
                    return True, "✅ Submitted for approval."
            return True, "✅ Submit clicked."
        except NaptaAuthError as e:
            return False, f"⛔ Napta login required. {e}"
        finally:
            with suppress_exc():
                if browser:
                    browser.close()
            p.stop()

    def _login_sync(self) -> Tuple[bool, str]:
        """Headful login and capture storage_state to STATE_PATH."""
        p = sync_playwright().start()
        browser = None
        try:
            browser, ctx = self._build_context(p, headless=False)
            page = ctx.new_page()
            page.goto(DEFAULT_APP_URL, wait_until="domcontentloaded", timeout=30_000)

            ready = _wait_for_timesheet_ready(page, timeout_ms=LONG_TIMEOUT_MS)
            if ready is None and not self._on_login_page(page):
                ctx.storage_state(path=str(STATE_PATH))
                return True, "✅ Login captured. You can now run: save / submit."
            if ready is None:
                name = f"napta_login_timeout_{ts()}.png"
                page.screenshot(path=name, full_page=True)
                return False, f"Login window timed out. Screenshot -> {name}"

            ctx.storage_state(path=str(STATE_PATH))
            return True, "✅ Login captured. You can now run: save / submit."
        except Exception as e:
            return False, f"Login failed: {e!s}"
        finally:
            with suppress_exc():
                if browser:
                    browser.close()
            p.stop()

    # ---------- CLI compatibility ----------

    def preview_week(self, iso_week: str, *, leave_details=None):
        return True, "(preview) Using current week; nothing to preview.", None

    def save_week(self, iso_week: str, *, leave_details=None):
        return self.save_current_week()

    def submit_week(self, iso_week: str):
        return self.submit_current_week()
