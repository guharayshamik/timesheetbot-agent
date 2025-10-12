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


# ----------------------------- Constants / Config -----------------------------

DEFAULT_APP_URL = "https://app.napta.io/timesheet"

# Absolute XPaths observed in your tenant (kept as last-resort fallbacks)
SAVE_BTN_XPATH = '/html/body/div[1]/div[2]/div[1]/div[2]/div[2]/button'
THIS_WEEK_BTN_XPATH = '/html/body/div[1]/div[2]/div[1]/div[2]/div[2]/div/button[2]'
NEXT_WEEK_CY = '[data-cy="PeriodNavigation_navRight"]'
NEXT_WEEK_BTN_XPATH = '/html/body/div[1]/div[2]/div[1]/div[2]/div[2]/div/button[3]'
CREATE_BTN_XPATH = '//button[contains(normalize-space(.), "Create")]'
CREATE_TIMESHEET_XPATH = '//button[contains(normalize-space(.), "Create timesheet")]'

# Cache paths
_CACHE_DIR = Path(os.path.expanduser("~/.cache/timesheetbot"))
_CACHE_DIR.mkdir(parents=True, exist_ok=True)
_COOKIE_CACHE = _CACHE_DIR / "napta_cookies.json"
STATE_PATH = _CACHE_DIR / "napta_storage_state.json"  # persisted after login()

# Screenshot dir (kept out of repo)
_SCREENSHOT_DIR = _CACHE_DIR / "shots"
_SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

def _shot(name: str) -> str:
    return str(_SCREENSHOT_DIR / name)


# Network slimming (speeds up page)
_ANALYTICS_HOSTS = (
    "googletagmanager.com", "google-analytics.com", "segment.io", "sentry.io",
    "plausible.io", "fullstory.com", "intercom.io", "hotjar.com",
    "gravatar.com", "unpkg.com",
)

# Timeouts
SHORT_TIMEOUT_MS = 4_000
DEFAULT_TIMEOUT_MS = 5_000
LONG_TIMEOUT_MS = 300_000  # headful SSO upper bound (not usually reached)

UA_DESKTOP = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
)


# --------------------------------- Errors ------------------------------------

class NaptaAuthError(RuntimeError):
    """Raised when SSO/app login is required or session is expired."""
    pass


# ---------------------------- Small util helpers -----------------------------

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
    if req.resource_type in ("image", "media", "font"):
        return route.abort()
    url = req.url
    if url.endswith((".map", ".svg")):
        return route.abort()
    if any(h in url for h in _ANALYTICS_HOSTS):
        return route.abort()
    return route.continue_()


# ------------------------------ Page helpers ---------------------------------

def _get_status_chip_text(page) -> str:
    """
    Read the small status chip near the top header (avoid the legend at the bottom).
    """
    containers = [
        "header",
        "main >> div:near(:text('This week'), 800)",
        "main >> div:has(button:has-text('Submit for approval'))",
        "main >> div:has(button:has-text('Save'))",
        "main",
    ]
    status_regex = r'^(Not created|Draft|Open|Approval pending|Submitted)$'
    for scope in containers:
        with suppress_exc():
            loc = page.locator(f"{scope} >> text=/{status_regex}/i").first
            if loc.count():
                text = (loc.inner_text() or "").strip()
                if text and len(text) <= 20:
                    return text
    return ""


def _saw_saved_toast(page) -> bool:
    with suppress_exc():
        page.wait_for_selector("text=/\\bSaved\\b/i", timeout=SHORT_TIMEOUT_MS)
        return True
    return False


def _wait_for_save_confirmation(page, *, prev_chip: str, timeout_s: float = 12.0) -> bool:
    if _saw_saved_toast(page):
        return True
    end = time.time() + timeout_s
    prev = (prev_chip or "").strip().lower()
    while time.time() < end:
        now = (_get_status_chip_text(page) or "").strip().lower()
        if now and now != "not created":
            if prev == "not created":
                return True
            if prev in ("open", "draft") and now in ("open", "draft", "approval pending", "submitted"):
                return True
            if now != prev:
                return True
        time.sleep(0.15)
    return False


