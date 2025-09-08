# timesheetbot_agent/fitnet.py
from __future__ import annotations

from pathlib import Path
from typing import Optional, Tuple, Union
from datetime import date, datetime
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

# ── Config ─────────────────────────────────────────────────────────────────────

BASE_URL = "https://palo-it.fitnetmanager.com"
STORAGE_STATE = Path.home() / ".tsbot" / "fitnet_state.json"

# Map bot leave names -> Fitnet “Holiday distribution” options (tweak if needed)
FITNET_LEAVE_LABELS = {
    "Sick Leave": "SICK LEAVE",
    "Annual Leave": "ANNUAL LEAVES",
    "Childcare Leave": "CHILDCARE LEAVE",
    "NS Leave": "NS LEAVE",
    "Weekend Efforts": "LEAVE IN LIEU",
    "Public Holiday Efforts": "LEAVE IN LIEU",
    "Half Day": "ANNUAL LEAVES",  # duration set separately
}

# ── Helpers ────────────────────────────────────────────────────────────────────

def _ensure_state_dir() -> None:
    STORAGE_STATE.parent.mkdir(parents=True, exist_ok=True)

def _fmt_d(d: datetime) -> str:
    """Fitnet typically uses dd/MM/yyyy."""
    return d.strftime("%d/%m/%Y")

def _coerce_date(v: Union[str, date, datetime]) -> datetime:
    """Accept a bunch of formats and return a date-only datetime."""
    if isinstance(v, datetime):
        return datetime(v.year, v.month, v.day)
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day)
    s = str(v).strip()
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d %b %Y", "%d %B %Y"):
        try:
            dt = datetime.strptime(s, fmt)
            return datetime(dt.year, dt.month, dt.day)
        except ValueError:
            pass
    # last resort
    try:
        dt = datetime.fromisoformat(s)
        return datetime(dt.year, dt.month, dt.day)
    except Exception:
        # Let caller turn this into a friendly message
        raise ValueError(f"Unrecognized date format: {v!r}")

def _norm_half_day(v: Optional[str]) -> Optional[str]:
    if not v:
        return None
    s = str(v).strip().lower()
    if s in ("am", "morning", "a.m.", "am."):
        return "AM"
    if s in ("pm", "afternoon", "p.m.", "pm."):
        return "PM"
    return None

def _state_ok() -> bool:
    """Does a saved login exist and look non-empty?"""
    try:
        return STORAGE_STATE.exists() and STORAGE_STATE.stat().st_size > 50
    except Exception:
        return False

# ── Session bootstrap ──────────────────────────────────────────────────────────

def login_interactive() -> Tuple[bool, str]:
    """
    One-time login capture. Opens a visible browser; user completes SSO.
    After user presses Enter in the terminal, cookies are saved for reuse.
    """
    try:
        _ensure_state_dir()
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            ctx = browser.new_context()
            page = ctx.new_page()
            page.set_default_timeout(15000)

            page.goto(BASE_URL, wait_until="load")
            print("➡️  Please complete Fitnet login in the opened browser.")
            print("   When you see the main menu (TIMESHEET / VACATION / LEAVE),")
            input("   press Enter here to save your session… ")

            ctx.storage_state(path=str(STORAGE_STATE))
            browser.close()

        if _state_ok():
            return True, f"✅ Fitnet session saved to {STORAGE_STATE}"
        else:
            return False, (
                "❌ Could not save session cookies. "
                "Please try `/login` again (keep the browser open until you press Enter)."
            )
    except Exception as e:
        return False, f"❌ Login capture failed: {e}"

def _ctx():
    """
    Start Playwright using saved cookies.
    Caller MUST wrap this in try/except — this can raise RuntimeError.
    """
    if not _state_ok():
        raise RuntimeError(
            f"Not logged in. Run `/login` in the Fitnet menu, complete SSO, "
            f"then press Enter. (Expected state at: {STORAGE_STATE})"
        )
    p = sync_playwright().start()
    browser = p.chromium.launch(headless=False)  # visible == safer in prod
    ctx = browser.new_context(storage_state=str(STORAGE_STATE))
    return p, browser, ctx

