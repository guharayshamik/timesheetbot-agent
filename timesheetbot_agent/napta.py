# timesheetbot_agent/napta.py
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

# Optional dependency; used only as a fallback
try:
    import browser_cookie3
except Exception:  # pragma: no cover
    browser_cookie3 = None  # type: ignore

from playwright.sync_api import sync_playwright
from playwright._impl._errors import TimeoutError as PlaywrightTimeoutError


# ──────────────────────────────── Config / Paths ───────────────────────────────

DEFAULT_APP_URL = "https://app.napta.io/timesheet"

# Fallback XPaths (last-resort)
SAVE_BTN_XPATH = '/html/body/div[1]/div[2]/div[1]/div[2]/div[2]/button'
THIS_WEEK_BTN_XPATH = '/html/body/div[1]/div[2]/div[1]/div[2]/div[2]/div/button[2]'
NEXT_WEEK_CY = '[data-cy="PeriodNavigation_navRight"]'
NEXT_WEEK_BTN_XPATH = '/html/body/div[1]/div[2]/div[1]/div[2]/div[2]/div/button[3]'
CREATE_BTN_XPATH = '//button[contains(normalize-space(.), "Create")]'
CREATE_TIMESHEET_XPATH = '//button[contains(normalize-space(.), "Create timesheet")]'

# Store under ~/.tsbot/napta (tighter perms than ~/.cache)
_APP_DIR = Path.home() / ".tsbot" / "napta"
_APP_DIR.mkdir(parents=True, exist_ok=True)
try:
    os.chmod(_APP_DIR, 0o700)
except Exception:
    pass

STATE_PATH = _APP_DIR / "napta_storage_state.json"   # canonical storage_state
_COOKIE_CACHE = _APP_DIR / "napta_cookies.json"      # fallback only
_SCREENSHOT_DIR = _APP_DIR / "shots"
_SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

def _shot(name: str) -> str:
    return str(_SCREENSHOT_DIR / name)

# Slim some network requests (helps speed)
_ANALYTICS_HOSTS = (
    "googletagmanager.com", "google-analytics.com", "segment.io", "sentry.io",
    "plausible.io", "fullstory.com", "intercom.io", "hotjar.com",
    "gravatar.com", "unpkg.com",
)

# Timeouts
SHORT_TIMEOUT_MS = 4_000
DEFAULT_TIMEOUT_MS = int(os.environ.get("NAPTA_TIMEOUT_MS", "30000"))  # 30s

UA_DESKTOP = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
)

# ────────────────────────────────── Utilities ─────────────────────────────────

def ts() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")

class suppress_exc:
    def __init__(self, raise_on_fail: bool = False):
        self.raise_on_fail = raise_on_fail
        self._exc = None
    def __enter__(self):
        return self
    def __exit__(self, exc_type, exc, tb):
        self._exc = exc
        return not self.raise_on_fail

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

class NaptaAuthError(RuntimeError):
    pass

def _proxy_conf():
    url = os.getenv("PLAYWRIGHT_PROXY") or os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY")
    return {"server": url} if url else None

def _safe_run(fn, label: str = "operation"):
    """Run a Playwright action with graceful timeout recovery."""
    try:
        return fn(), None
    except PlaywrightTimeoutError:
        return None, f"⏰ The {label} took too long (timed out). Please retry in a few seconds."
    except Exception as e:
        return None, f"⚠️ Unexpected error during {label}: {e}"


# ────────────────────────────── Page read helpers ─────────────────────────────

def _get_status_chip_text(page) -> str:
    containers = [
        "header",
        "main >> div:near(:text('This week'), 800)",
        "main >> div:has(button:has-text('Submit for approval'))",
        "main >> div:has(button:has-text('Save'))",
        "main",
    ]
    status_regex = r'^(Not created|Draft|Open|Validated|Approval pending|Submitted)$'
    for scope in containers:
        with suppress_exc():
            loc = page.locator(f"{scope} >> text=/{status_regex}/i").first
            if loc.count():
                text = (loc.inner_text() or "").strip()
                if text and len(text) <= 30:
                    return text
    return ""



def _get_week_title(page) -> str:
    # Prefer common date-range labels like "21–25 Oct 2025" or "21 Oct – 25 Oct"
    date_word = r"(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
    range_re = re.compile(
        rf"\b(\d{{1,2}}(?:\s*[–-]\s*\d{{1,2}})?\s*{date_word}(?:\s*[–-]\s*\d{{1,2}}\s*{date_word})?\s*(?:\d{{4}})?)\b",
        re.I,
    )
    # Support numeric styles, e.g. "W45 from 03-11-2025 to 09-11-2025" or "03-11-2025 – 09-11-2025"
    numeric_w_re = re.compile(
        r"\bW\d{1,2}\s+from\s+\d{2}-\d{2}-\d{4}\s+to\s+\d{2}-\d{2}-\d{4}\b",
        re.I,
    )
    numeric_range_re = re.compile(
        r"\b\d{1,2}-\d{1,2}-\d{4}\s*(?:–|-|to)\s*\d{1,2}-\d{1,2}-\d{4}\b",
        re.I,
    )

    # A) Period label near navigation
    with suppress_exc():
        loc = page.locator('[data-cy*="Period"][data-cy*="Label"], [aria-live="polite"]').first
        if loc.count():
            txt = (loc.inner_text() or "").strip()
            # Try month-name range
            m = range_re.search(txt)
            if m:
                return m.group(1)
            # Try numeric "W## from DD-MM-YYYY to DD-MM-YYYY"
            m2 = numeric_w_re.search(txt)
            if m2:
                return m2.group(0)
            # Try generic numeric "DD-MM-YYYY – DD-MM-YYYY"
            m3 = numeric_range_re.search(txt)
            if m3:
                return m3.group(0)

    # B) Any visible element that looks like a date-range (month-name)
    with suppress_exc():
        loc = page.locator("text=/\\d{1,2}\\s*[–-]\\s*\\d{1,2}\\s*(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)/i").first
        if loc.count():
            return (loc.inner_text() or "").strip()

    # B2) Any visible element that looks like a purely numeric range
    with suppress_exc():
        loc = page.locator("text=/\\b\\d{2}-\\d{2}-\\d{4}\\s*(?:–|-|to)\\s*\\d{2}-\\d{2}-\\d{4}\\b/i").first
        if loc.count():
            return (loc.inner_text() or "").strip()

    # C) Back-compat headings
    with suppress_exc():
        h = page.locator("h1,h2,h3").filter(has_text=re.compile(r"week", re.I)).first
        if h.count():
            t = (h.inner_text() or "").strip()
            if t:
                return t

    # D) "Week 42"
    with suppress_exc():
        t = page.locator("text=/Week\\s+\\d+/i").first
        if t.count():
            return (t.inner_text() or "").strip()
    return ""