def _wait_for_timesheet_ready(page, timeout_ms: int) -> Optional[str]:
    """
    Returns: "create" | "save" | "submit" | None
    """
    end = time.time() + (timeout_ms / 1000.0)
    while time.time() < end:
        # Create
        for sel in (
            lambda: page.get_by_role("button", name="Create timesheet"),
            lambda: page.get_by_role("button", name="Create"),
            lambda: page.locator('button:has-text("Create timesheet")'),
            lambda: page.locator('button:has-text("Create")'),
        ):
            with suppress_exc():
                loc = sel()
                if loc and loc.count() and loc.is_visible():
                    return "create"
        # Submit
        for sel in (
            lambda: page.get_by_role("button", name="Submit for approval"),
            lambda: page.locator('button:has-text("Submit for approval")'),
        ):
            with suppress_exc():
                loc = sel()
                if loc and loc.count() and loc.is_visible():
                    return "submit"
        # Save
        for sel in (
            lambda: page.get_by_role("button", name="Save"),
            lambda: page.locator('button:has-text("Save")'),
            lambda: page.locator(f"xpath={SAVE_BTN_XPATH}"),
        ):
            with suppress_exc():
                loc = sel()
                if loc and loc.is_visible():
                    return "save"
        time.sleep(0.10)
    return None


def _click_create(page) -> bool:
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
                with suppress_exc(): btn.scroll_into_view_if_needed()
                btn.click(timeout=SHORT_TIMEOUT_MS)
                time.sleep(0.4)
                return True
    return False


def _click_save(page) -> bool:
    with suppress_exc():
        page.get_by_role("button", name="Save").click(timeout=SHORT_TIMEOUT_MS); return True
    with suppress_exc():
        page.locator('button:has-text("Save")').click(timeout=SHORT_TIMEOUT_MS); return True
    with suppress_exc():
        page.click(f"xpath={SAVE_BTN_XPATH}", timeout=SHORT_TIMEOUT_MS); return True
    with suppress_exc():
        page.get_by_role("button", name="Save").click(timeout=SHORT_TIMEOUT_MS, force=True); return True
    return False


def _click_submit(page) -> bool:
    strategies = [
        lambda: page.get_by_role("button", name="Submit for approval"),
        lambda: page.locator('button:has-text("Submit for approval")'),
        lambda: page.locator('//button[contains(normalize-space(.), "Submit for approval")]'),
    ]
    for make in strategies:
        with suppress_exc():
            loc = make().first
            loc.wait_for(state="visible", timeout=SHORT_TIMEOUT_MS)
            with suppress_exc(): loc.scroll_into_view_if_needed()
            loc.click(timeout=SHORT_TIMEOUT_MS)
            with suppress_exc(): page.get_by_role("button", name="Submit").click(timeout=2_000)
            return True
    return False


def _has_submit_button(page) -> bool:
    with suppress_exc():
        loc = page.get_by_role("button", name="Submit for approval")
        if loc.count() and loc.is_visible():
            return True
    with suppress_exc():
        if page.locator('button:has-text("Submit for approval")').is_visible(): return True
    with suppress_exc():
        if page.locator('//button[contains(normalize-space(.), "Submit for approval")]').is_visible(): return True
    return False


def _get_week_title(page) -> str:
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
    before = _get_week_title(page)
    clicked = False
    for try_click in (
        lambda: page.locator(NEXT_WEEK_CY).first.click(timeout=SHORT_TIMEOUT_MS),
        lambda: page.locator('button:has(i.arrow.right)').first.click(timeout=SHORT_TIMEOUT_MS),
        lambda: page.locator(f"xpath={NEXT_WEEK_BTN_XPATH}").first.click(timeout=SHORT_TIMEOUT_MS),
    ):
        with suppress_exc():
            try_click(); clicked = True; break
    if not clicked:
        return False
    end = time.time() + 8.0
    while time.time() < end:
        after = _get_week_title(page)
        if after and after != before:
            return True
        time.sleep(0.15)
    return False


# ---------------------- View helpers (rows extractor) ------------------------

