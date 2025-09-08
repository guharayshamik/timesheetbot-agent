# timesheetbot_agent/napta.py
from __future__ import annotations

import re
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import browser_cookie3
from playwright.sync_api import (
    sync_playwright,
    TimeoutError as PWTimeoutError,
    Page,
)

DEFAULT_APP_URL = "https://app.napta.io/timesheet"


# ────────────────────────────────────────────────────────────────────────────────
# Utilities
# ────────────────────────────────────────────────────────────────────────────────

def _monday_of(d: datetime) -> datetime:
    return d - timedelta(days=d.weekday())

def _fmt_ymd(d: datetime) -> str:
    # dd-mm-YYYY as Napta shows it in the header
    return d.strftime("%d-%m-%Y")


# ────────────────────────────────────────────────────────────────────────────────
# Errors
# ────────────────────────────────────────────────────────────────────────────────

class NaptaAuthError(RuntimeError):
    """Raised when SSO cookies are missing/expired."""
    pass


# ────────────────────────────────────────────────────────────────────────────────
# Client
# ────────────────────────────────────────────────────────────────────────────────

class NaptaClient:
    """
    Headless Napta automation using existing browser SSO cookies.

    - No login flow here. If cookies are missing, we tell the user to open
      https://app.napta.io in a browser once to refresh cookies, then retry.
    - We cache cookies for the process lifetime so Keychain is only prompted once.
    """

    # one-process cookie cache to avoid repeated Keychain prompts
    _COOKIE_CACHE: Optional[List[Dict[str, Any]]] = None

    def __init__(self) -> None:
        self._cookie_ok: Optional[bool] = None

    # ── status ─────────────────────────────────────────────────────────────────
    def status(self) -> str:
        ok = self._cookie_ok
        if ok is None:
            return "Auth: will use your browser’s SSO cookies (not checked yet)."
        return "Auth: using browser SSO cookies — OK." if ok else \
               "Auth: missing/expired cookies. Please login to Napta once in your browser."

    # ── cookie loading ─────────────────────────────────────────────────────────
    @classmethod
    def _load_cookies_from_chrome(cls) -> List[Dict[str, Any]]:
        """
        Read *.napta.io cookies from Chrome via Keychain one time per process.
        Normalize types for Playwright (secure/httpOnly/sameSite/expiry).
        """
        if cls._COOKIE_CACHE is not None:
            return cls._COOKIE_CACHE

        cj = browser_cookie3.chrome(domain_name=".napta.io")
        cookies: List[Dict[str, Any]] = []

        now = time.time()
        for c in cj:
            if "napta.io" not in c.domain:
                continue
            exp = getattr(c, "expires", None)
            if exp not in (None, 0) and exp < now:
                continue

            secure = bool(getattr(c, "secure", False))
            http_only = bool(getattr(getattr(c, "_rest", {}), "get", lambda *_: False)("httponly"))
            rest = getattr(c, "_rest", {})
            same_site = rest.get("samesite")
            ss_norm: Optional[str] = None
            if isinstance(same_site, str):
                s = same_site.strip().lower()
                if s in ("lax", "strict", "none"):
                    ss_norm = s.capitalize()

            item: Dict[str, Any] = {
                "name": c.name,
                "value": c.value,
                "domain": c.domain,
                "path": c.path or "/",
                "secure": secure,
                "httpOnly": http_only,
            }
            if ss_norm:
                item["sameSite"] = ss_norm
            if exp not in (None, 0):
                item["expires"] = int(exp)

            cookies.append(item)

        if not cookies:
            raise NaptaAuthError(
                "No Napta cookies found. Please open https://app.napta.io in your browser, "
                "login once with SSO, then retry."
            )

        cls._COOKIE_CACHE = cookies
        return cookies

    @classmethod
    def _inject_cookies(cls, context) -> None:
        cookies = cls._load_cookies_from_chrome()
        # add in small batches — Playwright rejects the whole array if one has an issue
        batch: List[Dict[str, Any]] = []
        for ck in cookies:
            batch.append(ck)
            if len(batch) >= 50:
                context.add_cookies(batch)
                batch = []
        if batch:
            context.add_cookies(batch)

    # ── playwright context helpers ─────────────────────────────────────────────
    def _new_context(self):
        p = sync_playwright().start()
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1600, "height": 1000})
        try:
            self._inject_cookies(ctx)
            self._cookie_ok = True
        except NaptaAuthError:
            self._cookie_ok = False
            browser.close()
            p.stop()
            raise
        return p, browser, ctx

    def _open_timesheet(self, page: Page) -> None:
        page.goto(DEFAULT_APP_URL, wait_until="domcontentloaded")
        # If the app pulls extra data, wait a bit for the grid to settle
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except PWTimeoutError:
            pass
        # presence check (one of these will exist)
        try:
            page.wait_for_selector("text=Timesheet", timeout=8000)
        except PWTimeoutError:
            page.wait_for_selector("text=MONDAY", timeout=8000)

    # ── generic UI helpers ────────────────────────────────────────────────────
    def _page_text(self, page: Page) -> str:
        try:
            return page.evaluate("() => document.body.innerText") or ""
        except Exception:
            return ""

    _HDR_RANGE_RE = re.compile(
        r"W(\d+)\s+from\s+(\d{2}-\d{2}-\d{4})\s+to\s+(\d{2}-\d{2}-\d{4})",
        re.I,
    )
    _HDR_STAMP_RE = re.compile(r"W(\d+)\s*-\s*[A-Za-z]+\s+\d{4}", re.I)

    def _read_header_range(self, page: Page) -> Optional[Tuple[int, str, str]]:
        """
        Return (week_number, start_dd-mm-yyyy, end_dd-mm-yyyy) if detectable.
        Matches either the main banner “Wxx from … to …” or the right stamp
        “Wxx – MONTH YYYY”.
        """
        txt = self._page_text(page)
        m = self._HDR_RANGE_RE.search(txt)
        if m:
            return int(m.group(1)), m.group(2), m.group(3)
        m2 = self._HDR_STAMP_RE.search(txt)
        if m2:
            wk = int(m2.group(1))
            return wk, "", ""
        return None

    def _click_button(self, page: Page, label: str, *, strict: bool = True) -> None:
        """
        Robust click by label; tries role, visible text, and DOM evaluation.
        If strict=True, raise on failure.
        """
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
                loc.click(timeout=3000)
                return
            except Exception:
                continue

        ok = page.evaluate(
            """(txt) => {
                const match = el => el && el.textContent && el.textContent.trim() === txt;
                const btns = Array.from(document.querySelectorAll('button, [role="button"]'));
                const el = btns.find(b => match(b) || (b.getAttribute('aria-label')||'').trim() === txt);
                if (el) { el.click(); return true; }
                return false;
            }""",
            label,
        )
        if not ok and strict:
            raise PWTimeoutError(f"Could not find a clickable '{label}' button")

    def _click_week_chevron(self, page: Page, direction: str) -> None:
        """
        Click the chevron immediately before/after the “This week” chip by DOM traversal.
        direction: 'prev' or 'next'
        """
        ok = page.evaluate(
            """(dir) => {
                const buttons = Array.from(document.querySelectorAll('button, [role=button]'));
                const chip = buttons.find(b => (b.textContent||'').trim() === 'This week');
                if (!chip) return 'no-chip';
                let p = chip; let tries = 0;
                while (p && tries++ < 4) {
                  p = p.parentElement;
                  if (!p) break;
                  const btns = Array.from(p.querySelectorAll('button'));
                  const idx = btns.indexOf(chip);
                  if (idx !== -1) {
                    const target = dir === 'prev' ? btns[idx-1] : btns[idx+1];
                    if (target) { target.click(); return 'ok'; }
                  }
                }
                return 'no-sibling';
            }""",
            direction,
        )
        if ok != "ok":
            raise PWTimeoutError(f"Chevron click failed: {ok}")

    def _go_to_this_week(self, page: Page) -> None:
        try:
            self._click_button(page, "This week", strict=True)
            page.wait_for_load_state("domcontentloaded", timeout=8000)
            try:
                page.wait_for_load_state("networkidle", timeout=8000)
            except PWTimeoutError:
                pass
        except Exception:
            # chip not present means we are already on this week
            pass

    def _goto_week_offset(self, page: Page, offset_weeks: int) -> None:
        """
        0=this week; +1 next; -1 previous. We:
          * click chevrons relative to current view,
          * then verify via header text that week number/date changed accordingly.
        """
        if offset_weeks == 0:
            return

        today = datetime.now()
        target_monday = _monday_of(today) + timedelta(days=7 * offset_weeks)
        target_start = _fmt_ymd(target_monday)
        target_wk = int(target_monday.isocalendar()[1])

        step = "next" if offset_weeks > 0 else "prev"
        tries = abs(offset_weeks) + 4  # little cushion
        for _ in range(tries):
            self._click_week_chevron(page, step)
            page.wait_for_load_state("domcontentloaded", timeout=8000)
            try:
                page.wait_for_load_state("networkidle", timeout=8000)
            except PWTimeoutError:
                pass
            time.sleep(0.25)

            hdr = self._read_header_range(page)
            if hdr:
                wk, start, _ = hdr
                if wk == target_wk:
                    if start and start == target_start:
                        return
                    # Week number alone is good enough when only the stamp is present
                    return

        raise PWTimeoutError(
            f"Could not navigate to target week (wanted W{target_wk} starting {target_start})."
        )

    # ── result watchers ────────────────────────────────────────────────────────
    def _wait_saved(self, page: Page) -> bool:
        # Look for 'Saved' legend or a brief toast; try for ~5s
        for _ in range(20):
            try:
                if page.locator("text=Saved").first.is_visible():
                    return True
            except Exception:
                pass
            time.sleep(0.25)
        return False

    def _wait_submitted(self, page: Page) -> bool:
        # Look for 'Approval pending' chip; try for ~6s
        for _ in range(24):
            try:
                if page.locator("text=Approval pending").first.is_visible():
                    return True
            except Exception:
                pass
            time.sleep(0.25)
        return False

    # ── single-week public helpers (anchor: 'This week') ───────────────────────
    def save_current_week(self, *, all_day_1d: bool = False) -> Tuple[bool, str]:
        try:
            p, browser, ctx = self._new_context()
        except NaptaAuthError as e:
            return False, f"❌ {e}"

        page = ctx.new_page()
        self._open_timesheet(page)
        self._go_to_this_week(page)

        # FUTURE: if all_day_1d: fill first row Mon–Fri with "1d" each.

        try:
            self._click_button(page, "Save", strict=True)
        except Exception as e:
            browser.close(); p.stop()
            return False, f"❌ Could not click 'Save' ({e}). Maybe already saved."

        ok = self._wait_saved(page)
        browser.close(); p.stop()
        return (True, "✅ Saved (draft).") if ok else \
               (False, "ℹ️ Save click sent, but no confirmation (no 'Saved' chip).")

    def submit_current_week(self) -> Tuple[bool, str]:
        try:
            p, browser, ctx = self._new_context()
        except NaptaAuthError as e:
            return False, f"❌ {e}"

        page = ctx.new_page()
        self._open_timesheet(page)
        self._go_to_this_week(page)

        if page.locator("text='Not created'").first.is_visible():
            browser.close(); p.stop()
            return False, "⚠️ Week is not saved yet. /save first."
        if page.locator("text='Approval pending'").first.is_visible():
            browser.close(); p.stop()
            return True, "ℹ️ Already submitted (Approval pending)."

        try:
            self._click_button(page, "Submit for approval", strict=True)
            try:
                self._click_button(page, "Submit", strict=False)  # confirm dialog
            except Exception:
                pass
        except Exception as e:
            browser.close(); p.stop()
            return False, f"❌ Could not click 'Submit for approval' ({e})."

        ok = self._wait_submitted(page)
        browser.close(); p.stop()
        return (True, "✅ Submitted for approval.") if ok else \
               (False, "ℹ️ Submit clicked, but UI didn’t confirm. Check in Napta.")

    # ── relative-week helpers (offset from 'This week') ────────────────────────
    def save_by_offset(self, offset_weeks: int) -> Tuple[bool, str]:
        try:
            p, browser, ctx = self._new_context()
        except NaptaAuthError as e:
            return False, f"❌ {e}"

        page = ctx.new_page()
        self._open_timesheet(page)
        self._go_to_this_week(page)

        try:
            self._goto_week_offset(page, offset_weeks)
        except Exception as nav_err:
            browser.close(); p.stop()
            return False, f"❌ Navigation failed: {nav_err}"

        try:
            self._click_button(page, "Save", strict=True)
        except Exception as e:
            browser.close(); p.stop()
            return False, f"❌ Could not click 'Save' ({e}). Maybe already saved."

        ok = self._wait_saved(page)
        browser.close(); p.stop()
        return (True, f"✅ Save on week {offset_weeks:+d} from 'This week'.") if ok else \
               (False, "ℹ️ Save click sent, but no confirmation from UI.")

    def submit_by_offset(self, offset_weeks: int) -> Tuple[bool, str]:
        try:
            p, browser, ctx = self._new_context()
        except NaptaAuthError as e:
            return False, f"❌ {e}"

        page = ctx.new_page()
        self._open_timesheet(page)
        self._go_to_this_week(page)

        try:
            self._goto_week_offset(page, offset_weeks)
        except Exception as nav_err:
            browser.close(); p.stop()
            return False, f"❌ Navigation failed: {nav_err}"

        if page.locator("text='Not created'").first.is_visible():
            browser.close(); p.stop()
            return False, "⚠️ Week is not saved yet. /save first."
        if page.locator("text='Approval pending'").first.is_visible():
            browser.close(); p.stop()
            return True, "ℹ️ Already submitted (Approval pending)."

        try:
            self._click_button(page, "Submit for approval", strict=True)
            try:
                self._click_button(page, "Submit", strict=False)
            except Exception:
                pass
        except Exception as e:
            browser.close(); p.stop()
            return False, f"❌ Could not click 'Submit for approval' ({e})."

        ok = self._wait_submitted(page)
        browser.close(); p.stop()
        return (True, "✅ Submitted for approval.") if ok else \
               (False, "ℹ️ Submit clicked, but UI didn’t confirm. Check in Napta.")

    # ── month helpers (current calendar month) ─────────────────────────────────
    def _collect_offsets_in_month(self, page: Page, max_weeks: int = 5) -> List[int]:
        """
        From 'This week', determine which forward offsets (0..n) still belong
        to the same calendar month, based on the *start date* shown in the header.
        Returns offsets like [0, 1, 2, ...].
        """
        self._go_to_this_week(page)
        # Read the month of the current week's Monday
        meta0 = self._read_header_range(page)
        if not meta0 or not meta0[1]:
            # Fallback to local calendar if date range isn't rendered
            start0 = _monday_of(datetime.now())
            month0 = start0.month
        else:
            start0 = datetime.strptime(meta0[1], "%d-%m-%Y")
            month0 = start0.month

        offsets: List[int] = [0]

        # Probe forward up to max_weeks-1 more
        for off in range(1, max_weeks):
            try:
                # navigate from THIS WEEK each time to a *known* offset
                self._go_to_this_week(page)
                self._goto_week_offset(page, off)
                hdr = self._read_header_range(page)
                if hdr and hdr[1]:
                    start = datetime.strptime(hdr[1], "%d-%m-%Y")
                    if start.month == month0:
                        offsets.append(off)
                    else:
                        break
                else:
                    # If only the stamp is present, approximate by local calendar
                    guess = _monday_of(datetime.now()) + timedelta(days=7 * off)
                    if guess.month == month0:
                        offsets.append(off)
                    else:
                        break
            except Exception:
                # If we can't verify, stop expanding
                break

        # return to this week for cleanliness
        self._go_to_this_week(page)
        return offsets

    def _batch_do(self, offsets: List[int], action: str) -> Tuple[bool, str]:
        """
        Run save/submit across multiple offsets. Each offset navigation
        begins from 'This week' for correctness.
        """
        assert action in ("save", "submit")

        try:
            p, browser, ctx = self._new_context()
        except NaptaAuthError as e:
            return False, f"❌ {e}"

        page = ctx.new_page()
        self._open_timesheet(page)

        any_ok = False
        lines: List[str] = []

        for off in offsets:
            try:
                self._go_to_this_week(page)
                self._goto_week_offset(page, off)
            except Exception as nav_err:
                lines.append(f"❌ Navigation failed for offset {off:+d}: {nav_err}")
                continue

            if action == "save":
                try:
                    self._click_button(page, "Save", strict=True)
                except Exception as e:
                    lines.append(f"❌ Could not click 'Save' ({e}). Maybe already saved.")
                    continue
                ok = self._wait_saved(page)
                any_ok |= ok
                lines.append(("✅ " if ok else "ℹ️ ") + f"Save on week {off:+d} from 'This week'.")
            else:
                if page.locator("text='Not created'").first.is_visible():
                    lines.append("⚠️ Week not saved yet; skipping submit.")
                    continue
                if page.locator("text='Approval pending'").first.is_visible():
                    lines.append("ℹ️ Already submitted (Approval pending).")
                    continue
                try:
                    self._click_button(page, "Submit for approval", strict=True)
                    try:
                        self._click_button(page, "Submit", strict=False)
                    except Exception:
                        pass
                except Exception as e:
                    lines.append(f"❌ Could not click 'Submit for approval' ({e}).")
                    continue
                ok = self._wait_submitted(page)
                any_ok |= ok
                lines.append(("✅ " if ok else "ℹ️ ") + "Submitted for approval.")

        browser.close(); p.stop()
        return any_ok, "\n".join(lines)

    # public month API used by CLI
    def save_month_choice(self, choice: str | int) -> Tuple[bool, str]:
        """
        choice: 'all' or week index 1..5 (1=this week, 2=next, etc).
        Operates within the current calendar month only.
        """
        try:
            p, browser, ctx = self._new_context()
        except NaptaAuthError as e:
            return False, f"❌ {e}"
        page = ctx.new_page()
        self._open_timesheet(page)

        if str(choice).lower() == "all":
            offsets = self._collect_offsets_in_month(page)
        else:
            try:
                idx = int(choice)
                if idx < 1 or idx > 5:
                    raise ValueError
            except Exception:
                browser.close(); p.stop()
                return False, "⚠️ Week must be 'all' or a number 1–5."
            offsets = [idx - 1]

        browser.close(); p.stop()
        return self._batch_do(offsets, action="save")

    def submit_month_choice(self, choice: str | int) -> Tuple[bool, str]:
        """
        choice: 'all' or week index 1..5 (1=this week, 2=next, etc).
        Operates within the current calendar month only.
        """
        try:
            p, browser, ctx = self._new_context()
        except NaptaAuthError as e:
            return False, f"❌ {e}"
        page = ctx.new_page()
        self._open_timesheet(page)

        if str(choice).lower() == "all":
            offsets = self._collect_offsets_in_month(page)
        else:
            try:
                idx = int(choice)
                if idx < 1 or idx > 5:
                    raise ValueError
            except Exception:
                browser.close(); p.stop()
                return False, "⚠️ Week must be 'all' or a number 1–5."
            offsets = [idx - 1]

        browser.close(); p.stop()
        return self._batch_do(offsets, action="submit")

    # ── legacy stubs kept for compatibility with older callers ────────────────
    def whoami(self) -> Dict[str, Any]:
        try:
            p, browser, ctx = self._new_context()
        except NaptaAuthError as e:
            raise NaptaAuthError(str(e))
        page = ctx.new_page()
        self._open_timesheet(page)
        browser.close(); p.stop()
        return {"ok": True, "via": "cookies"}

    def preview_week(self, iso_week: str, *, leave_details=None):
        return True, "(preview) Using current week in UI; nothing to preview.", None

    def save_week(self, iso_week: str, *, leave_details=None):
        ok, msg = self.save_current_week(all_day_1d=False)
        return ok, msg

    def submit_week(self, iso_week: str):
        ok, msg = self.submit_current_week()
        return ok, msg

    def preview_month(self, month_text: str, *, leave_details=None):
        return True, "(preview) Month flow not implemented; use /save or /submit for this week.", None

    def save_month(self, month_text: str, *, leave_details=None):
        ok, msg = self.save_current_week(all_day_1d=False)
        return ok, f"{msg} (Note: month-wide save not implemented; saved current week.)"

    def submit_month(self, month_text: str):
        ok, msg = self.submit_current_week()
        return ok, f"{msg} (Note: month-wide submit not implemented; submitted current week.)"
