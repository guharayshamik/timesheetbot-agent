# timesheetbot_agent/fitnet.py
from __future__ import annotations

import json
import re
import os
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

from getpass import getpass
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError

from .storage import get_settings_path

FITNET_LOGIN_URL = "https://palo-it.fitnetmanager.com/FitnetManager/login.xhtml"
FITNET_STATE_PATH = Path.home() / ".tsbot" / "fitnet_storage_state.json"

# XPaths (from user)
X_VAC_LEAVE_BTN = '//*[@id="menuSideNew"]/div[1]/a[4]/div'
X_ADD_BTN       = '//*[@id="historiqueListeCongesCtrl"]/div/div[1]/button[1]/span'
X_LEAVE_SELECT  = '//*[@id="selectAbsenceTypes"]'
X_BEGIN_DATE    = '//*[@id="beginLink0"]'
X_END_DATE      = '//*[@id="endLink0"]'
X_DESIGNATION   = '//*[@id="motifInput"]'
X_CUST_INFORMED = '//*[@id="clientIsInformedAndReplacementChkbx"]/input'
X_SAVE_BTN      = '//*[@id="saveButton"]'

# Network slimming
_BLOCK = (
    "googletagmanager.com", "google-analytics.com", "segment.io", "sentry.io",
    "plausible.io", "fullstory.com", "intercom.io", "hotjar.com",
    "gravatar.com", "unpkg.com",
)

SHORT = 5_000
DEFAULT = 12_000
LONG = 60_000

DEBUG = bool(os.getenv("TSBOT_FITNET_DEBUG"))

@dataclass
class Creds:
    username: str
    password: str

def _ts() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")

def _snap(page, name: str) -> str:
    fn = f"fitnet_{name}_{_ts()}.png"
    try:
        page.screenshot(path=fn, full_page=True)
    except Exception:
        pass
    return fn

def _route_slim(route):
    req = route.request
    if req.resource_type in ("image", "media", "font"):
        return route.abort()
    if req.url.endswith((".map", ".svg")):
        return route.abort()
    if any(h in req.url for h in _BLOCK):
        return route.abort()
    return route.continue_()

# ---------------- Creds ----------------
def _load_creds() -> Optional[Creds]:
    p = get_settings_path()
    if not p.exists():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8")) or {}
        f = raw.get("fitnet") or {}
        u = (f.get("username") or "").strip()
        pw = (f.get("password") or "").strip()
        if u and pw:
            return Creds(u, pw)
    except Exception:
        pass
    return None

def _save_creds(c: Creds) -> None:
    p = get_settings_path()
    raw = {}
    if p.exists():
        try:
            raw = json.loads(p.read_text(encoding="utf-8")) or {}
        except Exception:
            raw = {}
    raw["fitnet"] = {"username": c.username, "password": c.password}
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")

def _get_or_prompt_creds() -> Creds:
    c = _load_creds()
    if c:
        return c
    print("\n— Fitnet login (stored locally in ~/.tsbot/settings.json) —")
    u = input("Fitnet username (email): ").strip()
    pw = getpass("Fitnet password: ").strip()
    c = Creds(u, pw)
    _save_creds(c)
    return c

# ---------------- Browser / context ----------------
def _build_context(p, *, headless: bool = True):
    browser = p.chromium.launch(headless=headless, args=["--disable-dev-shm-usage"])
    if FITNET_STATE_PATH.exists():
        ctx = browser.new_context(
            storage_state=str(FITNET_STATE_PATH),
            viewport={"width": 1400, "height": 900},
        )
    else:
        ctx = browser.new_context(viewport={"width": 1400, "height": 900})
    ctx.route("**/*", _route_slim)
    ctx.set_default_timeout(DEFAULT)
    return browser, ctx

def _looks_logged_in(page) -> bool:
    try:
        if page.locator("xpath=//*[@id='menuSideNew']").count():
            return True
        return page.get_by_role("navigation").filter(
            has_text=re.compile(r"Vacation\s*/\s*Leave|CONG[ÉE]S|ABSENCE", re.I)
        ).count() > 0
    except Exception:
        return False

def _switch_to_last_page(ctx, current_page):
    try:
        if len(ctx.pages) and ctx.pages[-1] is not current_page:
            return ctx.pages[-1]
    except Exception:
        pass
    return current_page