def _extract_week_rows(page) -> list[tuple[str, str]]:
    """
    Returns a list of (day_label, total_hours) for the visible week.
    Tries ARIA grid first, then a table fallback.
    """
    rows: list[tuple[str, str]] = []

    # Strategy A: ARIA grid / data-grid
    with suppress_exc():
        grid = page.locator('div[role="grid"], [data-cy="TimesheetGrid"]').first
        if grid.count():
            r_count = grid.locator('[role="row"]').count()
            for i in range(r_count):
                row = grid.locator('[role="row"]').nth(i)
                txt = (row.inner_text() or "").strip()
                if not txt or "Total" in txt or "Project" in txt:
                    continue
                parts = [p for p in txt.split() if p]
                if len(parts) >= 2:
                    day = " ".join(parts[:-1])
                    hours = parts[-1]
                    if any(ch.isdigit() for ch in hours):
                        rows.append((day, hours))
    if rows:
        return rows

    # Strategy B: first table in main content
    with suppress_exc():
        tbl = page.locator("main table").first
        if tbl.count():
            body_rows = tbl.locator("tbody tr")
            for i in range(body_rows.count()):
                r = body_rows.nth(i)
                cells = r.locator("td")
                if cells.count() >= 2:
                    day = (cells.nth(0).inner_text() or "").strip()
                    hours = (cells.nth(cells.count()-1).inner_text() or "").strip()
                    if day and any(ch.isdigit() for ch in hours):
                        rows.append((day, hours))
    return rows


def _format_rows_table(rows: list[tuple[str, str]]) -> str:
    if not rows:
        return "ℹ️ No day rows detected for this week (or parsing failed)."
    lines = ["Timesheet (visible week):"]
    for d, h in rows:
        lines.append(f"  • {d}: {h}")
    total = None
    try:
        vals = []
        for _, h in rows:
            hh = h.lower().replace("h", "").strip()
            vals.append(float(hh))
        total = sum(vals)
    except Exception:
        pass
    if total is not None:
        lines.append(f"  = Total: {total:g}h")
    return "\n".join(lines)


# --------------------------------- Client ------------------------------------

