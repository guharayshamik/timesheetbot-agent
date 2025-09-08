# timesheetbot_agent/cli.py
from __future__ import annotations

import sys
from typing import Optional, List, Tuple
from datetime import datetime
import re

from . import fitnet
from .ui import fitnet_header, fitnet_commands  # decorative helpers if present

from .storage import (
    load_profile,
    save_profile,
    load_session,
    clear_session,
    clear_profile,
)
from .registration import run_registration_interactive
from .engine import Engine

# Pretty UI helpers (see timesheetbot_agent/ui.py)
from .ui import (
    banner,
    menu,
    input_prompt,
    panel,
    panels,
    note,
    show_vibrant_help,
)

HELP_TEXT = (
    "Type in natural language (or use commands):\n"
    "  – e.g. 'AL 1st to 3rd June', 'sick leave on 10 Sep', '5 and 7 Aug mc'\n"
    "Commands: /show, /clear, /deregister, /generate, /comment, /help, /back, /email, /quit"
)


# ------------------------------ profile helpers ------------------------------

def ensure_profile() -> dict:
    prof = load_profile()
    if not prof:
        panel("⚠️ No registration found. Let's get you set up.")
        prof = run_registration_interactive()
    return prof


# ------------------------------ generic helpers ------------------------------

def show_help() -> None:
    panels([HELP_TEXT])


def show_session_box() -> None:
    sess = load_session()
    month = sess.get("month", "—")
    leaves = sess.get("leave_details", [])
    lines = [
        f"Month: {month}",
        f"Leave details: {leaves}",
    ]
    panels(lines)


# ------------------------------ GovTech flow ---------------------------------

def govtech_loop(profile: dict) -> None:
    eng = Engine(profile)
    eng.reset_session()  # start fresh so old leaves aren’t carried over

    banner(f"{profile.get('name')} <{profile.get('email')}>")
    show_vibrant_help()
    print()  # one blank line before the prompt

    while True:
        try:
            s = input_prompt("›")
        except (EOFError, KeyboardInterrupt):
            panel("👋 Bye!")
            return

        if not s:
            continue

        cmd = s.strip()

        # Core commands
        if cmd in ("/quit", "/q"):
            panel("👋 Bye!")
            sys.exit(0)

        if cmd == "/back":
            panel("↩️  Back to main menu.")
            return

        if cmd == "/help":
            show_help()
            continue

        if cmd == "/show":
            show_session_box()
            continue

        if cmd == "/clear":
            clear_session()
            panel("🧹 Session cleared.")
            continue

        if cmd == "/deregister":
            clear_profile()
            clear_session()
            panel("👋 Deregistered and session cleared. Returning to main menu.")
            return

        # Hand over to engine (includes /generate handling and /comment)
        out_lines = eng.handle_text(cmd)
        panels(out_lines)


# ------------------------------ Napta placeholder ----------------------------

def napta_loop(profile: dict) -> None:
    banner(f"{profile.get('name')} <{profile.get('email')}>")
    panels(["(Napta flow coming soon) 🙏"])
    input_prompt("Press Enter to return…")
    panel("↩️  Back to main menu.")


# ------------------------------ Fitnet flow ----------------------------------

def _key_to_dt(key: str) -> datetime:
    """
    Convert Engine's 'dd-Month' key (e.g., '11-September') to a datetime
    using the current year.
    """
    day_str, month_name = key.split("-", 1)
    day = int(day_str)
    month = datetime.strptime(month_name, "%B").month
    year = datetime.now().year
    return datetime(year, month, day)