def _labels_from_title(title: str) -> list[str]:
    """
    Build clean weekday labels from a title like:
      - 'W45 from 03-11-2025 to 09-11-2025'
      - '03-11-2025 – 09-11-2025'
      - '21–25 Oct 2025'  (we will expand based on the start date)
    Returns labels like: ['Monday 03-11-2025', 'Tuesday 04-11-2025', ...]
    """
    import re
    from datetime import datetime, timedelta

    title = (title or "").strip()

    # Try numeric "from ... to ..."
    m = re.search(r"\bfrom\s+(\d{2}-\d{2}-\d{4})\s+to\s+(\d{2}-\d{2}-\d{4})\b", title, flags=re.I)
    if not m:
        # Try generic numeric range "DD-MM-YYYY – DD-MM-YYYY" or "... - ..."
        m = re.search(r"\b(\d{2}-\d{2}-\d{4})\s*(?:–|-|to)\s*(\d{2}-\d{2}-\d{4})\b", title, flags=re.I)
    if m:
        start = datetime.strptime(m.group(1), "%d-%m-%Y")
        end = datetime.strptime(m.group(2), "%d-%m-%Y")
        days = (end - start).days + 1
        days = max(1, min(days, 7))  # clamp to 1..7
        out = []
        for i in range(days):
            d = start + timedelta(days=i)
            out.append(f"{d.strftime('%A')} {d.strftime('%d-%m-%Y')}")
        return out

    # Try textual month like "21–25 Oct 2025" → expand 5 days
    m = re.search(
        r"\b(\d{1,2})\s*[–-]\s*(\d{1,2})\s*(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s*(\d{4})\b",
        title, flags=re.I
    )
    if m:
        d1, d2, mon, year = int(m.group(1)), int(m.group(2)), m.group(3), int(m.group(4))
        start = datetime.strptime(f"{d1:02d}-{mon}-{year}", "%d-%b-%Y")
        days = max(1, min((d2 - d1 + 1), 7))
        out = []
        for i in range(days):
            d = start + timedelta(days=i)
            out.append(f"{d.strftime('%A')} {d.strftime('%d-%m-%Y')}")
        return out

    return []


def _period_fingerprint(page) -> str:
    """
    Robust fallback for detecting a period change when we can't parse the title.
    Based on the visible weekday header labels (often include explicit dates).
    """
    try:
        tbl = _find_timesheet_table(page)
        if not tbl or not tbl.count():
            return ""
        day_cols = _get_weekday_headers(tbl)
        if not day_cols:
            return ""
        labels = [label for _, label in day_cols]
        labels = [" ".join((label or "").split()) for label in labels]  # normalize whitespace
        return " | ".join(labels)
    except Exception:
        return ""


def _saw_saved_toast(page) -> bool:
    with suppress_exc():
        page.wait_for_selector("text=/\\bSaved\\b/i", timeout=SHORT_TIMEOUT_MS)
        return True
    return False

def _has_submit_button(page) -> bool:
    with suppress_exc():
        if page.get_by_role("button", name=re.compile(r"Submit for approval", re.I)).count():
            return True
    with suppress_exc():
        if page.locator("xpath=" + SAVE_BTN_XPATH.replace("button","button[contains(.,'Submit')]")).count():
            return True
    return False

def _click_save(page) -> bool:
    with suppress_exc():
        btn = page.get_by_role("button", name=re.compile(r"^Save$", re.I)).first
        if btn.count():
            btn.click(timeout=SHORT_TIMEOUT_MS)
            return True
    with suppress_exc():
        btn = page.locator("xpath=" + SAVE_BTN_XPATH).first
        if btn.count():
            btn.click(timeout=SHORT_TIMEOUT_MS)
            return True
    return False

# def _click_submit(page) -> bool:
#     with suppress_exc():
#         btn = page.get_by_role("button", name=re.compile(r"Submit for approval", re.I)).first
#         if btn.count():
#             btn.click(timeout=SHORT_TIMEOUT_MS)
#             return True
#     return False
def _click_submit(page) -> bool:
    with suppress_exc():
        btn = page.get_by_role("button", name=re.compile(r"Submit for approval", re.I)).first
        if btn.count():
            btn.click(timeout=SHORT_TIMEOUT_MS)
            _confirm_submit_modal(page)  # <-- new
            return True
    return False

def _click_create(page) -> bool:
    with suppress_exc():
        b = page.locator("xpath=" + CREATE_TIMESHEET_XPATH).first
        if b.count():
            b.click(timeout=SHORT_TIMEOUT_MS)
            return True
    with suppress_exc():
        b = page.locator("xpath=" + CREATE_BTN_XPATH).first
        if b.count():
            b.click(timeout=SHORT_TIMEOUT_MS)
            return True
    return False

def _wait_for_save_submit_chip(page, timeout_ms: int) -> Optional[str]:
    end = time.time() + (timeout_ms / 1000.0)
    while time.time() < end:
        with suppress_exc():
            if page.get_by_role("button", name=re.compile(r"Create timesheet", re.I)).count():
                return "create"
        with suppress_exc():
            if page.get_by_role("button", name=re.compile(r"^Save$", re.I)).count():
                return "save"
        with suppress_exc():
            if page.get_by_role("button", name=re.compile(r"Submit for approval", re.I)).count():
                return "submit"
        chip = (_get_status_chip_text(page) or "").lower().strip()
        if chip.startswith(("approval pending", "submitted")):
            return None
        time.sleep(0.15)
    return None


def _confirm_submit_modal(page) -> bool:
    """
    If a confirmation modal appears after clicking 'Submit for approval',
    press the confirm/submit button. Returns True if nothing blocked us.
    """
    with suppress_exc():
        # Common modal confirm buttons
        btn = page.get_by_role("button", name=re.compile(r"^(Submit|Confirm|Yes|OK)$", re.I)).first
        if btn.count():
            btn.click(timeout=SHORT_TIMEOUT_MS)
            return True
    # No modal or couldn't find it is fine — continue
    return True

