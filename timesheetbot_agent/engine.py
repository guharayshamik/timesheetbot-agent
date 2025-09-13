# timesheetbot_agent/engine.py
from __future__ import annotations
import logging, re
from pathlib import Path
from . import mailer
from datetime import datetime
from typing import List, Tuple, Optional, Dict, Any, Sequence

from .storage import load_session, save_session, clear_session
from .ph_sg import PUBLIC_HOLIDAYS  # pass PHs to the generator

log = logging.getLogger("timesheetbot_engine")

# ---------- config ----------
RANGE_SEP = r"(?:-|–|—|−|~|to|until|till|through|thru)"

_MONTHS = {
    "jan": "January", "feb": "February", "mar": "March", "apr": "April",
    "may": "May", "jun": "June", "jul": "July", "aug": "August",
    "sep": "September", "sept": "September", "oct": "October",
    "nov": "November", "dec": "December",
}

_LEAVE_SYNONYMS = {
    # --- Sick ---
    "sick": "Sick Leave",
    "mc": "Sick Leave",
    "medical": "Sick Leave",
    # variants / typos
    "sick leave": "Sick Leave",
    "sickleave": "Sick Leave",
    "sikcleave": "Sick Leave",
    "sicleave": "Sick Leave",
    "sik leave": "Sick Leave",
    "sic leave": "Sick Leave",

    # --- Annual ---
    "annual": "Annual Leave",
    "vacation": "Annual Leave",
    "al": "Annual Leave",
    # variants / typos
    "annual leave": "Annual Leave",
    "annualleave": "Annual Leave",
    "anual leave": "Annual Leave",
    "anualleave": "Annual Leave",
    "ann leave": "Annual Leave",
    "pto": "Annual Leave",
    "leave": "Annual Leave",   # liberal
    "oil": "Annual Leave",
    "oli": "Annual Leave",

    # --- Childcare ---
    "childcare": "Childcare Leave",
    "cc": "Childcare Leave",
    "childcare leave": "Childcare Leave",
    "child care": "Childcare Leave",
    "child care leave": "Childcare Leave",

    # --- NS ---
    "ns": "NS Leave",
    "national service": "NS Leave",
    "ns leave": "NS Leave",

    # --- Weekend Efforts (OT on Sat/Sun) ---
    "weekend": "Weekend Efforts",
    "weekend effort": "Weekend Efforts",
    "weekend efforts": "Weekend Efforts",
    "weekend work": "Weekend Efforts",
    "worked weekend": "Weekend Efforts",
    "we": "Weekend Efforts",
    "wknd": "Weekend Efforts",
    "wknd effort": "Weekend Efforts",
    "week-end": "Weekend Efforts",

    # --- Public Holiday Efforts (OT on PH) ---
    "public holiday effort": "Public Holiday Efforts",
    "public holiday efforts": "Public Holiday Efforts",
    "public holiday work": "Public Holiday Efforts",
    "ph effort": "Public Holiday Efforts",
    "ph efforts": "Public Holiday Efforts",
    "ph work": "Public Holiday Efforts",
    "ph ot": "Public Holiday Efforts",
    "ph-ot": "Public Holiday Efforts",

    # --- Half day ---
    "half day": "Half Day",
    "halfday": "Half Day",
    "half-day": "Half Day",
    "hafday": "Half Day",
    "haf day": "Half Day",
    "hd": "Half Day",
    "1/2 day": "Half Day",
}

_ALLOWED_TYPES = {v for v in _LEAVE_SYNONYMS.values()} | {
    "Sick Leave", "Annual Leave", "Childcare Leave", "NS Leave",
    "Weekend Efforts", "Public Holiday Efforts", "Half Day",
}

# ---------- helpers ----------
def _full_month_name(token: str) -> Optional[str]:
    t = token.strip()
    if len(t) <= 3:
        return _MONTHS.get(t.lower())
    cap = t.capitalize()
    if cap in _MONTHS.values():
        return cap
    return _MONTHS.get(t[:3].lower())

