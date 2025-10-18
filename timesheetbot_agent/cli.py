# timesheetbot_agent/cli.py
from __future__ import annotations

import sys
from typing import Optional, List
from datetime import datetime

# UI
from .ui import (
    console,
    banner,
    menu,
    input_prompt,
    panel,
    panels,
    note,
    show_vibrant_help,
)

# Napta
from .napta import NaptaClient

# Engine / storage / registration
from .engine import Engine
from .storage import (
    load_profile,
    save_profile,
    load_session,
    clear_session,
    clear_profile,
)
from .registration import run_registration_interactive

# Pretty blocks
from rich.text import Text
from rich.panel import Panel
from rich.table import Table
from rich import box


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


def _drain_stdin_nonblocking() -> None:
    """Swallow any pending newlines so we don't print a duplicate prompt."""
    try:
        import select
        while select.select([sys.stdin], [], [], 0)[0]:
            sys.stdin.readline()
    except Exception:
        # best effort; ignore on platforms without select
        pass


# ------------------------------ GovTech flow ---------------------------------

def govtech_loop(profile: dict) -> None:
    eng = Engine(profile)
    eng.reset_session()  # start fresh so old leaves aren’t carried over

    banner(f"{profile.get('name')} <{profile.get('email')}>")
    show_vibrant_help()
    print()  # one blank line before the prompt

    while True:
        try:
            s = input_prompt("govtech_timesheet›")
        except (EOFError, KeyboardInterrupt):
            panel("👋 Bye!")
            return

        if not s:
            continue

        cmd = s.strip()

        # Core commands
        if cmd in ("/quit", "/q", "quit", "q", "/exit", "exit"):
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


# ------------------------------ Napta (simple) --------------------------------

def _bullet_line(s: str, style: str = "bold green") -> Text:
    return Text("• ", style="dim") + Text(s, style=style)


def _show_napta_simple_help_block() -> None:
    """Pretty 'NAPTA Chat mode ON + examples + commands' block."""
    # Chip header
    chip = Text.assemble(("⚡  NAPTA Chat mode", "bold"), ("  ON", "bold bright_green"))
    console.print(Panel(chip, border_style="bright_green", padding=(0, 1), box=box.SQUARE))

    # Subtitle
    console.print(Text("Describe your Napta action in plain English, e.g.:", style="bold cyan"))

    # Examples
    ex_tbl = Table.grid(padding=(0, 1))
    ex_tbl.add_column()
    ex_tbl.add_row(_bullet_line("'view' — Show THIS week entries"))
    ex_tbl.add_row(_bullet_line("'view next week' (or 'vnw') — Show NEXT week entries"))
    ex_tbl.add_row(_bullet_line("'save' — Save THIS week (draft)"))
    ex_tbl.add_row(_bullet_line("'submit' — Submit THIS week for approval"))
    ex_tbl.add_row(_bullet_line("'save next week' — Save NEXT week (draft)"))
    ex_tbl.add_row(_bullet_line("'submit next week' — Submit NEXT week for approval"))
    ex_tbl.add_row(_bullet_line("'ss' — Save then Submit (THIS week)"))
    console.print(
        Panel(
            ex_tbl,
            title="Examples",
            title_align="left",
            border_style="cyan",
            box=box.ROUNDED,
            padding=(0, 1),
        )
    )

    # Commands
    cmds = Text(
        "login   view   vnw (view next week)   save   snw (save next week)   submit   sbnw (submit next week)   ss (save & submit)   back   quit",
        style="bold magenta",
    )
    console.print(
        Panel(
            cmds,
            title="Commands",
            title_align="left",
            border_style="magenta",
            box=box.ROUNDED,
            padding=(0, 1),
        )
    )


def napta_loop(profile: dict) -> None:
    banner(f"{profile.get('name')} <{profile.get('email')}>")

    client = NaptaClient()
    panel(f"Napta auth status: {client.status()}")

    # Napta chat mode UI (chip + examples + commands)
    _show_napta_simple_help_block()

    # Notes
    console.print(Panel(
        "This uses your saved session or browser SSO cookies.\n"
        "‘login’ will open a browser window to sign in once (SSO), then save the session.",
        border_style="white",
        box=box.ROUNDED,
    ))
    console.print(Panel(
        "If headless login/save/submit fails, open https://app.napta.io once and sign in, then retry.",
        border_style="white",
        box=box.ROUNDED,
    ))

    while True:
        try:
            _drain_stdin_nonblocking()
            cmd = input_prompt("napta›").strip().lower()
        except (EOFError, KeyboardInterrupt):
            panel("👋 Bye!")
            return

        if not cmd:
            continue

        if cmd in ("/quit", "/q", "quit", "q", "/exit", "exit"):
            panel("👋 Bye!")
            sys.exit(0)

        if cmd in ("back", "/back"):
            panel("↩️  Back to main menu.")
            return

        # Save NEXT week
        if cmd in ("save next week", "/save next week", "save-next-week", "/save-next-week", "snw", "/snw"):
            ok, msg = client.save_next_week()
            panel(msg)
            continue

        # Submit NEXT week
        if cmd in ("submit next week", "/submit next week", "submit-next-week", "/submit-next-week", "sbnw", "/sbnw"):
            ok, msg = client.submit_next_week()
            panel(msg)
            continue

        # Save THIS week
        if cmd in ("save", "/save"):
            ok, msg = client.save_current_week()
            panel(msg)
            continue

        # Submit THIS week
        if cmd in ("submit", "/submit"):
            ok, msg = client.submit_current_week()
            panel(msg)
            continue

        # Save & Submit THIS week
        if cmd in ("ss", "/ss"):
            ok, msg = client.save_and_submit_current_week()
            panel(msg)
            continue

        if cmd in ("login", "/login"):
            # Headful login: opens a browser window for SSO and saves storage_state
            ok, msg = client.login()
            panel(msg)
            continue

        # View THIS week
        if cmd in ("view", "/view", "show", "/show"):
            ok, msg = client.view_week("current")
            panel(msg)
            continue

        # View NEXT week
        if cmd in ("view next week", "/view next week", "view-next-week", "/view-next-week", "vnw", "/vnw"):
            ok, msg = client.view_week("next")
            panel(msg)
            continue

        panel("⚠️ Unknown command. Use: login | view | vnw | save | save next week (snw) | submit | submit next week (sbnw) | ss | back | quit")


# ------------------------------ main menu ------------------------------------

def main(argv: Optional[list] = None) -> int:
    #banner("Timesheet BOT agent — PALO IT123")
    banner("CLI Tool")

    while True:
        choice = menu("Choose an option:", [
            "Napta Timesheet",
            "GovTech Timesheet",
            "Registration",
            "Quit",
        ])

        if choice == "1":
            profile = ensure_profile()
            napta_loop(profile)
        elif choice == "2":
            profile = ensure_profile()
            govtech_loop(profile)
        elif choice == "3":
            run_registration_interactive()
        elif choice == "4":
            panel("Goodbye! 👋")
            return 0
        else:
            panel("Please pick 1–4.")


if __name__ == "__main__":
    raise SystemExit(main())