def _wait_until_submitted(page, timeout_ms: int) -> bool:
    """
    Wait until the status chip becomes 'Approval pending' or 'Submitted'
    OR the 'Submit for approval' button disappears.
    """
    end = time.time() + (timeout_ms / 1000.0)
    while time.time() < end:
        with suppress_exc():
            # button disappears?
            if not page.get_by_role("button", name=re.compile(r"Submit for approval", re.I)).count():
                return True
        chip = (_get_status_chip_text(page) or "").strip().lower()
        if chip.startswith(("approval pending", "submitted")):
            return True
        time.sleep(0.2)
    return False


# ────────────────────────── Table / grid view helpers ─────────────────────────

def _pretty_labels(day_cols):
    """Compact headers like 'Mon 10-11' (always with a space)."""
    abbr = {
        "monday":"Mon", "tuesday":"Tue", "wednesday":"Wed",
        "thursday":"Thu", "friday":"Fri", "saturday":"Sat", "sunday":"Sun",
    }
    out = []
    for _, lbl in day_cols:
        s = " ".join((lbl or "").strip().split())
        # Accept both 'Monday10-11-2025' and 'Monday 10-11-2025'
        m = re.match(
            r'^(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\s*(\d{1,2})[-/](\d{1,2})[-/]\d{2,4}$',
            s, flags=re.I
        )
        if m:
            day = abbr[m.group(1).lower()]
            dd  = f"{int(m.group(2)):02d}"
            mm  = f"{int(m.group(3)):02d}"
            out.append(f"{day} {dd}-{mm}")  # <-- enforced space
        else:
            # Fallback: if only day name, abbreviate it
            parts = s.split()
            if parts and parts[0].lower() in abbr:
                parts[0] = abbr[parts[0].lower()]
            out.append(" ".join(parts) or s)
    return out


def _find_timesheet_table(page):
    ci = "translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')"
    weekdays = ("monday","tuesday","wednesday","thursday","friday")
    parts = " or ".join([f"contains({ci}, '{d}')" for d in weekdays])

    # A) HTML table with weekday headers
    loc = page.locator(f"xpath=//table[.//th[{parts}] or .//*[@role='columnheader'][{parts}]]").first
    if loc.count(): return loc

    # B) ARIA grid (React/AG-Grid) with weekday headers
    grid = page.locator("xpath=//*[@role='grid' or @role='table']").filter(
        has=page.locator(f"xpath=.//*[@role='columnheader'][{parts}] | .//*[self::th or self::div][{parts}]")
    ).first
    if grid.count(): return grid

    # C) Fallback: first table/grid under main
    loc = page.locator("main").locator("table, [role='grid'], [role='table']").first
    if loc.count(): return loc

    # D) Near headings
    return page.locator("xpath=(//h1[contains(., 'Timesheet')]/following::*[self::table or @role='grid'])[1]").first

def _get_weekday_headers(tbl_or_page):
    """
    Return [(col_index, label)].
    Strategy:
      1) Try native table <thead><th>…> parsing (textContent).
      2) Try ARIA grid columnheaders.
      3) Try generic DIV-based header strip.
      4) If all else fails AND we can parse the title on the page, synthesize labels.
    """
    def _dedupe_keep_order(pairs):
        seen = set()
        out = []
        for idx, lbl in pairs:
            key = " ".join((lbl or "").split())
            if key and key not in seen:
                seen.add(key)
                out.append((idx, key))
        return out

    headers = []

    # 1) Native table header
    try:
        tbl = tbl_or_page
        if hasattr(tbl_or_page, "locator"):
            # If page, try to find the table first
            maybe_tbl = _find_timesheet_table(tbl_or_page)
            if maybe_tbl and maybe_tbl.count():
                tbl = maybe_tbl
        ths = tbl.locator("thead th")
        if ths.count():
            day_re = re.compile(
                r"(MONDAY|TUESDAY|WEDNESDAY|THURSDAY|FRIDAY|SATURDAY|SUNDAY)\b(?:.*?\b(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}))?",
                re.I,
            )
            for i in range(ths.count()):
                txt = ""
                with suppress_exc():
                    txt = (ths.nth(i).evaluate("el => el.textContent") or "").strip()
                if not txt:
                    with suppress_exc():
                        txt = (ths.nth(i).inner_text() or "").strip()
                txt = " ".join(txt.split())
                m = day_re.search(txt)
                if m:
                    day = m.group(1).title()
                    date = m.group(2)
                    headers.append((i, f"{day} {date}" if date else day))
            headers = _dedupe_keep_order(headers)
            if headers:
                return headers
    except Exception:
        pass

    # 2) ARIA grid header
    try:
        tbl = tbl_or_page if not headers else tbl
        if hasattr(tbl, "locator"):
            cols = tbl.locator('[role="columnheader"]')
            if cols.count():
                day_names = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]
                for i in range(cols.count()):
                    txt = ""
                    with suppress_exc():
                        txt = (cols.nth(i).evaluate("el => el.textContent") or "").strip()
                    if not txt:
                        with suppress_exc():
                            txt = (cols.nth(i).inner_text() or "").strip()
                    low = (txt or "").lower()
                    for dn in day_names:
                        if dn.lower() in low:
                            headers.append((i, " ".join((txt or dn).split())))
                            break
                headers = _dedupe_keep_order(headers)
                if headers:
                    return headers
    except Exception:
        pass

    # 3) Generic DIV-based header strip (very loose)
    try:
        tbl = _find_timesheet_table(tbl_or_page)
        if tbl and tbl.count():
            day_names = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]
            ci = "translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')"
            day_xpath = " or ".join([f"contains({ci}, '{dn.lower()}')" for dn in day_names])
            generic = tbl.locator(f"xpath=.//*[self::div or self::span or self::p][{day_xpath}]")
            if generic.count():
                for i in range(generic.count()):
                    txt = ""
                    with suppress_exc():
                        txt = (generic.nth(i).evaluate('el => el.textContent') or '').strip()
                    if not txt:
                        with suppress_exc():
                            txt = (generic.nth(i).inner_text() or '').strip()
                    txt = " ".join((txt or "").split())
                    if txt:
                        headers.append((i, txt))
                headers = _dedupe_keep_order(headers)[:7]
                if headers:
                    return headers
    except Exception:
        pass

    # 4) Synthesize from title on page (assume project column is 0, days are 1..N)
    try:
        title = _get_week_title(tbl_or_page)
        labels = _labels_from_title(title)
        if labels:
            return list(enumerate(labels, start=1))
    except Exception:
        pass

    return []