def _month_from_text(text: str) -> Optional[str]:
    m = re.search(r"\b(for|in)\s+([A-Za-z]{3,9})\b", text, flags=re.I)
    if m:
        return _full_month_name(m.group(2))
    m2 = re.search(r"\b([A-Za-z]{3,9})\s+(timesheet|sheet)\b", text, flags=re.I)
    if m2:
        return _full_month_name(m2.group(1))
    m3 = re.search(r"\b([A-Za-z]{3,9})\b", text, flags=re.I)
    if m3 and re.search(r"\b(generate|submit|create)\b", text, flags=re.I):
        return _full_month_name(m3.group(1))
    return None

def _std_day(tok: str) -> Optional[int]:
    d = re.sub(r"(st|nd|rd|th)$", "", tok.strip(), flags=re.I)
    if d.isdigit():
        v = int(d)
        if 1 <= v <= 31:
            return v
    return None

def _fmt(day: int, month_name: str) -> str:
    return f"{day:02d}-{month_name}"

def _split(date_str: str) -> Tuple[int, str]:
    d, m = date_str.split("-", 1)
    return int(d), m

def _valid(day: int, month_name: str) -> bool:
    try:
        month_num = datetime.strptime(month_name, "%B").month
        # Use current year instead of 2025:
        datetime(datetime.now().year, month_num, day)
        return True
    except ValueError:
        return False


