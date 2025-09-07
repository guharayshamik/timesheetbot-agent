from __future__ import annotations
import sys
from typing import Optional

from .storage import load_profile, save_profile, load_session, clear_session, clear_profile
from .registration import run_registration_interactive
from .engine import Engine

WELCOME = """\
Welcome, I am your Timesheet BOT agent – PALO IT
I am here to assist in filling up your timesheet.
"""

MENU = """\
Choose an option:
1) Napta Timesheet
2) GovTech Timesheet
3) Registration
4) Quit
"""

HELP = """\
Type in natural language (or use commands):
  – e.g. 'AL 1st to 3rd June', 'sick leave on 10 Sep', '5 and 7 Aug mc'
Commands: /show, /clear, /deregister, /generate, /help, /back, /quit
"""

def _press_enter():
    try:
        input("\nPress Enter to continue… ")
    except KeyboardInterrupt:
        print()
        sys.exit(0)

def ensure_profile() -> dict:
    prof = load_profile()
    if not prof:
        print("No registration found.")
        prof = run_registration_interactive()
    return prof

def govtech_loop(profile: dict) -> None:
    print()
    print(f"Using profile: {profile['name']} <{profile['email']}>")

    engine = Engine(profile)
    # start fresh so old leaves aren’t carried over
    engine.reset_session()

    print("\nLLM mode ON.")
    print("Describe your work/leave in plain English, e.g.:")
    print("• \"generate timesheet for August\"")
    print("• \"annual leave 11–13 Aug\"")
    print("• \"sick leave on 11 Aug\"")
    print("\nType in natural language (or use commands):")
    print("  – e.g. 'AL 1st to 3rd June', 'sick leave on 10 Sep', '5 and 7 Aug mc'")
    print("Commands: /show, /clear, /deregister, /generate, /help, /back, /quit\n")

    while True:
        try:
            s = input("› ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return

        if not s:
            continue

        # built-in commands
        if s in ("/quit", "/q"):
            sys.exit(0)

        if s == "/back":
            return

        if s == "/help":
            print(HELP)
            continue

        if s == "/show":
            print()
            sess = load_session()
            print("Month:", sess.get("month"))
            print("Leave details:", sess.get("leave_details", []))
            continue

        if s == "/clear":
            clear_session()
            print("🧹 Session cleared.")
            continue

        if s == "/deregister":
            clear_profile()
            clear_session()
            print("👋 Deregistered and session cleared. Returning to main menu.")
            return

        # hand over to engine (includes /generate handling)
        for line in engine.handle_text(s):
            print(line)

def main(argv: Optional[list] = None) -> int:
    print(WELCOME)

    while True:
        print(MENU)
        try:
            choice = input("Enter choice (1–4): ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye! 👋")
            return 0

        if choice == "1":
            print("\n(Napta flow coming soon) 🙏")
            _press_enter()
            continue

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
            print("Goodbye! 👋")
            return 0

        else:
            print("Please pick 1–4.")

if __name__ == "__main__":
    raise SystemExit(main())