def _verbatim_grid(tbl, day_cols):
    """Read rows for native tables and ARIA grids, aligned to weekday count, ignoring frozen dup & totals."""
    rows = []

    # number of weekday columns we want to render (Mon..Fri or Mon..Sun)
    day_count = max(0, len(day_cols))

    def _txt(loc):
        s = ""
        with suppress_exc():
            s = (loc.evaluate("el => el.textContent") or "").strip()
        if not s:
            with suppress_exc():
                s = (loc.inner_text() or "").strip()
        return " ".join((s or "").split())

    # Helper: post-process the values scraped after the project column
    def _sanitize_values(values, proj):
        # 1) Some layouts repeat the frozen "project" column again as the first value
        while values and values[0]:
            v0 = values[0].strip()
            if not v0:
                break
            # If the first value equals (or starts with) the project text, drop it
            if v0 == proj or v0.lower().startswith(proj.lower()[:16]):
                values.pop(0)
                continue
            break

        # 2) If there's exactly one extra trailing cell (weekly total), drop it
        if len(values) == day_count + 1:
            values = values[:day_count]

        # 3) Clip/pad to the expected weekday count
        if len(values) > day_count:
            values = values[:day_count]
        elif len(values) < day_count:
            values = values + [""] * (day_count - len(values))
        return values

    # # ───────── Native <table> (alternate “control/value” cells) ─────────
    # body_rows = tbl.locator("tbody tr")
    # if body_rows.count():
    #     import re

    #     def _read_cell_number(cell):
    #         # 1) prefer the hidden number input’s value
    #         with suppress_exc():
    #             inp = cell.locator("input[type='number']").first
    #             if inp.count():
    #                 v = (inp.get_attribute("value") or "").strip()
    #                 if v:
    #                     return f"{float(v):g}d"
    #         # 2) fallback: visible text (ignore aria-hidden)
    #         with suppress_exc():
    #             el = cell.locator(
    #                 "p:not([aria-hidden='true']),"
    #                 "span:not([aria-hidden='true']),"
    #                 "div:not([aria-hidden='true'])"
    #             ).first
    #             if el.count():
    #                 t = (el.inner_text() or "").strip()
    #                 m = re.search(r"\d+(?:\.\d+)?", t)
    #                 if m:
    #                     return f"{float(m.group(0)):g}d"
    #         # 3) last resort: raw inner text
    #         with suppress_exc():
    #             raw = (cell.inner_text() or "").strip()
    #             m = re.search(r"\d+(?:\.\d+)?", raw)
    #             if m:
    #                 return f"{float(m.group(0)):g}d"
    #         return "0d"

    #     for rix in range(body_rows.count()):
    #         r = body_rows.nth(rix)
    #         tds = r.locator("td")
    #         if tds.count() < 3:
    #             continue

    #         # Project = first td
    #         proj = _txt(tds.nth(0))
    #         if not proj:
    #             with suppress_exc():
    #                 p0 = tds.nth(0).locator("p, div, span").first
    #                 if p0.count():
    #                     proj = _txt(p0)
    #         if not proj:
    #             continue

    #         # Values = every 2nd td starting at index 2 (0-based):
    #         # indices: 2,4,6,8,10 → Mon..Fri; ignore totals and anything after.
    #         day_count = max(0, len(day_cols))
    #         idxs = [i for i in range(2, tds.count(), 2)][:day_count]

    #         values = [_read_cell_number(tds.nth(i)) for i in idxs]
    #         # still run through sanitizer to pad/clip if headers are <5 or >5
    #         values = _sanitize_values(values, proj)
    #         rows.append((proj, values))

    #     return rows
    # ───────── Native <table> (alternate control/value cells) ─────────
    body_rows = tbl.locator("tbody tr")
    if body_rows.count():
        import re

        def _read_cell_number(cell):
            # Prefer the hidden <input type=number> value
            with suppress_exc():
                inp = cell.locator("input[type='number']").first
                if inp.count():
                    v = (inp.get_attribute("value") or "").strip()
                    if v:
                        return f"{float(v):g}d"
            # Fallback: visible text (ignore aria-hidden)
            with suppress_exc():
                el = cell.locator(
                    "p:not([aria-hidden='true']),"
                    "span:not([aria-hidden='true']),"
                    "div:not([aria-hidden='true'])"
                ).first
                if el.count():
                    t = (el.inner_text() or "").strip()
                    m = re.search(r"\d+(?:\.\d+)?", t)
                    if m:
                        return f"{float(m.group(0)):g}d"
            # Last resort: raw text
            with suppress_exc():
                raw = (cell.inner_text() or "").strip()
                m = re.search(r"\d+(?:\.\d+)?", raw)
                if m:
                    return f"{float(m.group(0)):g}d"
            return "0d"

        for rix in range(body_rows.count()):
            r = body_rows.nth(rix)
            tds = r.locator("td")
            if tds.count() < 3:
                continue

            # Project = td #1
            proj = _txt(tds.nth(0))
            if not proj:
                with suppress_exc():
                    p0 = tds.nth(0).locator("p, div, span").first
                    if p0.count():
                        proj = _txt(p0)
            if not proj:
                continue

            # Values: accept 3,5,7,9,11  (0-based: 2,4,6,8,10)
            day_count = max(0, len(day_cols)) or 5
            value_idxs = [i for i in range(2, tds.count(), 2)][:day_count]
            values = [_read_cell_number(tds.nth(i)) for i in value_idxs]

            # Total (if present): td #14  (0-based index 13)
            total = ""
            if tds.count() >= 14:
                total = _read_cell_number(tds.nth(13))
            else:
                # compute a total if Napta didn't render one
                s = 0.0
                for v in values:
                    m = re.search(r'\d+(?:\.\d+)?', v or '')
                    if m: s += float(m.group(0))
                total = f"{s:g}d"

            values = _sanitize_values(values, proj)
            rows.append((proj, values, total))
        return rows



    # ───────── ARIA grid ─────────
    aria_rows = tbl.locator('[role="rowgroup"] [role="row"]')
    if not aria_rows.count():
        aria_rows = tbl.locator('[role="row"]')  # broad fallback

    for rix in range(aria_rows.count()):
        r = aria_rows.nth(rix)
        cells = r.locator('[role="gridcell"], [role="cell"]')
        if not cells.count():
            continue

        # Project: first cell
        proj = _txt(cells.nth(0))
        if not proj:
            continue

        # Day cells: everything after the first cell, in order
        values = [_txt(cells.nth(i)) for i in range(1, cells.count())]
        values = _sanitize_values(values, proj)
        rows.append((proj, values))

    return rows