def _parse_day_pairs(text: str) -> List[Tuple[int, str]]:
    pairs: List[Tuple[int, str]] = []
    for m in re.finditer(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?(?:\s+|[-–—])([A-Za-z]{{3,9}})\b", text, flags=re.I):
        d = _std_day(m.group(1)); mon = _full_month_name(m.group(2))
        if d and mon: pairs.append((d, mon))
    for m in re.finditer(r"\b([A-Za-z]{3,9})\s+(\d{1,2})(?:st|nd|rd|th)?\b", text, flags=re.I):
        mon = _full_month_name(m.group(1)); d = _std_day(m.group(2))
        if d and mon: pairs.append((d, mon))
    return pairs

def _parse_range(text: str) -> Optional[Tuple[Tuple[int, str], Tuple[int, str]]]:
    mA = re.search(
        rf"\b(?:between\s+)?(\d{{1,2}})(?:st|nd|rd|th)?\s*{RANGE_SEP}\s*(\d{{1,2}})(?:st|nd|rd|th)?\s+([A-Za-z]{{3,9}})\b",
        text, flags=re.I)
    if mA:
        d1 = _std_day(mA.group(1)); d2 = _std_day(mA.group(2)); mon = _full_month_name(mA.group(3))
        if d1 and d2 and mon: return (d1, mon), (d2, mon)
    mB = re.search(
        rf"\b([A-Za-z]{{3,9}})\s+(\d{{1,2}})(?:st|nd|rd|th)?\s*{RANGE_SEP}\s*(\d{{1,2}})(?:st|nd|rd|th)?\b",
        text, flags=re.I)
    if mB:
        mon = _full_month_name(mB.group(1)); d1 = _std_day(mB.group(2)); d2 = _std_day(mB.group(3))
        if d1 and d2 and mon: return (d1, mon), (d2, mon)
    return None

def _parse_range_no_month(text: str) -> Optional[Tuple[int, int]]:
    m = re.search(
        rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s*{RANGE_SEP}\s*(\d{{1,2}})(?:st|nd|rd|th)?\b(?!\s*[A-Za-z])",
        text, flags=re.I)
    if not m: return None
    d1 = _std_day(m.group(1)); d2 = _std_day(m.group(2))
    if d1 and d2: return (min(d1, d2), max(d1, d2))
    return None

def _parse_single_no_month(text: str) -> Optional[int]:
    m = re.search(r"\bon\s+(\d{1,2})(?:st|nd|rd|th)?\b(?!\s*[A-Za-z])", text, flags=re.I)
    if m: return _std_day(m.group(1))
    month_keys = "|".join(_MONTHS.keys())
    m2 = re.search(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\b(?!\s*(?:{month_keys}))", text, flags=re.I)
    if m2 and not _parse_range(text): return _std_day(m2.group(1))
    return None

def _extract_days_list(blob: str) -> List[int]:
    parts = re.split(r"(?:\s*,\s*|\s+and\s+|\s*&\s*)", blob.strip(), flags=re.I)
    out = []
    for p in parts:
        d = _std_day(p)
        if d: out.append(d)
    return out

def _parse_multi_with_month(text: str) -> Optional[Tuple[List[int], str]]:
    m = re.search(
        r"\b((?:\d{1,2}(?:st|nd|rd|th)?(?:\s*,\s*|\s+and\s+|\s*&\s*)?)+)\s+([A-Za-z]{3,9})\b",
        text, flags=re.I)
    if not m: return None
    days = _extract_days_list(m.group(1)); mon = _full_month_name(m.group(2))
    if mon and days: return days, mon
    return None

def _parse_multi_no_month(text: str) -> Optional[List[int]]:
    m = re.search(
        r"\b((?:\d{1,2}(?:st|nd|rd|th)?(?:\s*,\s*|\s+and\s+|\s*&\s*)?)+)\b(?!\s*[A-Za-z])",
        text, flags=re.I)
    if not m: return None
    days = _extract_days_list(m.group(1))
    return days or None

def _ranges_overlap(ns: str, ne: str, os_: str, oe: str) -> bool:
    s1, m1 = _split(ns); e1, m1b = _split(ne); s2, m2 = _split(os_); e2, m2b = _split(oe)
    if m1 != m2 or m1b != m2b: return False
    return not (e1 < s2 or s1 > e2)

def _find_overlap(leave_details, start: str, end: str):
    """Return (index, existing_tuple) if any entry overlaps [start,end] in same month."""
    for i, (s, e, t) in enumerate(leave_details):
        if _ranges_overlap(start, end, s, e):
            return i, (s, e, t)
    return None, None

def _extract_comment_after(text: str, matched_span: tuple[int, int]) -> str:
    """Return everything after the matched date as the comment, stripped of separators."""
    _, end = matched_span
    tail = text[end:].strip()
    tail = re.sub(r'^[\s:–—-]+', '', tail).strip()
    return tail

def _first_date_with_span(text: str) -> Optional[Tuple[int, str, Tuple[int, int]]]:
    """
    Find the first explicit 'day month' or 'month day' in the *original* text and return:
    (day_int, MonthFullName, span_of_that_exact_match_in_text)
    """
    # day first
    m = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?(?:\s+|[-–—])([A-Za-z]{3,9})\b", text, flags=re.I)
    if m:
        d = _std_day(m.group(1)); mon = _full_month_name(m.group(2))
        if d and mon:
            return d, mon, m.span()
    # month first
    m = re.search(r"\b([A-Za-z]{3,9})\s+(\d{1,2})(?:st|nd|rd|th)?\b", text, flags=re.I)
    if m:
        mon = _full_month_name(m.group(1)); d = _std_day(m.group(2))
        if d and mon:
            return d, mon, m.span()
    return None

def _detect_leave_type(text: str) -> Optional[str]:
    import re
    specific_hit = None
    generic_hit = None
    for k, canon in _LEAVE_SYNONYMS.items():
        if re.search(rf"\b{re.escape(k)}\b", text, flags=re.I):
            if k == "leave":
                generic_hit = canon      # remember as fallback
                continue                 # skip for now; try to find something specific
            # first specific match wins (ns, sick, childcare, etc.)
            specific_hit = canon
            break
    if specific_hit:
        return specific_hit
    # If user literally typed a full allowed type, accept that too
    for a in _ALLOWED_TYPES:
        if re.search(rf"\b{re.escape(a)}\b", text, flags=re.I):
            return a
    return generic_hit

# ---------- Engine ----------
class Engine:
    """Parser; state is persisted via storage session."""
    def __init__(self, profile: Dict[str, Any]):
        self.profile = profile

    def reset_session(self) -> None:
        clear_session()

    def show_state(self) -> Dict[str, Any]:
        return load_session()

    def handle_text(self, text: str) -> List[str]:
        sess = load_session()
        msgs: List[str] = []

        # ---- pending overlap resolution
        if "pending_overlap" in sess:
            ans = text.strip().lower()
            overlap = sess["pending_overlap"]
            if ans in ("yes", "y", "yeah", "yep", "sure"):
                sess["leave_details"][overlap["idx"]] = overlap["new"]
                _, new_month = _split(overlap["new"][0])
                sess["recent_leave_month"] = new_month
                sess["month"] = new_month
                sess.pop("pending_overlap")
                save_session(sess)
                return [
                    f"🔄 Replaced {overlap['old'][2]} {overlap['old'][0]}–{overlap['old'][1]} with {overlap['new'][2]}.",
                    "You can add more leaves or type `/generate`.",
                ]
            elif ans in ("no", "n", "nope"):
                sess.pop("pending_overlap"); save_session(sess)
                return ["❌ Kept your original leave. Discarded the new one."]

        # ---- generate intent?
        wants_generate = bool(re.search(r"\b(generate|submit|create)\b.*\b(timesheet|sheet)\b", text, flags=re.I)) \
                         or bool(re.search(r"^/generate\b|\bgenerate\b", text, flags=re.I))

        # ---- current month?
        month_mentioned = _month_from_text(text)
        if month_mentioned:
            sess["month"] = month_mentioned

        # ---- ADD COMMENT command
        if re.search(r"^(?:/comment|/remark|add\s+comment|remark)\b", text, flags=re.I):
            start_key = None
            comment = None

            found = _first_date_with_span(text)
            if found:
                d, mon, span = found
                if not _valid(d, mon):
                    return [f"⚠️ {d}-{mon} is not a valid date."]
                start_key = _fmt(d, mon)
                comment = _extract_comment_after(text, span)
                sess["recent_leave_month"] = mon
                sess["month"] = mon
            else:
                # allow '/comment 11 OIL' when month is known in session
                single_no_mon = _parse_single_no_month(text)
                if single_no_mon:
                    fallback_mon = sess.get("recent_leave_month") or sess.get("month")
                    if not fallback_mon:
                        return ["⚠️ Please include a month (e.g., `/comment 11 Sep OIL`)."]
                    if not _valid(single_no_mon, fallback_mon):
                        return [f"⚠️ {single_no_mon}-{fallback_mon} is not a valid date."]
                    m = re.search(rf"\b{single_no_mon}(?:st|nd|rd|th)?\b", text, flags=re.I)
                    start_key = _fmt(single_no_mon, fallback_mon)
                    comment = _extract_comment_after(text, m.span()) if m else ""
                    sess["recent_leave_month"] = fallback_mon
                    sess["month"] = fallback_mon

            if not start_key:
                return ["⚠️ I couldn’t find the date. Example: `/comment 11 Sep OIL`."]

            comment = (comment or "").strip()
            if not comment:
                return ["⚠️ I didn’t catch the comment text. Example: `/comment 11 Sep: OIL`."]

            remarks = sess.get("remarks", {})
            remarks[start_key] = comment
            sess["remarks"] = remarks
            save_session(sess)
            return [f"📝 Added remark “{comment}” for {start_key}. You can `/show` or `/generate`."]

        # ---- EMAIL command
        if re.search(r"^/email\b", text, flags=re.I):
            sess_meta = load_session()
            path_str = sess_meta.get("last_generated_path")
            meta = sess_meta.get("last_generated_meta", {})
            if not path_str:
                return ["⚠️ No generated file found. Please `/generate` the timesheet first."]

            attachment = Path(path_str)
            if not attachment.exists():
                return [f"⚠️ I can't find the file on disk: {attachment}. Please `/generate` again."]

            # Recipient: if user typed an email with the command, use that.
            #m = re.search(r"/email\\s+(\\S+@\\S+)", text, flags=re.I)
            #m = re.search(r"/email\s+(\S+@\S+)", text, flags=re.I)
            m = re.search(r"/email\s+([^\s,;]+@[^\s,;]+)", text, flags=re.I)

            to_addr = None
            if m:
                to_addr = m.group(1)
            else:
                # fallback to stored manager email if present
                to_addr = (self.profile.get("manager_email") or "").strip()

            if not to_addr:
                return [
                    "⚠️ I don't know who to send this to. Either:",
                    "  • type `/email manager@yourorg.com`, or",
                    "  • add `manager_email` to your profile/registration.",
                ]

            month = meta.get("month") or "Your"
            year = meta.get("year") or datetime.now().year
            employee = self.profile.get("name") or "Employee"

            # NEW: personalize greeting from Reporting Officer's first name (saved during registration)
            mgr_first = (self.profile.get("manager_first_name") or "").strip()
            greeting = f"Hi {mgr_first}," if mgr_first else "Hi,"

            subject = f"{month} {year} Timesheet — {employee}"
            body_lines = [
                greeting,
                "",
                f"Please find attached my {month} {year} timesheet.",
                "",
                "Regards,",
                employee,
            ]
            # Use CRLF to be safe across clients; mailer handles AppleScript 'return' too
            body = "\r\n".join(body_lines)



            try:
                mailer.compose_with_best_available(
                    to=[to_addr],
                    subject=subject,
                    body=body,
                    attachment=attachment,
                    cc=[self.profile.get("email")] if self.profile.get("email") else None,
                )
                return [
                    "✉️ Opening your email client with the timesheet attached…",
                    f"To: {to_addr}",
                    f"Subject: {subject}",
                    "✅ Review and hit Send.",
                ]
            except Exception as e:
                return [
                    f"❌ Couldn't open a compose window automatically ({e}).",
                    f"Please email this file manually: {attachment}",
                ]


        # ---- leave type?
        leave_type = _detect_leave_type(text)

        # for k, canon in _LEAVE_SYNONYMS.items():
        #     if re.search(rf"\b{re.escape(k)}\b", text, flags=re.I):
        #         leave_type = canon; break
        # if not leave_type:
        #     for a in _ALLOWED_TYPES:
        #         if re.search(rf"\b{re.escape(a)}\b", text, flags=re.I):
        #             leave_type = a; break

        leave_details: List[Sequence] = sess.get("leave_details", [])

        # ---- parse dates
        date_range = _parse_range(text)
        date_pairs = None
        multi_with_month = None
        if not date_range:
            date_pairs = _parse_day_pairs(text)
            multi_with_month = _parse_multi_with_month(text)

        if not date_range:
            no_mon_range = _parse_range_no_month(text)
            fallback_mon = sess.get("recent_leave_month") or sess.get("month")
            if no_mon_range and fallback_mon:
                d1, d2 = no_mon_range
                date_range = ((d1, fallback_mon), (d2, fallback_mon))
            elif no_mon_range and not fallback_mon:
                return ["⚠️ I see a date range but no month. Please include it (e.g., `5–7 August`)."]

        single_no_mon = None; multi_no_month = None
        if not date_range and not multi_with_month and not date_pairs:
            single_no_mon = _parse_single_no_month(text)
            if not single_no_mon:
                multi_no_month = _parse_multi_no_month(text)

        # ---- multi (discrete) with month
        if leave_type and multi_with_month:
            days, mon = multi_with_month
            for d in days:
                if not _valid(d, mon):
                    return [f"⚠️ {d}-{mon} is not a valid date."]
            recorded = []
            for d in days:
                start = _fmt(d, mon)
                idx, existing = _find_overlap(leave_details, start, start)
                if existing and existing[2] != leave_type:
                    sess["pending_overlap"] = {"new": (start, start, leave_type), "old": existing, "idx": idx}
                    sess["recent_leave_month"] = mon; sess["month"] = mon; save_session(sess)
                    return [f"⚠️ {start} already has {existing[2]}. Replace with {leave_type}? (yes/no)"]
                leave_details.append((start, start, leave_type)); recorded.append(start)
            sess["leave_details"] = leave_details; sess["recent_leave_month"] = mon; sess["month"] = mon
            save_session(sess)
            return [f"✅ Recorded {leave_type} on {', '.join(recorded)}.", "You can add more or type `/generate`."]

        # ---- range
        if leave_type and date_range:
            (d1, m1), (d2, m2) = date_range
            if not _valid(d1, m1): return [f"⚠️ {d1}-{m1} is not a valid date."]
            if not _valid(d2, m2): return [f"⚠️ {d2}-{m2} is not a valid date."]
            start = _fmt(d1, m1); end = _fmt(d2, m2)
            idx, existing = _find_overlap(leave_details, start, end)
            if existing:
                sess["pending_overlap"] = {"new": (start, end, leave_type), "old": existing, "idx": idx}
                save_session(sess)
                return [f"⚠️ {start}–{end} already has {existing[2]}. Replace with {leave_type}? (yes/no)"]
            leave_details.append((start, end, leave_type))
            sess["leave_details"] = leave_details; sess["recent_leave_month"] = m1; sess["month"] = m1
            save_session(sess)
            return [f"✅ Recorded {leave_type} from {start} to {end}."]

        # ---- single with month
        if leave_type and date_pairs:
            day, mon = date_pairs[0]
            if not _valid(day, mon):
                return [f"⚠️ {day}-{mon} is not a valid date."]
            start = _fmt(day, mon)
            idx, existing = _find_overlap(leave_details, start, start)
            if existing and existing[2] != leave_type:
                sess["pending_overlap"] = {"new": (start, start, leave_type), "old": existing, "idx": idx}
                sess["recent_leave_month"] = mon; sess["month"] = mon; save_session(sess)
                return [f"⚠️ {start} already has {existing[2]}. Replace with {leave_type}? (yes/no)"]
            sess["pending_leave"] = {"leave_type": leave_type, "start_date": start, "end_date": None}
            sess["awaiting_confirmation"] = True
            sess["recent_leave_month"] = mon; sess["month"] = mon
            save_session(sess)
            return [f"🧐 Just to confirm, {leave_type} only for {start}? (yes/no)"]

        # ---- single without month
        if leave_type and single_no_mon:
            fallback_mon = sess.get("recent_leave_month") or sess.get("month")
            if not fallback_mon:
                return ["⚠️ I saw a day but no month. Include month (e.g., `10th June`)."]
            if not _valid(single_no_mon, fallback_mon):
                return [f"⚠️ {single_no_mon}-{fallback_mon} is not a valid date."]
            start = _fmt(single_no_mon, fallback_mon)
            sess["pending_leave"] = {"leave_type": leave_type, "start_date": start, "end_date": None}
            sess["awaiting_confirmation"] = True
            sess["recent_leave_month"] = fallback_mon; sess["month"] = fallback_mon
            save_session(sess)
            return [f"🧐 Did you mean {leave_type} only for {start}? (yes/no)"]

        # ---- multi without month
        if leave_type and multi_no_month:
            fallback_mon = sess.get("recent_leave_month") or sess.get("month")
            if not fallback_mon:
                return ["⚠️ I saw multiple days but no month. Include month (e.g., `5 and 7 Aug`)."]
            for d in multi_no_month:
                if not _valid(d, fallback_mon):
                    return [f"⚠️ {d}-{fallback_mon} is not a valid date."]
            recorded = []
            for d in multi_no_month:
                start = _fmt(d, fallback_mon)
                idx, existing = _find_overlap(leave_details, start, start)
                if existing and existing[2] != leave_type:
                    sess["pending_overlap"] = {"new": (start, start, leave_type), "old": existing, "idx": idx}
                    sess["recent_leave_month"] = fallback_mon; sess["month"] = fallback_mon; save_session(sess)
                    return [f"⚠️ {start} already has {existing[2]}. Replace with {leave_type}? (yes/no)"]
                leave_details.append((start, start, leave_type)); recorded.append(start)
            sess["leave_details"] = leave_details; sess["recent_leave_month"] = fallback_mon; sess["month"] = fallback_mon
            save_session(sess)
            return [f"✅ Recorded {leave_type} on {', '.join(recorded)}.", "You can add more or type `/generate`."]

        # ---- generate
        if wants_generate:
            month = month_mentioned or sess.get("recent_leave_month") or sess.get("month")
            if not month:
                return ["⚠️ I couldn't detect the month. Try: `generate timesheet for September`"]

            if sess.pop("awaiting_confirmation", False) is False and "pending_leave" in sess:
                p = sess.pop("pending_leave")
                leave_details.append((p["start_date"], p["start_date"], p["leave_type"]))
                sess["leave_details"] = leave_details

            save_session(sess)
            return self._generate(month, leave_details, sess.get("remarks", {}))

        # ---- awaiting yes/no for single day
        if sess.get("awaiting_confirmation"):
            ans = text.strip().lower()
            if ans in ("yes", "y", "yeah", "yep", "sure"):
                p = sess.pop("pending_leave", None)
                sess["awaiting_confirmation"] = False
                if p:
                    idx, existing = _find_overlap(leave_details, p["start_date"], p["start_date"])
                    if existing and existing[2] != p["leave_type"]:
                        sess["pending_overlap"] = {"new": (p["start_date"], p["start_date"], p["leave_type"]),
                                                   "old": existing, "idx": idx}
                        _, mon = _split(p["start_date"])
                        sess["recent_leave_month"] = mon; sess["month"] = mon; save_session(sess)
                        return [f"⚠️ {p['start_date']} already has {existing[2]}. Replace with {p['leave_type']}? (yes/no)"]
                    leave_details.append((p["start_date"], p["start_date"], p["leave_type"]))
                    sess["leave_details"] = leave_details
                    _, mon = _split(p["start_date"]); sess["recent_leave_month"] = mon; sess["month"] = mon
                    save_session(sess)
                    return [f"✅ Recorded {p['leave_type']} on {p['start_date']}.", "You can add more or type `/generate`."]
            elif ans in ("no", "n", "nope"):
                sess.pop("pending_leave", None); sess.pop("awaiting_confirmation", None); save_session(sess)
                return ["❌ Okay, cancelled. Please rephrase your leave request."]

        # ---- help
        current_month = sess.get("month") or sess.get("recent_leave_month")
        if current_month:
            return [
                "Examples:",
                f"- generate timesheet for {current_month}",
                f"- annual leave 11–13 {current_month[:3]}",
                f"- sick leave on 10 {current_month[:3]}",
                f"- /comment 11 {current_month[:3]} OIL",
                f"- /email manager@yourorg.com",
            ]
        else:
            return [
                "Examples:",
                "- generate timesheet for September",
                "- annual leave 11–13 Sep",
                "- sick leave on 10 Sep",
                "- /comment 11 Sep OIL",
                "⚠️ Please mention the month along with the date (e.g., 10th June).",
            ]

    # ---------- private ----------
    def _generate(
        self,
        month: str,
        leave_details: List[Tuple[str, str, str]],
        remarks: Dict[str, str],
    ) -> List[str]:
        out: List[str] = []
        out_dir = Path.cwd() / "generated_timesheets"
        out_dir.mkdir(exist_ok=True)

        try:
            month_int = datetime.strptime(month[:3], "%b").month
        except ValueError:
            return [f"❌ Invalid month: {month}. Try: `generate timesheet for September`."]

        year = datetime.now().year

        # Clear any previous "last generated" info up front
        sess = load_session()
        sess.pop("last_generated_path", None)
        sess.pop("last_generated_meta", None)
        save_session(sess)

        try:
            from .generators.govtech_excel import generate_cli as generate_excel_cli
            path = None
            # Try new signature (with remarks). If package not updated yet, fall back.
            try:
                path = generate_excel_cli(
                    self.profile,
                    month_int,
                    year,
                    leave_details,
                    out_dir,
                    public_holidays=PUBLIC_HOLIDAYS,
                    remarks=remarks,
                )
            except TypeError:
                path = generate_excel_cli(
                    self.profile,
                    month_int,
                    year,
                    leave_details,
                    out_dir,
                    public_holidays=PUBLIC_HOLIDAYS,
                )

            if not path:
                raise RuntimeError("Generator did not return a file path")

            sess = load_session()
            sess["last_generated_path"] = str(path)
            sess["last_generated_meta"] = {
                "month": month,
                "month_int": month_int,
                "year": year,
                "path": str(path),
            }
            save_session(sess)

            return [
                "📊 Generating your timesheet…",
                f"✅ Saved -> {path}",
                "Here’s your generated timesheet!",
            ]

        except Exception as e:
            import json
            payload = {
                "profile": self.profile,
                "month": month,
                "leave_details": leave_details,
                "remarks": remarks,
            }
            fname = out_dir / f"{month}_timesheet_payload.json"
            fname.write_text(json.dumps(payload, indent=2))

            # Record fallback artifact too
            sess = load_session()
            sess["last_generated_path"] = str(fname)
            sess["last_generated_meta"] = {
                "month": month,
                "month_int": month_int,
                "year": year,
                "path": str(fname),
                "fallback": True,
                "error": str(e),
            }
            save_session(sess)

            return [
                f"❌ Generator error: {e}. Falling back to JSON artifact.",
                "📊 Generating your timesheet (fallback)…",
                f"✅ Payload saved -> {fname}",
                "ℹ️ Hook your real generator by exposing "
                "`generate_cli(profile, month_int, year, leave_details, out_dir, public_holidays=..., remarks=...)`.",
            ]
