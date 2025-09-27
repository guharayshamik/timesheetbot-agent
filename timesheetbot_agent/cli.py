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

# Napta (simple current-week actions)
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

def _drain_stdin_nonblocking():
    """Swallow any pending newlines so we don't print a duplicate prompt."""
    try:
        import sys, select
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

from rich.text import Text
from rich.panel import Panel
from rich.table import Table
from rich import box
from .ui import console, panel, input_prompt, banner

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
    ex_tbl.add_row(_bullet_line("'save' — Save THIS week (draft)"))
    ex_tbl.add_row(_bullet_line("'save next week' — Save NEXT week (draft)"))  # <-- added
    ex_tbl.add_row(_bullet_line("'submit' — Submit THIS week for approval"))
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
    cmds = Text("login   save   snw (save next week)   submit   ss (save & submit)   back   quit", style="bold magenta")
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

    # Small note boxes
    console.print(Panel("This uses your browser’s SSO cookies.", border_style="white", box=box.ROUNDED))
    console.print(
        Panel(
            "If a save/submit fails, please open https://app.napta.io once and log in, then retry.",
            border_style="white",
            box=box.ROUNDED,
        )
    )

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

        # NEW: Save NEXT week
        if cmd in ("save next week", "/save next week", "save-next-week", "/save-next-week", "snw", "/snw"):
            ok, msg = client.save_next_week()
            panel(msg)
            continue

        if cmd in ("save", "/save"):
            ok, msg = client.save_current_week()
            panel(msg)
            continue

        if cmd in ("submit", "/submit"):
            ok, msg = client.submit_current_week()
            panel(msg)
            continue

        if cmd in ("ss", "/ss"):
            ok, msg = client.save_and_submit_current_week()
            panel(msg)
            continue

        if cmd in ("login", "/login"):
            ok, msg = client.login()
            panel(msg)
            continue

        panel("⚠️ Unknown command. Use: login | save | save next week (snw) | submit | ss | back | quit")



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
    from . import fitnet  # local import to avoid circulars if any

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

        comment = remarks.get(start_key, "")
        half_day = None

        ok, msg, shot = fitnet.apply_leave(
            start=start_dt,
            end=end_dt,
            leave_type=leave_type,
            comment=comment,
            half_day=half_day,
            commit=commit,
            screenshot_to=None,
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
    # Fresh parsing session
    eng = Engine(profile)
    eng.reset_session()

    banner(f"{profile.get('name')} <{profile.get('email')}>")

    # If you have the fancy Fitnet headers in ui.py, show them; else show basics
    try:
        from .ui import fitnet_header, fitnet_commands
        fitnet_header(); fitnet_commands()
    except Exception:
        panels([
            "Fitnet (Leave) — safe preview by default.",
            "Commands: /preview, /commit, /show, /clear, /help, /back, /quit",
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

        # exits
        if cmd in ("/quit", "/q", "quit", "q", "/exit", "exit"):
            panel("👋 Bye!")
            sys.exit(0)
        if cmd == "/back":
            panel("↩️  Back to main menu.")
            return

        # help & session mgmt
        if cmd == "/help":
            panels([
                "Fitnet commands:",
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
        choice = menu("Choose an option:", [
            "Napta Timesheet",
            "GovTech Timesheet",
            "Fitnet (Leave)",
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
            profile = ensure_profile()
            fitnet_loop(profile)
        elif choice == "4":
            run_registration_interactive()
        elif choice == "5":
            panel("Goodbye! 👋")
            return 0
        else:
            panel("Please pick 1–5.")


if __name__ == "__main__":
    raise SystemExit(main())