def _read_flex_grid(tbl, day_cols):
    """
    Fallback reader for DIV-based layouts (no <table>, no ARIA roles).
    Heuristic: treat each immediate child (or obvious row container) as a row,
    first child as "Project", subsequent children as day cells.
    """
    rows = []

    # likely row containers
    candidates = tbl.locator(
        "xpath=.//*[contains(@class,'row') or contains(@class,'Row') or contains(@class,'timesheet') or contains(@class,'TableRow')]"
    )
    if not candidates.count():
        # fallback: direct children
        candidates = tbl.locator(":scope > *")

    for i in range(min(100, candidates.count())):
        r = candidates.nth(i)
        # collect direct children as columns
        cells = r.locator(":scope > *")
        if cells.count() < 2:
            continue
        # project name
        proj = ""
        with suppress_exc():
            proj = (cells.nth(0).evaluate("el => el.textContent") or "").strip()
        if not proj:
            with suppress_exc():
                proj = (cells.nth(0).inner_text() or "").strip()
        proj = " ".join((proj or "").split())
        if not proj:
            continue

        # day cells – map by index count from headers
        out = []
        for j, _ in day_cols:
            if j+1 >= cells.count():
                out.append("")
                continue
            txt = ""
            with suppress_exc():
                txt = (cells.nth(j+1).evaluate("el => el.textContent") or "").strip()
            if not txt:
                with suppress_exc():
                    txt = (cells.nth(j+1).inner_text() or "").strip()
            out.append(" ".join((txt or "").split()))
        rows.append((proj, out))

    return rows


# ────────────────────────────── Napta Client (API) ───────────────────────────