class NaptaClient:
    """
    All Playwright work is executed in a background thread (prevents SyncAPI-in-asyncio crash).
    """

    def __init__(self) -> None:
        self._cookie_ok: Optional[bool] = None

    # -------- Public API (threaded wrappers) --------

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
        return self._run_in_worker(self._save_and_submit_current_week_sync)

    def login(self) -> Tuple[bool, str]:
        """Headful login and capture storage_state."""
        return self._run_in_worker(self._login_sync)

    def view_week(self, which: str = "current") -> Tuple[bool, str]:
        return self._run_in_worker(lambda: self._view_week_sync(which))

    # -------- Worker runner --------

    def _run_in_worker(self, fn):
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(fn).result()

    # -------------------- Context / Cookies / Open page -----------------------

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

    def _load_cookies_from_cache(self, ctx) -> bool:
        if not _COOKIE_CACHE.exists():
            return False
        try:
            data = json.loads(_COOKIE_CACHE.read_text())
        except Exception:
            return False
        now = time.time()
        keep = []
        for c in data:
            exp = c.get("expires", None)
            if exp in (None, 0) or exp > now:
                keep.append(c)
        if not keep:
            return False
        batch = []
        for ck in keep:
            batch.append(ck)
            if len(batch) >= 100:
                ctx.add_cookies(batch); batch = []
        if batch:
            ctx.add_cookies(batch)
        return True

    def _load_cookies_from_keychain_and_cache(self, ctx) -> None:
        # Include app + auth hosts (SSO)
        cj = []
        with suppress_exc():
            cj += list(browser_cookie3.chrome(domain_name=".napta.io"))
        with suppress_exc():
            cj += list(browser_cookie3.chrome(domain_name="auth.napta.io"))

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

        batch = []
        for ck in cookies:
            batch.append(ck)
            if len(batch) >= 100:
                ctx.add_cookies(batch); batch = []
        if batch:
            ctx.add_cookies(batch)
        with suppress_exc():
            _COOKIE_CACHE.write_text(json.dumps(cookies, indent=2))

    def _on_login_page(self, page) -> bool:
        with suppress_exc():
            if page.locator('input[type="email"]').count(): return True
        with suppress_exc():
            if page.get_by_role("button", name="Continue with Google").count(): return True
        with suppress_exc():
            if page.locator("text=Welcome").count() and page.locator("text=Log in to continue").count(): return True
        return False

    def _open_timesheet(self, page):
        page.goto(DEFAULT_APP_URL, wait_until="domcontentloaded", timeout=12_000)
        with suppress_exc(): page.keyboard.press("Escape")
        with suppress_exc(): page.get_by_role("button", name="This week").click(timeout=1_200)
        with suppress_exc(): page.locator(f"xpath={THIS_WEEK_BTN_XPATH}").first.click(timeout=1_200)

    # ------------------------------ Operations --------------------------------

    def _save_current_week_sync(self) -> Tuple[bool, str]:
        p = sync_playwright().start(); browser = None
        try:
            browser, ctx = self._build_context(p, headless=True)
            page = ctx.new_page(); self._open_timesheet(page)

            chip = (_get_status_chip_text(page) or "").strip().lower()
            if chip.startswith(("approval pending", "submitted")):
                return True, "ℹ️ Timesheet already submitted for this week (Approval pending)."

            if self._on_login_page(page):
                name = f"napta_login_required_{ts()}.png"; page.screenshot(path=_shot(name), full_page=True)
                return False, f"⛔ Napta login required. Please open Napta once in Chrome. Screenshot -> {name}"

            state = _wait_for_timesheet_ready(page, timeout_ms=SHORT_TIMEOUT_MS)
            if state is None:
                return True, "ℹ️ Timesheet already submitted for this week (Approval pending)."

            if state == "create":
                if not _click_create(page):
                    name = f"napta_create_failure_{ts()}.png"; page.screenshot(path=_shot(name), full_page=True)
                    return False, f"❌ Could not click 'Create timesheet'. Screenshot -> {name}"
                state = _wait_for_timesheet_ready(page, timeout_ms=SHORT_TIMEOUT_MS)
                if state is None:
                    name = f"napta_create_post_state_{ts()}.png"; page.screenshot(path=_shot(name), full_page=True)
                    return False, "❌ After 'Create', no Save/Submit visible."

            if state == "submit":
                return True, "✅ Timesheet already saved. 'Submit for approval' is visible."

            if not _click_save(page):
                name = f"napta_save_failure_{ts()}.png"; page.screenshot(path=_shot(name), full_page=True)
                return False, f"❌ Could not click 'Save'. Screenshot -> {name}"

            _saw_saved_toast(page)
            return True, "✅ Saved (draft)."
        except NaptaAuthError as e:
            return False, f"⛔ Napta login required. {e}"
        finally:
            with suppress_exc(): 
                if browser: browser.close()
            p.stop()

    def _save_next_week_sync(self) -> Tuple[bool, str]:
        p = sync_playwright().start(); browser = None
        try:
            browser, ctx = self._build_context(p, headless=True)
            page = ctx.new_page(); self._open_timesheet(page)

            if self._on_login_page(page):
                name = f"napta_login_required_{ts()}.png"; page.screenshot(path=_shot(name), full_page=True)
                return False, f"⛔ Napta login required. Please open Napta once in Chrome. Screenshot -> {name}"

            before = _get_week_title(page)
            if not _go_to_next_week(page):
                name = f"napta_error_{ts()}.png"; page.screenshot(path=_shot(name), full_page=True)
                return False, f"❌ Could not navigate to next week. Screenshot -> {name}"
            after = _get_week_title(page)
            if not after or after == before:
                name = f"napta_nav_verify_{ts()}.png"; page.screenshot(path=_shot(name), full_page=True)
                return False, f"❌ Navigation didn't land on next week. Screenshot -> {name}"

            chip_before = (_get_status_chip_text(page) or "").strip().lower()
            state = _wait_for_timesheet_ready(page, timeout_ms=SHORT_TIMEOUT_MS)
            if state is None and _has_submit_button(page):
                return True, "✅ Next week saved. Do you want to 'Submit for approval'? Type sbnw"
            if state is None:
                chip = (_get_status_chip_text(page) or "").strip().lower()
                if chip.startswith(("approval pending", "submitted")):
                    return True, "ℹ️ Next week already submitted (Approval pending)."
                return True, "✅ Next week already saved. 'Submit for approval' may be visible."

            if state == "create":
                if not _click_create(page):
                    name = f"napta_create_failure_{ts()}.png"; page.screenshot(path=_shot(name), full_page=True)
                    return False, f"❌ Could not click 'Create timesheet' on next week. Screenshot -> {name}"
                state = _wait_for_timesheet_ready(page, timeout_ms=SHORT_TIMEOUT_MS)

            if state == "submit":
                return True, "✅ Next week saved. Do you want to 'Submit for approval'? Type sbnw"

            if state == "save":
                if not _click_save(page):
                    name = f"napta_save_failure_{ts()}.png"; page.screenshot(path=_shot(name), full_page=True)
                    return False, f"❌ Could not click 'Save' on next week. Screenshot -> {name}"
                if not _wait_for_save_confirmation(page, prev_chip=chip_before, timeout_s=12):
                    name = f"napta_save_verify_fail_{ts()}.png"; page.screenshot(path=_shot(name), full_page=True)
                    return False, f"❌ Save didn’t stick for next week (chip stayed “{chip_before or 'unknown'}”). Screenshot -> {name}"
                return True, "✅ Saved next week (draft)."

            return False, "❌ Unexpected state while saving next week."
        except NaptaAuthError as e:
            return False, f"⛔ Napta login required. {e}"
        finally:
            with suppress_exc(): 
                if browser: browser.close()
            p.stop()

    def _submit_current_week_sync(self) -> Tuple[bool, str]:
        p = sync_playwright().start(); browser = None
        try:
            browser, ctx = self._build_context(p, headless=True)
            page = ctx.new_page(); self._open_timesheet(page)

            chip = (_get_status_chip_text(page) or "").strip().lower()
            if chip.startswith(("approval pending", "submitted")):
                return True, "ℹ️ Timesheet already submitted for this week (Approval pending)."

            if self._on_login_page(page):
                name = f"napta_login_required_{ts()}.png"; page.screenshot(path=_shot(name), full_page=True)
                return False, f"⛔ Napta login required. Please open Napta once in Chrome. Screenshot -> {name}"

            state = _wait_for_timesheet_ready(page, timeout_ms=SHORT_TIMEOUT_MS)
            if state is None and _has_submit_button(page):
                state = "submit"
            elif state is None:
                return True, "ℹ️ Timesheet already submitted for this week (Approval pending)."

            if state in ("create", "save"):
                if state == "create":
                    if not _click_create(page):
                        name = f"napta_create_failure_{ts()}.png"; page.screenshot(path=_shot(name), full_page=True)
                        return False, f"❌ Could not click 'Create timesheet'. Screenshot -> {name}"
                    state = _wait_for_timesheet_ready(page, timeout_ms=SHORT_TIMEOUT_MS)
                if state == "save":
                    if not _click_save(page):
                        name = f"napta_save_failure_{ts()}.png"; page.screenshot(path=_shot(name), full_page=True)
                        return False, f"❌ Could not click 'Save'. Screenshot -> {name}"
                    _saw_saved_toast(page)
                    state = _wait_for_timesheet_ready(page, timeout_ms=SHORT_TIMEOUT_MS)

            if not _click_submit(page):
                name = f"napta_submit_failure_{ts()}.png"; page.screenshot(path=_shot(name), full_page=True)
                return False, f"❌ Could not click 'Submit for approval'. Screenshot -> {name}"

            with suppress_exc():
                if page.locator("text=Approval pending").count():
                    return True, "✅ Submitted for approval."
            return True, "✅ Submit clicked."
        except NaptaAuthError as e:
            return False, f"⛔ Napta login required. {e}"
        finally:
            with suppress_exc(): 
                if browser: browser.close()
            p.stop()

    def _submit_next_week_sync(self) -> Tuple[bool, str]:
        p = sync_playwright().start(); browser = None
        try:
            browser, ctx = self._build_context(p, headless=True)
            page = ctx.new_page(); self._open_timesheet(page)

            if self._on_login_page(page):
                name = f"napta_login_required_{ts()}.png"; page.screenshot(path=_shot(name), full_page=True)
                return False, f"⛔ Napta login required. Please open Napta once in Chrome. Screenshot -> {name}"

            before = _get_week_title(page)
            if not _go_to_next_week(page):
                name = f"napta_error_{ts()}.png"; page.screenshot(path=_shot(name), full_page=True)
                return False, f"❌ Could not navigate to next week. Screenshot -> {name}"
            after = _get_week_title(page)
            if not after or after == before:
                name = f"napta_nav_verify_{ts()}.png"; page.screenshot(path=_shot(name), full_page=True)
                return False, f"❌ Navigation didn't land on next week. Screenshot -> {name}"

            if _has_submit_button(page):
                if not _click_submit(page):
                    name = f"napta_submit_failure_{ts()}.png"; page.screenshot(path=_shot(name), full_page=True)
                    return False, f"❌ Could not click 'Submit for approval' on next week. Screenshot -> {name}"
                with suppress_exc():
                    if page.locator("text=Approval pending").count():
                        return True, "✅ Submitted next week for approval."
                return True, "✅ Submit clicked (next week)."

            state = _wait_for_timesheet_ready(page, timeout_ms=SHORT_TIMEOUT_MS)

            if state == "create":
                if not _click_create(page):
                    name = f"napta_create_failure_{ts()}.png"; page.screenshot(path=_shot(name), full_page=True)
                    return False, f"❌ Could not click 'Create timesheet' on next week. Screenshot -> {name}"
                state = _wait_for_timesheet_ready(page, timeout_ms=SHORT_TIMEOUT_MS)

            if state == "save":
                prev_chip = (_get_status_chip_text(page) or "").strip().lower()
                if not _click_save(page):
                    name = f"napta_save_failure_{ts()}.png"; page.screenshot(path=_shot(name), full_page=True)
                    return False, f"❌ Could not click 'Save' on next week. Screenshot -> {name}"
                if not _wait_for_save_confirmation(page, prev_chip=prev_chip, timeout_s=12):
                    name = f"napta_save_verify_fail_{ts()}.png"; page.screenshot(path=_shot(name), full_page=True)
                    return False, f"❌ Save didn’t stick for next week (chip stayed “{prev_chip or 'unknown'}”). Screenshot -> {name}"

            if not _has_submit_button(page):
                chip = (_get_status_chip_text(page) or "").strip().lower()
                if chip.startswith(("approval pending", "submitted")):
                    return True, "ℹ️ Next week already submitted (Approval pending)."
                name = f"napta_submit_absent_{ts()}.png"; page.screenshot(path=_shot(name), full_page=True)
                return False, "❌ Submit button not visible after saving next week. Screenshot -> " + name

            if not _click_submit(page):
                name = f"napta_submit_failure_{ts()}.png"; page.screenshot(path=_shot(name), full_page=True)
                return False, f"❌ Could not click 'Submit for approval' on next week. Screenshot -> {name}"

            with suppress_exc():
                if page.locator("text=Approval pending").count():
                    return True, "✅ Submitted next week for approval."
            return True, "✅ Submit clicked (next week)."
        except NaptaAuthError as e:
            return False, f"⛔ Napta login required. {e}"
        finally:
            with suppress_exc(): 
                if browser: browser.close()
            p.stop()

    def _save_and_submit_current_week_sync(self) -> Tuple[bool, str]:
        p = sync_playwright().start(); browser = None
        try:
            browser, ctx = self._build_context(p, headless=True)
            page = ctx.new_page(); self._open_timesheet(page)

            chip = (_get_status_chip_text(page) or "").strip().lower()
            if chip.startswith(("approval pending", "submitted")):
                return True, "ℹ️ Timesheet already submitted for this week (Approval pending)."

            if self._on_login_page(page):
                name = f"napta_login_required_{ts()}.png"; page.screenshot(path=_shot(name), full_page=True)
                return False, f"⛔ Napta login required. Please open Napta once in Chrome. Screenshot -> {name}"

            state = _wait_for_timesheet_ready(page, timeout_ms=SHORT_TIMEOUT_MS)
            if state is None and _has_submit_button(page):
                state = "submit"
            elif state is None:
                return True, "ℹ️ Timesheet already submitted for this week (Approval pending)."

            if state == "create":
                if not _click_create(page):
                    name = f"napta_create_failure_{ts()}.png"; page.screenshot(path=_shot(name), full_page=True)
                    return False, f"❌ Could not click 'Create timesheet'. Screenshot -> {name}"
                state = _wait_for_timesheet_ready(page, timeout_ms=SHORT_TIMEOUT_MS)

            if state == "save":
                if not _click_save(page):
                    name = f"napta_save_failure_{ts()}.png"; page.screenshot(path=_shot(name), full_page=True)
                    return False, f"❌ Could not click 'Save'. Screenshot -> {name}"
                _saw_saved_toast(page)
                state = _wait_for_timesheet_ready(page, timeout_ms=SHORT_TIMEOUT_MS)

            if not _click_submit(page):
                name = f"napta_submit_failure_{ts()}.png"; page.screenshot(path=_shot(name), full_page=True)
                return False, f"❌ Could not click 'Submit for approval'. Screenshot -> {name}"

            with suppress_exc():
                if page.locator("text=Approval pending").count():
                    return True, "✅ Submitted for approval."
            return True, "✅ Submit clicked."
        except NaptaAuthError as e:
            return False, f"⛔ Napta login required. {e}"
        finally:
            with suppress_exc(): 
                if browser: browser.close()
            p.stop()

    # ------------------------------ Login (fast) -------------------------------

    def _login_sync(self) -> Tuple[bool, str]:
        """Headful login; persist session as soon as we detect the app shell."""
        p = sync_playwright().start()
        browser = None
        try:
            browser, ctx = self._build_context(p, headless=False)
            page = ctx.new_page()
            page.goto(DEFAULT_APP_URL, wait_until="domcontentloaded", timeout=30_000)

            def in_app_shell() -> bool:
                if self._on_login_page(page):
                    return False
                for mk in (
                    lambda: page.locator("nav >> text=Timesheets").first,
                    lambda: page.locator("h1:has-text('Timesheet')").first,
                    lambda: page.locator("text=/^W\\d{1,2}\\s+from\\s+\\d{2}-\\d{2}-\\d{4}/i").first,
                ):
                    with suppress_exc():
                        loc = mk()
                        if loc and loc.count() and loc.is_visible():
                            return True
                with suppress_exc():
                    return "app.napta.io" in page.url and "/timesheet" in page.url
                return False

            deadline = time.time() + 10.0  # snappy
            while time.time() < deadline:
                if in_app_shell():
                    with suppress_exc(): ctx.storage_state(path=str(STATE_PATH))
                    return True, "✅ Login captured. You can now run: save / submit."
                with suppress_exc(): page.wait_for_load_state("domcontentloaded", timeout=800)
                time.sleep(0.2)

            name = f"napta_login_timeout_{ts()}.png"
            with suppress_exc(): page.screenshot(path=_shot(name), full_page=True)
            return False, f"Login window timed out. Screenshot -> {name}"

        except Exception as e:
            return False, f"Login failed: {e!s}"
        finally:
            with suppress_exc():
                if browser: browser.close()
            p.stop()

    # --------------------------- CLI compatibility ----------------------------

    def _view_week_sync(self, which: str = "current") -> Tuple[bool, str]:
        p = sync_playwright().start(); browser = None
        try:
            browser, ctx = self._build_context(p, headless=True)
            page = ctx.new_page(); self._open_timesheet(page)

            if self._on_login_page(page):
                name = f"napta_login_required_{ts()}.png"; page.screenshot(path=_shot(name), full_page=True)
                return False, f"⛔ Napta login required. Please open Napta once in Chrome. Screenshot -> {name}"

            if which == "next":
                before = _get_week_title(page)
                if not _go_to_next_week(page):
                    name = f"napta_error_{ts()}.png"; page.screenshot(path=_shot(name), full_page=True)
                    return False, f"❌ Could not navigate to next week. Screenshot -> {name}"
                after = _get_week_title(page)
                if not after or after == before:
                    name = f"napta_nav_verify_{ts()}.png"; page.screenshot(path=_shot(name), full_page=True)
                    return False, f"❌ Navigation didn't land on next week. Screenshot -> {name}"

            _wait_for_timesheet_ready(page, timeout_ms=DEFAULT_TIMEOUT_MS)
            chip = _get_status_chip_text(page) or "unknown"
            rows = _extract_week_rows(page)
            title = _get_week_title(page) or "Week"
            body = _format_rows_table(rows)
            return True, f"🗓 {title}\n{chip}  —  Status: {chip}\n{body}"
        finally:
            with suppress_exc():
                if browser: browser.close()
            p.stop()

    def preview_week(self, iso_week: str, *, leave_details=None):
        return True, "(preview) Using current week; nothing to preview.", None

    def save_week(self, iso_week: str, *, leave_details=None):
        return self.save_current_week()

    def submit_week(self, iso_week: str):
        return self.submit_current_week()