# ---------------- Login ----------------
def _login_if_needed(page, ctx, creds: Creds) -> None:
    page.goto(FITNET_LOGIN_URL, wait_until="domcontentloaded", timeout=LONG)
    if _looks_logged_in(page):
        return

    user_fields = [
        "input[name='j_username']", "#j_username", "input[type='email']",
        "input[name='username']", "input[name='email']", "input[type='text']",
    ]
    pass_fields = [
        "input[name='j_password']", "#j_password", "input[type='password']",
        "input[name='password']",
    ]

    user = None
    for s in user_fields:
        loc = page.locator(s)
        if loc.count():
            user = loc.first
            break
    pwd = None
    for s in pass_fields:
        loc = page.locator(s)
        if loc.count():
            pwd = loc.first
            break

    if user and pwd:
        user.fill(creds.username)
        pwd.fill(creds.password)
        try:
            pwd.press("Enter")
        except Exception:
            pass
        for s in ("input[type='submit']", "button[type='submit']",
                  "xpath=//button[normalize-space()='Sign in']",
                  "xpath=//input[@value='Sign in']",
                  "xpath=//button[contains(., 'Se connecter')]"):
            try:
                page.locator(s).first.click(timeout=SHORT)
                break
            except Exception:
                pass

    # Wait for shell or menu
    try:
        page.wait_for_selector("xpath=//*[@id='menuSideNew']", timeout=LONG)
    except PWTimeoutError:
        raise RuntimeError("Fitnet login failed; check creds or complete SSO once (we will persist the session).")

    # Persist session
    ctx.storage_state(path=str(FITNET_STATE_PATH))

# ---------------- Page helpers ----------------
def _open_leave_dialog(page) -> None:
    # Click "Vacation / Leave"
    btn = page.locator(f"xpath={X_VAC_LEAVE_BTN}")
    btn.wait_for(state="visible", timeout=LONG)
    try:
        btn.scroll_into_view_if_needed()
    except Exception:
        pass
    try:
        btn.click(timeout=LONG)
    except Exception:
        # click via JS as a last resort
        page.evaluate("(el)=>el.click()", btn.element_handle())

    # Wait the list panel then click Add
    page.locator(f"xpath={X_ADD_BTN}").wait_for(state="visible", timeout=LONG)
    add_btn = page.locator(f"xpath={X_ADD_BTN}")
    try:
        add_btn.scroll_into_view_if_needed()
    except Exception:
        pass
    try:
        add_btn.click(timeout=LONG)
    except Exception:
        page.evaluate("(el)=>el.click()", add_btn.element_handle())

    # Wait for the leave dialog (the select must be visible)
    page.locator(f"xpath={X_LEAVE_SELECT}").wait_for(state="visible", timeout=LONG)

def _select_leave_type(page, label_text: str) -> None:
    sel = page.locator(f"xpath={X_LEAVE_SELECT}")
    sel.wait_for(state="visible", timeout=LONG)
    want = (label_text or "").strip().upper()

    try:
        sel.select_option(label=want)
        return
    except Exception:
        pass

    opts = sel.locator("xpath=.//option")
    count = opts.count()
    idx = None
    for i in range(count):
        txt = (opts.nth(i).inner_text() or "").strip().upper()
        if txt == want or want in txt:
            idx = i
            break
    if idx is None:
        raise RuntimeError(f"Leave type '{label_text}' not found in dropdown.")
    value = opts.nth(idx).get_attribute("value") or ""
    sel.select_option(value=value)

def _fill_date(page, link_xpath: str, dt: datetime) -> None:
    ddmmyyyy = dt.strftime("%d/%m/%Y")
    link = page.locator(f"xpath={link_xpath}")
    link.wait_for(state="visible", timeout=LONG)
    inp = link.locator("xpath=preceding-sibling::input[1]")
    if inp.count():
        inp.first.fill(ddmmyyyy)
        return
    tag = link.evaluate("el => el.tagName.toLowerCase()")
    if tag == "input":
        link.fill(ddmmyyyy)
        return
    page.evaluate(
        """(el, v) => {
            const inp = el.previousElementSibling && el.previousElementSibling.tagName==='INPUT'
              ? el.previousElementSibling : el;
            try { inp.removeAttribute('readonly'); } catch(e) {}
            inp.value = v;
            inp.dispatchEvent(new Event('input', {bubbles:true}));
            inp.dispatchEvent(new Event('change', {bubbles:true}));
        }""",
        link.element_handle(), ddmmyyyy
    )