# ── Navigation helpers ─────────────────────────────────────────────────────────

def _goto_vacation_leave(page) -> bool:
    """
    Try direct URL, then the home tile. Return True if an 'Add' button is present.
    """
    def _try_direct() -> bool:
        try:
            page.goto(f"{BASE_URL}/FitnetManager/saisieConge.xhtml",
                      wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(500)
            return _find_add_button(page) is not None
        except PWTimeout:
            return False
        except Exception:
            return False

    def _try_tile() -> bool:
        try:
            page.goto(BASE_URL, wait_until="domcontentloaded", timeout=20000)
            for label in ("VACATION / LEAVE", "Vacation / Leave", "Leave", "Vacation"):
                loc = page.get_by_text(label, exact=False)
                if loc.count() > 0:
                    loc.first.click()
                    page.wait_for_timeout(600) 
                    break
            page.wait_for_timeout(1000)
            return _find_add_button(page) is not None
        except Exception:
            return False

    return _try_direct() or _try_tile()


def _find_add_button(page):
    """Return a locator for the Add/New button using many fallbacks, or None."""
    texts = ["Add", "New", "Create", "Add request", "New request"]
    selectors = [
        'button:has-text("{t}")',
        'a:has-text("{t}")',
        '[role="button"]:has-text("{t}")',
        'button[title*="{t}"]',
        'span.ui-button-text:has-text("{t}")',
        'span:has-text("{t}") >> xpath=ancestor::button[1]',
        # common icon buttons
        'i[class*="pi-plus"] >> xpath=ancestor::button[1]',
        'i[class*="fa-plus"] >> xpath=ancestor::button[1]',
        # generic text locator fallback
        'text="{t}"',
    ]
    for t in texts:
        for tpl in selectors:
            sel = tpl.format(t=t)
            try:
                loc = page.locator(sel)
                if loc.count() > 0:
                    # if it's a plain text locator, climb to the closest button if possible
                    try:
                        btn = loc.first.locator("xpath=ancestor::button[1]")
                        if btn.count() > 0:
                            return btn.first
                    except Exception:
                        pass
                    return loc.first
            except Exception:
                continue
    return None



# ── Main action ────────────────────────────────────────────────────────────────

def apply_leave(
    start: Union[str, date, datetime],
    end: Union[str, date, datetime],
    leave_type: str,
    comment: str = "",
    half_day: Optional[str] = None,
    *,
    commit: bool = False,                  # default = preview only (no save)
    screenshot_to: Optional[Path] = None,  # optional preview screenshot
) -> Tuple[bool, str, Optional[Path]]:
    """
    Fill Fitnet ‘VACATION / LEAVE → Add’ form.

    Returns: (ok, message, screenshot_path_if_any)

    Behavior:
      • commit=False (default): fill the form, optionally take a screenshot,
        and STOP before saving — safe for production testing.
      • commit=True: attempt to click Save/Record/Validate.
    """
    # Normalize inputs (convert errors to clear messages)
    try:
        start_dt = _coerce_date(start)
        end_dt = _coerce_date(end)
        half_day = _norm_half_day(half_day)
    except Exception as e:
        return False, f"⚠️ Date parsing error: {e}", None

    label = FITNET_LEAVE_LABELS.get(leave_type)
    if not label:
        return False, f"⚠️ Leave type not mapped to Fitnet: {leave_type}", None

    p = browser = ctx = None
    shot_path: Optional[Path] = None

    try:
        # Create session context (friendly message if not logged in)
        try:
            p, browser, ctx = _ctx()
        except RuntimeError as e:
            return False, f"⚠️ {e}", None

        page = ctx.new_page()

        # Click Add
        try:
            if not _goto_vacation_leave(page):
                return False, "❌ Couldn't reach the VACATION / LEAVE screen.", None


            add_btn = _find_add_button(page)
            if not add_btn:
                return False, "❌ Couldn't find the 'Add' button on VACATION / LEAVE.", None
            add_btn.click()
            page.wait_for_timeout(400)

        except Exception:
            return False, "❌ Couldn't find the 'Add' button on VACATION / LEAVE.", None

        # Holiday distribution
        try:
            # Prefer label; fall back to nearby dropdown if needed
            try:
                page.get_by_label("Holiday distribution").click()
            except Exception:
                page.locator("label:has-text('Holiday distribution')").first.click()
            page.get_by_role("option", name=label).click()
        except Exception:
            return False, f"❌ Couldn't select Holiday distribution '{label}'.", None

        # Begin / End
        try:
            try:
                page.get_by_label("Begin").fill(_fmt_d(start_dt))
            except Exception:
                page.locator("label:has-text('Begin')").locator("xpath=following::input[1]").fill(_fmt_d(start_dt))
            try:
                page.get_by_label("End").fill(_fmt_d(end_dt))
            except Exception:
                page.locator("label:has-text('End')").locator("xpath=following::input[1]").fill(_fmt_d(end_dt))
        except Exception:
            return False, "❌ Couldn't fill Begin/End dates.", None

        # Half-day (optional)
        if half_day in ("AM", "PM"):
            try:
                try:
                    page.get_by_label("Duration").click()
                except Exception:
                    page.locator("label:has-text('Duration')").first.click()
                for opt in ("1/2 day", "0.5 day", "Half day"):
                    if page.get_by_role("option", name=opt).count() > 0:
                        page.get_by_role("option", name=opt).click()
                        break
                try:
                    page.get_by_label("Half-day").click()
                except Exception:
                    page.locator("label:has-text('Half')").first.click()
                part = "morning" if half_day == "AM" else "afternoon"
                for opt in (part.capitalize(), part, "AM" if half_day == "AM" else "PM"):
                    if page.get_by_role("option", name=opt).count() > 0:
                        page.get_by_role("option", name=opt).click()
                        break
            except Exception:
                return False, "❌ Couldn't set half-day options.", None

        # Comment (optional)
        if comment:
            try:
                try:
                    page.get_by_label("Comment").fill(comment)
                except Exception:
                    page.locator("label:has-text('Comment')").locator(
                        "xpath=following::textarea|following::input[1]"
                    ).first.fill(comment)
            except Exception:
                # Not fatal — continue without a comment
                pass

        # Optional screenshot before saving
        if screenshot_to:
            try:
                screenshot_to.parent.mkdir(parents=True, exist_ok=True)
                page.wait_for_timeout(300)
                page.screenshot(path=str(screenshot_to), full_page=True)
                shot_path = screenshot_to
            except Exception:
                # Non-fatal; continue
                pass

        if not commit:
            return (
                True,
                "👀 Preview ready — form filled, NOT saved.\n"
                "   Review in the opened browser and click Save manually if all looks good.",
                shot_path,
            )

        # Save (tenants differ: Save / Record / Validate / Submit / Enregistrer)
        for button_name in ("Save", "Record", "Validate", "Submit", "Enregistrer"):
            try:
                if page.get_by_role("button", name=button_name).count() > 0:
                    page.get_by_role("button", name=button_name).click()
                    page.wait_for_timeout(800)
                    return True, "✅ Fitnet leave created (saved).", shot_path
            except Exception:
                continue

        return False, "❌ Couldn't find a Save/Record/Validate button.", shot_path

    except Exception as e:
        # Catch-all to keep the CLI clean
        return False, f"❌ Fitnet automation failed: {e}", shot_path
    finally:
        try:
            if browser:
                browser.close()
        except Exception:
            pass
        try:
            if p:
                p.stop()
        except Exception:
            pass


# ── Optional convenience APIs used by the CLI (status, logout) ────────────────

def session_status() -> Tuple[bool, str]:
    """Quick check the saved cookie state."""
    if _state_ok():
        return True, f"✅ Logged in (cookies at {STORAGE_STATE})"
    return False, "⚠️ Not logged in. Use `/login` in the Fitnet menu."

def logout_state() -> Tuple[bool, str]:
    """Delete saved cookie state."""
    try:
        if STORAGE_STATE.exists():
            STORAGE_STATE.unlink()
        return True, "✅ Fitnet session cleared."
    except Exception as e:
        return False, f"❌ Couldn't clear session: {e}"