class NaptaClient:
    """Fast client: login once (headed) → save storage_state → close browser.
       Subsequent commands run headless in a single browser with storage_state loaded.
    """
    def __init__(self) -> None:
        self._cookie_ok: Optional[bool] = None
        self._p = None
        self._browser = None
        self._ctx = None
        self._page = None
        self._view_cache_path = _APP_DIR / "view_cache.json"

    # ───── Public API (synchronous) ─────

    def status(self) -> str:
        if STATE_PATH.exists():
            return "Auth: will use saved session (storage state)."
        if self._cookie_ok:
            return "Auth: OK (session present)."
        return "Auth: will use saved session (storage state or browser SSO cookies)."

    def login(self) -> Tuple[bool, str]:
        return self._login_sync()

    def view_week(self, which: str = "current") -> Tuple[bool, str]:
        return self._view_week_fast(which)

    def save_current_week(self) -> Tuple[bool, str]:
        return self._save_current_week_fast()

    def save_next_week(self) -> Tuple[bool, str]:
        return self._save_next_week_fast()

    def submit_current_week(self) -> Tuple[bool, str]:
        return self._submit_current_week_fast()

    def submit_next_week(self) -> Tuple[bool, str]:
        return self._submit_next_week_fast()

    def save_and_submit_current_week(self) -> Tuple[bool, str]:
        ok, msg = self._save_current_week_fast()
        if not ok:
            return ok, msg
        return self._submit_current_week_fast()

    # ────────────────── Playwright lifecycle ──────────────────

    def _ensure_session(self, *, headless: bool = True):
        """Create ONE headless browser for the chat session, using saved storage_state if present."""
        if headless and self._p and self._browser and self._ctx and self._page:
            return

        self._p = sync_playwright().start()
        self._browser = self._p.chromium.launch(
            headless=headless,
            proxy=_proxy_conf(),
            args=["--disable-dev-shm-usage"],
        )
        # Use storage_state when available (avoid re-login)
        if STATE_PATH.exists():
            self._ctx = self._browser.new_context(storage_state=str(STATE_PATH))
        else:
            self._ctx = self._browser.new_context()

        self._ctx.set_default_timeout(DEFAULT_TIMEOUT_MS)
        self._ctx.route("**/*", _route_slim)
        self._page = self._ctx.new_page()

        try:
            self._page.add_init_script(f"Object.defineProperty(navigator, 'userAgent', {{get: () => '{UA_DESKTOP}'}});")
        except Exception:
            pass

    def _shutdown(self):
        with suppress_exc():
            if self._ctx: self._ctx.close()
        with suppress_exc():
            if self._browser: self._browser.close()
        with suppress_exc():
            if self._p: self._p.stop()
        self._p = self._browser = self._ctx = self._page = None

    def close(self):
        self._shutdown()

    # ────────────────── Auth helpers ──────────────────

    def _open_timesheet(self):
        last_err = None
        for attempt in range(2):
            try:
                self._page.goto(DEFAULT_APP_URL, timeout=45_000)
                with suppress_exc(): self._page.wait_for_load_state("domcontentloaded", timeout=3_000)
                with suppress_exc(): self._page.keyboard.press("Escape")
                with suppress_exc(): self._page.get_by_role("button", name="This week").click(timeout=1_200)
                with suppress_exc(): self._page.locator(f"xpath={THIS_WEEK_BTN_XPATH}").first.click(timeout=1_200)
                return
            except Exception as e:
                last_err = e
                time.sleep(0.6)
        raise last_err if last_err else RuntimeError("Failed to open timesheet")

    # ─────────────────────────────── Login ───────────────────────────────

    def _on_login_page(self) -> bool:
        with suppress_exc():
            if self._page.locator('input[type="email"]').count(): return True
        with suppress_exc():
            if self._page.get_by_role("button", name="Continue with Google").count(): return True
        with suppress_exc():
            if self._page.locator("text=Welcome").count() and self._page.locator("text=Log in to continue").count(): return True
        return False

    def _login_sync(self) -> Tuple[bool, str]:
        """
        Headed login to capture storage_state. If an asyncio loop is running
        (e.g. when prompt_toolkit owns the TTY), run the same logic in a
        subprocess to avoid the 'Playwright Sync API inside asyncio loop' error.
        """
        import sys, subprocess, asyncio, textwrap, re, time

        # Helper: do we already have a valid session open?
        def _captured(ctx, page) -> bool:
            with suppress_exc():
                if page.get_by_role("button", name=re.compile(r"Create timesheet", re.I)).count():
                    ctx.storage_state(path=str(STATE_PATH)); return True
            with suppress_exc():
                if page.get_by_role("button", name=re.compile(r"^Save$", re.I)).count():
                    ctx.storage_state(path=str(STATE_PATH)); return True
            with suppress_exc():
                if page.get_by_role("button", name=re.compile(r"Submit for approval", re.I)).count():
                    ctx.storage_state(path=str(STATE_PATH)); return True
            chip = (_get_status_chip_text(page) or "").strip()
            if chip:
                with suppress_exc():
                    ctx.storage_state(path=str(STATE_PATH))
                return True
            return False

        # Path 1: normal (no running event loop) — keep your original logic
        try:
            asyncio.get_running_loop()
            loop_running = True
        except RuntimeError:
            loop_running = False

        if not loop_running:
            from playwright.sync_api import sync_playwright as _sp  # original import path
            with _sp() as p:
                browser = p.chromium.launch(
                    headless=False,
                    proxy=_proxy_conf(),
                    args=["--disable-dev-shm-usage"],
                )
                ctx = browser.new_context()
                ctx.set_default_timeout(DEFAULT_TIMEOUT_MS)
                ctx.route("**/*", _route_slim)
                page = ctx.new_page()
                try:
                    try:
                        page.goto(DEFAULT_APP_URL, timeout=45_000)
                    except Exception:
                        with suppress_exc():
                            page.goto("https://app.napta.io", timeout=45_000)
                            page.goto(DEFAULT_APP_URL, timeout=45_000)

                    # Wait for user to complete SSO (up to 3 minutes)
                    start = time.time()
                    while time.time() - start < 180:
                        if _captured(ctx, page):
                            self._cookie_ok = True
                            return True, "✅ Login captured. You can now run: view / save / submit."
                        time.sleep(0.5)

                    # timed out
                    name = f"napta_login_timeout_{ts()}.png"
                    with suppress_exc():
                        page.screenshot(path=_shot(name), full_page=True)
                    return False, f"Login window timed out. Screenshot -> {name}"
                finally:
                    with suppress_exc():
                        ctx.close()
                    with suppress_exc():
                        browser.close()

        # Path 2: fallback when an asyncio loop is running — spawn a subprocess
        helper = textwrap.dedent(f"""
            import re, time
            from playwright.sync_api import sync_playwright
            DEFAULT_APP_URL = {DEFAULT_APP_URL!r}
            STATE_PATH = {str(STATE_PATH)!r}
            DEFAULT_TIMEOUT_MS = {DEFAULT_TIMEOUT_MS}
            def _route_slim(route):
                req = route.request
                if req.resource_type in ("image","media","font"): return route.abort()
                url = req.url
                if url.endswith((".map",".svg")): return route.abort()
                return route.continue_()
            def _get_chip(page):
                try:
                    el = page.locator("header, main").locator("text=/^(Not created|Draft|Open|Approval pending|Submitted)$/i").first
                    if el.count(): return (el.inner_text() or "").strip()
                except Exception: pass
                return ""
            with sync_playwright() as p:
                br = p.chromium.launch(headless=False, args=["--disable-dev-shm-usage"])
                ctx = br.new_context()
                ctx.set_default_timeout(DEFAULT_TIMEOUT_MS)
                ctx.route("**/*", _route_slim)
                pg = ctx.new_page()
                try:
                    try:
                        pg.goto(DEFAULT_APP_URL, timeout=45_000)
                    except Exception:
                        pg.goto("https://app.napta.io", timeout=45_000)
                        pg.goto(DEFAULT_APP_URL, timeout=45_000)
                    start = time.time()
                    ok = False
                    while time.time() - start < 180:
                        if pg.get_by_role("button", name=re.compile(r"Create timesheet", re.I)).count():
                            ctx.storage_state(path=STATE_PATH); ok=True; break
                        if pg.get_by_role("button", name=re.compile(r"^Save$", re.I)).count():
                            ctx.storage_state(path=STATE_PATH); ok=True; break
                        if pg.get_by_role("button", name=re.compile(r"Submit for approval", re.I)).count():
                            ctx.storage_state(path=STATE_PATH); ok=True; break
                        if _get_chip(pg):
                            ctx.storage_state(path=STATE_PATH); ok=True; break
                        time.sleep(0.5)
                    print("OK" if ok else "TIMEOUT")
                finally:
                    try: ctx.close()
                    except Exception: pass
                    try: br.close()
                    except Exception: pass
        """)
        proc = subprocess.run([sys.executable, "-c", helper], capture_output=True, text=True)
        if proc.returncode == 0 and "OK" in proc.stdout and STATE_PATH.exists():
            self._cookie_ok = True
            return True, "✅ Login captured. You can now run: view / save / submit."
        return False, "Login window timed out or failed in helper. Please try again."


    # ─────────────────────────────── View ───────────────────────────────

    def _view_cache_get(self, which: str) -> Optional[str]:
        try:
            d = json.loads(self._view_cache_path.read_text())
            fresh = (time.time() - d["ts"]) < 10 and d.get("which")==which
            return d["text"] if fresh else None
        except Exception:
            return None

    def _view_cache_put(self, which: str, text: str) -> None:
        with suppress_exc():
            self._view_cache_path.write_text(json.dumps({"ts": time.time(), "which": which, "text": text}))

    def _view_week_fast(self, which: str = "current") -> Tuple[bool, str]:
        """Render the current or next week view in readable grid format."""
        cached = self._view_cache_get(which)
        if cached:
            return True, cached

        # Headless browser session (uses saved state)
        self._ensure_session(headless=True)
        _, err = _safe_run(lambda: self._open_timesheet(), "page load")
        if err:
            return False, err

        if self._on_login_page():
            name = f"napta_login_required_{ts()}.png"
            self._page.screenshot(path=_shot(name), full_page=True)
            return False, f"⛔ Napta login required. Please open https://app.napta.io once in Chrome. Screenshot -> {name}"

        # Navigate if user asked for "next" week
        if which == "next":
            if not self._go_to_next_week():
                name = f"napta_nav_verify_{ts()}.png"
                self._page.screenshot(path=_shot(name), full_page=True)
                return False, f"❌ Navigation didn't land on next week. Screenshot -> {name}"

        # Allow DOM to fully update after week switch
        self._page.wait_for_load_state("domcontentloaded", timeout=5_000)
        time.sleep(1.5)

        # Wait for Save/Submit buttons or state chips
        _ = _wait_for_save_submit_chip(self._page, timeout_ms=DEFAULT_TIMEOUT_MS)
        chip = _get_status_chip_text(self._page) or "unknown"
        title = _get_week_title(self._page) or "Week"

        # Find the table/grid container
        tbl = _find_timesheet_table(self._page)
        if not tbl.count():
            with suppress_exc():
                self._page.goto(DEFAULT_APP_URL, timeout=45_000)
                self._page.wait_for_load_state("domcontentloaded", timeout=3_000)
            tbl = _find_timesheet_table(self._page)
        if not tbl.count():
            msg = f"🗓 {title}\nStatus: {chip}\nℹ️ Could not locate the timesheet table."
            self._view_cache_put(which, msg)
            return True, msg

        # Prefer robust headers (includes synthesized labels if DOM parsing fails)
        day_cols = _get_weekday_headers(self._page) or _get_weekday_headers(tbl)
        if not day_cols:
            msg = f"🗓 {title}\nStatus: {chip}\n(Headers not found)"
            self._view_cache_put(which, msg)
            return True, msg

        # Read rows
        rows = _verbatim_grid(tbl, day_cols)
        if not rows:
            with suppress_exc():
                rows = _read_flex_grid(tbl, day_cols)

        # ─────────── Build formatted output ───────────
        lines = [f"🗓 {title}"]
        if chip and chip.lower() != "unknown":
            lines.append(chip)
        lines.append("")

        labels = _pretty_labels(day_cols)  # ["Mon 10-11", ...]
        labels += ["Total"]

        # Fixed project width = exact length of Training row label
        _TARGET = "PALO IT Singapore — SG - Training 2025"
        proj_width = len(_TARGET)

        # compute column widths so headers & values align
        day_headers = labels[:-1]
        total_header = labels[-1]

        base_cell_width = 6  # fits "0.5d", "10d"
        day_width = max(base_cell_width, max(len(h) for h in day_headers))   # align to header
        total_width = max(base_cell_width, len(total_header))
        gap_width = 3  # extra padding before Total on DATA ROWS only

        def _fit_project(name: str) -> str:
            if len(name) <= proj_width:
                return name.ljust(proj_width)
            return (name[:proj_width - 3] + " ..")

        def fmt_row(project, cells, total):
            wc = len(day_headers)
            cells = (cells + [""] * (wc - len(cells)))[:wc]
            pj = _fit_project(project)
            cs = " | ".join(c.ljust(day_width) for c in cells)
            tt = (total or "").ljust(total_width)
            # add gap AFTER the bar before Total (rows only)
            return f"{pj} | {cs} |{' ' * gap_width}{tt}"

        # Header (NO extra gap here)
        hdr_parts = [_fit_project("Project")] + [h.ljust(day_width) for h in day_headers] + [total_header.ljust(total_width)]
        hdr = " | ".join(hdr_parts)
        lines.append(hdr)
        lines.append("-" * max(40, len(hdr)))

        # Rows (auto-compute total when not provided)
        import re
        if rows:
            for row in rows:
                if len(row) == 3:
                    proj, cells, total = row
                else:
                    proj, cells = row
                    s = 0.0
                    for v in cells:
                        m = re.search(r'\d+(?:\.\d+)?', v or '')
                        if m:
                            s += float(m.group(0))
                    total = f"{s:g}d"
                lines.append(fmt_row(proj, cells, total))
        else:
            lines.append("(No rows)")

        msg = "\n".join(lines)
        self._view_cache_put(which, msg)
        return True, msg


    def _go_to_next_week(self) -> bool:
        # Use either the parsed title or the header fingerprint as the "before" marker
        before_title = (_get_week_title(self._page) or "").strip()
        before_fp = _period_fingerprint(self._page)
        before = before_title or before_fp

        attempts = 0
        while attempts < 3:
            attempts += 1
            with suppress_exc():
                # data-cy variants
                self._page.locator('[data-cy*="navRight"], [data-cy*="PeriodNavigation_navRight"]').first.click(timeout=SHORT_TIMEOUT_MS)
            with suppress_exc():
                # generic "Next"
                self._page.get_by_role("button", name=re.compile(r"Next|>", re.I)).first.click(timeout=SHORT_TIMEOUT_MS)
            with suppress_exc():
                # keyboard fallback
                self._page.keyboard.press("ArrowRight")

            # Wait for week label OR fingerprint to change (longer to handle slow loads)
            for _ in range(30):
                after_title = (_get_week_title(self._page) or "").strip()
                after_fp = _period_fingerprint(self._page)
                after = after_title or after_fp
                if after and after != before:
                    return True
                time.sleep(0.3)
        return False

    # ─────────────────────────── Save / Submit (fast) ───────────────────────────

    def _save_current_week_fast(self) -> Tuple[bool, str]:
        self._ensure_session(headless=True)
        _, err = _safe_run(lambda: self._open_timesheet(), "page load")
        if err:
            return False, err


        if self._on_login_page():
            name = f"napta_login_required_{ts()}.png"; self._page.screenshot(path=_shot(name), full_page=True)
            return False, f"⛔ Napta login required. Please open https://app.napta.io once in Chrome. Screenshot -> {name}"

        state = _wait_for_save_submit_chip(self._page, timeout_ms=SHORT_TIMEOUT_MS)
        if state is None:
            return True, "ℹ️ Timesheet already submitted for this week."

        if state == "create":
            if not _click_create(self._page):
                name = f"napta_create_failure_{ts()}.png"; self._page.screenshot(path=_shot(name), full_page=True)
                return False, f"❌ Could not click 'Create timesheet'. Screenshot -> {name}"
            state = _wait_for_save_submit_chip(self._page, timeout_ms=SHORT_TIMEOUT_MS)
            if state is None:
                return False, "❌ After 'Create', no Save/Submit visible."

        if state == "submit":
            return True, "✅ Timesheet already saved. 'Submit for approval' is visible."

        if not _click_save(self._page):
            name = f"napta_save_failure_{ts()}.png"; self._page.screenshot(path=_shot(name), full_page=True)
            return False, f"❌ Could not click 'Save'. Screenshot -> {name}"

        _saw_saved_toast(self._page)
        with suppress_exc(): self._view_cache_path.unlink()
        return True, "✅ Saved (draft)."

    def _save_next_week_fast(self) -> Tuple[bool, str]:
        self._ensure_session(headless=True)
        _, err = _safe_run(lambda: self._open_timesheet(), "page load")
        if err:
            return False, err


        if self._on_login_page():
            name = f"napta_login_required_{ts()}.png"; self._page.screenshot(path=_shot(name), full_page=True)
            return False, f"⛔ Napta login required. Please open https://app.napta.io once in Chrome. Screenshot -> {name}"

        if not self._go_to_next_week():
            name = f"napta_error_{ts()}.png"; self._page.screenshot(path=_shot(name), full_page=True)
            return False, f"❌ Could not navigate to next week. Screenshot -> {name}"

        state = _wait_for_save_submit_chip(self._page, timeout_ms=SHORT_TIMEOUT_MS)
        if state is None and _has_submit_button(self._page):
            return True, "✅ Next week saved. Do you want to 'Submit for approval'? Type sbnw"
        if state is None:
            chip = (_get_status_chip_text(self._page) or "").strip().lower()
            if chip.startswith(("approval pending", "submitted")):
                return True, "ℹ️ Next week already submitted."
            return True, "✅ Next week already saved. 'Submit for approval' may be visible."

        if state == "create":
            if not _click_create(self._page):
                name = f"napta_create_failure_{ts()}.png"; self._page.screenshot(path=_shot(name), full_page=True)
                return False, f"❌ Could not click 'Create timesheet'. Screenshot -> {name}"
            state = _wait_for_save_submit_chip(self._page, timeout_ms=SHORT_TIMEOUT_MS)
            if state is None:
                return False, "❌ After 'Create', no Save/Submit visible."

        if state == "submit":
            return True, "✅ Next week already saved. 'Submit for approval' is visible."

        if not _click_save(self._page):
            name = f"napta_save_failure_{ts()}.png"; self._page.screenshot(path=_shot(name), full_page=True)
            return False, f"❌ Could not click 'Save'. Screenshot -> {name}"

        _saw_saved_toast(self._page)
        with suppress_exc(): self._view_cache_path.unlink()
        return True, "✅ Next week saved (draft)."

    def _submit_current_week_fast(self) -> Tuple[bool, str]:
        self._ensure_session(headless=True)
        _, err = _safe_run(lambda: self._open_timesheet(), "page load")
        if err:
            return False, err


        if self._on_login_page():
            name = f"napta_login_required_{ts()}.png"; self._page.screenshot(path=_shot(name), full_page=True)
            return False, f"⛔ Napta login required. Please open https://app.napta.io once in Chrome. Screenshot -> {name}"

        state = _wait_for_save_submit_chip(self._page, timeout_ms=SHORT_TIMEOUT_MS)
        if state is None:
            return True, "ℹ️ Timesheet already submitted."

        if state == "create":
            if not _click_create(self._page):
                name = f"napta_create_failure_{ts()}.png"; self._page.screenshot(path=_shot(name), full_page=True)
                return False, f"❌ Could not click 'Create timesheet'. Screenshot -> {name}"
            state = _wait_for_save_submit_chip(self._page, timeout_ms=SHORT_TIMEOUT_MS)

        if state == "save":
            if not _click_save(self._page):
                return False, "❌ Could not click 'Save' before submit."
            _saw_saved_toast(self._page)
            state = "submit"

        if state == "submit":
            if not _click_submit(self._page):
                return False, "❌ Could not click 'Submit for approval'."
            if not _wait_until_submitted(self._page, timeout_ms=DEFAULT_TIMEOUT_MS):
                name = f"napta_submit_verify_{ts()}.png"
                with suppress_exc(): self._page.screenshot(path=_shot(name), full_page=True)
                return False, f"❌ Submit click didn't finalize. Screenshot -> {name}"
            with suppress_exc(): self._view_cache_path.unlink()
            return True, "✅ Submitted for approval."

        return False, "❌ Unknown state while submitting."

    def _submit_next_week_fast(self) -> Tuple[bool, str]:
        self._ensure_session(headless=True)
        _, err = _safe_run(lambda: self._open_timesheet(), "page load")
        if err:
            return False, err

        if self._on_login_page():
            name = f"napta_login_required_{ts()}.png"; self._page.screenshot(path=_shot(name), full_page=True)
            return False, f"⛔ Napta login required. Please open https://app.napta.io once in Chrome. Screenshot -> {name}"

        if not self._go_to_next_week():
            name = f"napta_error_{ts()}.png"; self._page.screenshot(path=_shot(name), full_page=True)
            return False, f"❌ Could not navigate to next week. Screenshot -> {name}"

        state = _wait_for_save_submit_chip(self._page, timeout_ms=SHORT_TIMEOUT_MS)
        if state is None:
            return True, "ℹ️ Next week already submitted."

        if state == "save":
            if not _click_save(self._page):
                return False, "❌ Could not click 'Save' before submit."
            _saw_saved_toast(self._page)
            state = "submit"

        if state == "submit":
            if not _click_submit(self._page):
                return False, "❌ Could not click 'Submit for approval'."
            if not _wait_until_submitted(self._page, timeout_ms=DEFAULT_TIMEOUT_MS):
                name = f"napta_submit_verify_{ts()}.png"
                with suppress_exc(): self._page.screenshot(path=_shot(name), full_page=True)
                return False, f"❌ Submit click didn't finalize. Screenshot -> {name}"
            with suppress_exc(): self._view_cache_path.unlink()
            return True, "✅ Next week submitted for approval."

        if state == "create":
            if not _click_create(self._page):
                name = f"napta_create_failure_{ts()}.png"; self._page.screenshot(path=_shot(name), full_page=True)
                return False, f"❌ Could not click 'Create timesheet'. Screenshot -> {name}"
            # After creating, save+submit if available
            state = _wait_for_save_submit_chip(self._page, timeout_ms=SHORT_TIMEOUT_MS)
            if state == "save":
                if not _click_save(self._page):
                    return False, "❌ Could not click 'Save' after creating."
                _saw_saved_toast(self._page)
                state = "submit"
            if state == "submit":
                if not _click_submit(self._page):
                    return False, "❌ Could not click 'Submit for approval'."
                with suppress_exc(): self._view_cache_path.unlink()
                return True, "✅ Next week submitted for approval."

        return False, "❌ Unknown state while submitting."