def _fill_designation(page, text: str) -> None:
    inp = page.locator(f"xpath={X_DESIGNATION}")
    inp.wait_for(state="visible", timeout=LONG)
    inp.fill(text)

def _tick_customer_informed(page) -> None:
    chk = page.locator(f"xpath={X_CUST_INFORMED}")
    if chk.count():
        try:
            if not chk.is_checked():
                chk.check()
        except Exception:
            chk.click()

def _click_save(page) -> None:
    btn = page.locator(f"xpath={X_SAVE_BTN}")
    try:
        btn.scroll_into_view_if_needed()
    except Exception:
        pass
    try:
        btn.click(timeout=LONG)
    except Exception:
        page.evaluate("(el)=>el.click()", btn.element_handle())

# ---------------- API ----------------
_LEAVE_MAP = {
    "mc": "SICK LEAVE",
    "sick leave": "SICK LEAVE",
    "annual leave": "ANNUAL LEAVES",
    "annual leaves": "ANNUAL LEAVES",
    "leave in lieu": "LEAVE IN LIEU",
    "unpaid leave": "UNPAID LEAVE",
    "child care leave": "CHILD CARE LEAVE",
    "compassionate leave": "COMPASSIONATE LEAVE",
    "hospitalization leave": "HOSPITALIZATION LEAVE",
}

def _norm_label(s: str) -> str:
    return _LEAVE_MAP.get((s or "").strip().lower(), (s or "").strip().upper())

def apply_leave(
    *,
    start: datetime,
    end: datetime,
    leave_type: str,
    comment: str = "",
    half_day: Optional[str] = None,
    commit: bool = False,
    screenshot_to: Optional[str] = None,
) -> Tuple[bool, str, Optional[str]]:
    creds = _get_or_prompt_creds()
    label = _norm_label(leave_type)
    shot = screenshot_to

    with sync_playwright() as p:
        browser = None
        try:
            # Run headless normally; flip TSBOT_FITNET_DEBUG=1 for headed debug
            browser, ctx = _build_context(p, headless=not DEBUG)
            page = ctx.new_page()

            _login_if_needed(page, ctx, creds)
            page = _switch_to_last_page(ctx, page)

            # Defensive: ensure the left nav exists before we proceed
            try:
                page.locator("xpath=//*[@id='menuSideNew']").wait_for(state="visible", timeout=LONG)
            except Exception:
                if DEBUG:
                    _snap(page, "no_menu_after_login")
                return False, "Fitnet: app shell did not appear after login.", shot

            # Open dialog
            try:
                _open_leave_dialog(page)
            except Exception as e:
                if DEBUG:
                    _snap(page, "open_dialog_failed")
                return False, f"Fitnet: could not open Add dialog ({e}).", shot

            # Fill
            try:
                _select_leave_type(page, label)
                _fill_date(page, X_BEGIN_DATE, start)
                _fill_date(page, X_END_DATE, end)
                _fill_designation(page, "Sr. DevOps Engineer")
                _tick_customer_informed(page)
            except Exception as e:
                if DEBUG:
                    _snap(page, "fill_fields_failed")
                return False, f"Fitnet: could not fill fields ({e}).", shot

            if not commit:
                if shot:
                    _snap(page, "preview")
                return True, "Prefilled in Fitnet (no save).", shot

            # Save
            try:
                _click_save(page)
            except Exception as e:
                if DEBUG:
                    _snap(page, "save_click_failed")
                return False, f"Fitnet: could not click Save ({e}).", shot

            # Verify: dialog goes away OR a row appears quickly. We just wait a bit.
            try:
                page.locator(f"xpath={X_SAVE_BTN}").wait_for(state="detached", timeout=10_000)
            except Exception:
                pass  # some tenants leave the button in DOM; we don't hard-fail here

            if shot:
                _snap(page, "saved")

            return True, "Saved in Fitnet.", shot

        except Exception as e:
            try:
                if not shot:
                    shot = f"fitnet_error_{_ts()}.png"
                if browser:
                    ctx = browser.contexts[0]
                    if ctx.pages:
                        ctx.pages[-1].screenshot(path=shot, full_page=True)
            except Exception:
                pass
            return False, f"Fitnet: {e}", shot
        finally:
            try:
                if browser:
                    browser.close()
            except Exception:
                pass

def login() -> Tuple[bool, str]:
    try:
        _ = _get_or_prompt_creds()
        return True, "✅ Fitnet login saved locally."
    except Exception as e:
        return False, f"❌ Fitnet login not saved: {e}"