def _run_fitnet_applies(commit: bool) -> List[str]:
    """
    Iterate all queued leaves in the current session and call fitnet.apply_leave.
    Returns UI lines to display.
    """
    sess = load_session()
    leaves = sess.get("leave_details", [])
    remarks = sess.get("remarks", {}) or {}

    if not leaves:
        return ["⚠️ No leaves recorded yet. Type things like: `mc on 11 Sep` or `annual leave 1–3 Aug`."]

    lines: List[str] = []
    for (start_key, end_key, leave_type) in leaves:
        try:
            start_dt = _key_to_dt(start_key)
            end_dt = _key_to_dt(end_key)
        except Exception as e:
            lines.append(f"❌ Skipped {start_key}–{end_key}: bad date key ({e}).")
            continue

        # Per-day remarks supported via start_key
        comment = remarks.get(start_key, "")
        half_day = None  # engine doesn't capture AM/PM; extend later if needed

        ok, msg, shot = fitnet.apply_leave(
            start=start_dt,
            end=end_dt,
            leave_type=leave_type,
            comment=comment,
            half_day=half_day,
            commit=commit,                 # preview by default; commit when asked
            screenshot_to=None,            # could capture if desired
        )

        prefix = "✅" if ok else "❌"
        when = f"{start_key}" if start_key == end_key else f"{start_key}–{end_key}"
        lines.append(f"{prefix} {leave_type} {when}: {msg}")

    if not commit:
        lines.append("👀 Preview mode: forms were filled in Fitnet but NOT saved. Review the browser and click Save manually if all looks good.")
        lines.append("Tip: run `/commit` to save automatically next time.")
    else:
        lines.append("📌 Done. Entries should now appear in Fitnet. (Napta will pick it up per your usual nightly sync.)")

    return lines


def fitnet_loop(profile: dict) -> None:
    # Fresh parsing session for Fitnet too (so you can try a different month)
    eng = Engine(profile)
    eng.reset_session()

    banner(f"{profile.get('name')} <{profile.get('email')}>")
    try:
        # If these helpers exist, they provide a nice header/help block.
        fitnet_header()
        fitnet_commands()
    except Exception:
        panels([
            "Fitnet (Leave) — safe preview by default.",
            "Commands: /login, /preview, /commit, /show, /clear, /help, /back, /quit",
            "Examples:",
            "  - mc on 11 Sep",
            "  - annual leave 1–3 Aug",
            "  - /comment 11 Sep OIL",
            "Then run `/preview` to prefill Fitnet (no save), or `/commit` to save.",
        ])
    print()

    while True:
        try:
            s = input_prompt("fitnet›")
        except (EOFError, KeyboardInterrupt):
            panel("👋 Bye!")
            return

        if not s:
            continue

        cmd = s.strip().lower()

        # global exits
        if cmd in ("/quit", "/q"):
            panel("👋 Bye!")
            sys.exit(0)
        if cmd == "/back":
            panel("↩️  Back to main menu.")
            return

        # help & session mgmt
        if cmd == "/help":
            panels([
                "Fitnet commands:",
                "  /login   — open a browser to capture SSO cookies (one-time)",
                "  /preview — prefill Fitnet (no save), for all leaves you've typed",
                "  /commit  — save in Fitnet (careful!)",
                "  /show    — show the queued leaves",
                "  /clear   — clear current session",
                "  /back    — return to main menu",
            ])
            continue

        if cmd == "/show":
            show_session_box()
            continue

        if cmd == "/clear":
            clear_session()
            panel("🧹 Session cleared.")
            continue

        # Fitnet login capture
        if cmd == "/login":
            try:
                path = fitnet.login_interactive()
                panels([f"✅ Session captured to {path}", "You can now use /preview or /commit."])
            except Exception as e:
                panel(f"❌ Login capture failed: {e}")
            continue

        # Preview / Commit actions
        if cmd == "/preview":
            panels(_run_fitnet_applies(commit=False))
            continue

        if cmd == "/commit":
            panels(_run_fitnet_applies(commit=True))
            continue

        # Otherwise treat as natural-language leave text (reuse Engine parser)
        out_lines = eng.handle_text(s)
        panels(out_lines)


# ------------------------------ main menu ------------------------------------

def main(argv: Optional[list] = None) -> int:
    banner("Timesheet BOT agent — PALO IT")

    while True:
        choice = menu(
            "Choose an option:",
            [
                "Napta Timesheet",
                "GovTech Timesheet",
                "Fitnet (Leave)",
                "Registration",
                "Quit",
            ],
        )

        if choice == "1":
            profile = ensure_profile()
            napta_loop(profile)

        elif choice == "2":
            profile = ensure_profile()
            if not profile.get("client"):
                profile["client"] = "GovTech"
                save_profile(profile)
            govtech_loop(profile)

        elif choice == "3":
            profile = ensure_profile()
            fitnet_loop(profile)

        elif choice == "4":
            run_registration_interactive()

        elif choice == "5":
            panel("Goodbye! 👋")
            return 0

        else:
            panel("Please pick 1–5.")

    # Unreachable
    # return 0


if __name__ == "__main__":
    raise SystemExit(main())
