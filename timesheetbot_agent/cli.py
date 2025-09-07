# timesheetbot_agent/cli.py
from __future__ import annotations

import sys
from typing import Optional

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
    show_vibrant_help,  # <— bring the vibrant help block into scope
)

HELP_TEXT = (
    "Type in natural language (or use commands):\n"
    "  – e.g. 'AL 1st to 3rd June', 'sick leave on 10 Sep', '5 and 7 Aug mc'\n"
    "Commands: /show, /clear, /deregister, /generate, /comment, /help, /back, /email, /quit"
)


def ensure_profile() -> dict:
    prof = load_profile()
    if not prof:
        panel("⚠️ No registration found. Let's get you set up.")
        prof = run_registration_interactive()
    return prof


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


def napta_loop(profile: dict) -> None:
    banner(f"{profile.get('name')} <{profile.get('email')}>")
    panels(["(Napta flow coming soon) 🙏"])
    # Minimal placeholder flow
    input_prompt("Press Enter to return…")
    panel("↩️  Back to main menu.")


def main(argv: Optional[list] = None) -> int:
    # Show top-level banner (no profile yet)
    banner("Timesheet BOT agent — PALO IT")

    while True:
        choice = menu(
            "Choose an option:",
            [
                "Napta Timesheet",
                "GovTech Timesheet",
                "Registration",
                "Quit",
            ],
        )

        if choice == "1":
            profile = ensure_profile()
            napta_loop(profile)

        elif choice == "2":
            profile = ensure_profile()
            # default the client and persist if missing
            if not profile.get("client"):
                profile["client"] = "GovTech"
                save_profile(profile)
            govtech_loop(profile)

        elif choice == "3":
            run_registration_interactive()

        elif choice == "4":
            panel("Goodbye! 👋")
            return 0

        else:
            panel("Please pick 1–4.")

    # Unreachable, but keeps type-checkers happy
    # (The loop exits via returns above.)
    # return 0


if __name__ == "__main__":
    raise SystemExit(main())
