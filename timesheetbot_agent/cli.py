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

        # Login (headless email-based; no browser pop-up)
        # if cmd in ("login", "/login"):
        #     email = (profile or {}).get("email", "").strip()
        #     if not email:
        #         email = input_prompt("napta_login_email›").strip()
        #     if not email:
        #         panel("⚠️ Email is required for headless login. Try again with 'login' and enter your Napta email.")
        #         continue
        #     ok, msg = client.login(email=email)
        #     panel(msg)
        #     continue

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


# ------------------------------ Fitnet UI helpers (new look) ------------------

def _fitnet_bullet(s: str, style: str = "white") -> Text:
    return Text("• ", style="dim") + Text(s, style=style)

def fitnet_examples_block() -> None:
    tbl = Table.grid(padding=(0, 1))
    tbl.add_column()
    tbl.add_row(_fitnet_bullet('"mc on 11 Sep"', "bright_white"))
    tbl.add_row(_fitnet_bullet('"annual leave 1–3 Aug"', "bright_white"))
    tbl.add_row(_fitnet_bullet('"/comment 11 Sep OIL"', "bright_white"))
    console.print(
        Panel(
            tbl,
            title="Examples",
            title_align="left",
            border_style="cyan",
            box=box.ROUNDED,
            padding=(0, 1),
        )
    )

def fitnet_commands_block() -> None:
    # slash commands (compact, single line like the old UI)
    slash = Text("/login   /preview   /commit   /show   /clear   /help   /back   /quit",
                 style="bold magenta")
    console.print(
        Panel(
            slash,
            title="Commands",
            title_align="left",
            border_style="magenta",
            box=box.ROUNDED,
            padding=(0, 1),
        )
    )

    # natural commands (one panel with bullets, not many boxes)
    nat = Table.grid(padding=(0, 1))
    nat.add_column()
    nat.add_row(_fitnet_bullet("login"))
    nat.add_row(_fitnet_bullet("preview"))
    nat.add_row(_fitnet_bullet('commit   (same as “add leave to fitnet”)'))
    nat.add_row(_fitnet_bullet("add leave to fitnet"))
    console.print(
        Panel(
            nat,
            title="You can also use natural commands (no slashes):",
            title_align="left",
            border_style="white",
            box=box.ROUNDED,
            padding=(0, 1),
        )
    )


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
        lines.append("👀 Preview mode: forms were filled in Fitnet but NOT saved. Review the browser if opened and click Save manually if all looks good.")
        lines.append("Tip: run `commit` (or `add leave to fitnet`) to save automatically next time.")
    else:
        lines.append("📌 Done. Entries should now appear in Fitnet. (Napta will pick it up per your usual nightly sync.)")

    return lines


def _fitnet_help_panels() -> None:
    """Old look & feel, but with the refined examples/commands panels."""
    # Header line like before
    console.print(Text("Type your leave in plain English, then preview or commit to Fitnet.", style="bold cyan"))
    # Examples + Commands blocks (new)
    fitnet_examples_block()
    fitnet_commands_block()


def fitnet_loop(profile: dict) -> None:
    # Fresh parsing session
    eng = Engine(profile)
    eng.reset_session()

    banner(f"{profile.get('name')} <{profile.get('email')}>")
    _fitnet_help_panels()
    print()

    from . import fitnet  # local import

    # normalize helpers for natural commands
    NAT_ALIASES = {
        "login": {"/login", "login"},
        "preview": {"/preview", "preview"},
        "commit": {"/commit", "commit", "add leave to fitnet", "add leave", "save leaves", "apply leaves"},
        "show": {"/show", "show"},
        "clear": {"/clear", "clear"},
        "help": {"/help", "help", "h", "?"},
        "back": {"/back", "back"},
        "quit": {"/quit", "/q", "quit", "q", "/exit", "exit"},
    }

    def _is(cmd: str, key: str) -> bool:
        cmd_norm = " ".join(cmd.strip().lower().split())
        return cmd_norm in NAT_ALIASES[key]

    while True:
        try:
            s = input_prompt("fitnet›")
        except (EOFError, KeyboardInterrupt):
            panel("👋 Bye!")
            return

        if not s:
            continue

        cmd = s.strip()

        # exits
        if _is(cmd, "quit"):
            panel("👋 Bye!")
            sys.exit(0)
        if _is(cmd, "back"):
            panel("↩️  Back to main menu.")
            return

        # help & session mgmt
        if _is(cmd, "help"):
            _fitnet_help_panels()
            continue

        if _is(cmd, "show"):
            show_session_box()
            continue

        if _is(cmd, "clear"):
            clear_session()
            panel("🧹 Session cleared.")
            continue

        # login (store creds)
        if _is(cmd, "login"):
            ok, msg = fitnet.login()
            panel(msg)
            continue

        # Preview / Commit actions
        if _is(cmd, "preview"):
            panels(_run_fitnet_applies(commit=False))
            continue

        if _is(cmd, "commit"):
            panels(_run_fitnet_applies(commit=True))
            continue

        # Otherwise treat as natural-language leave text (reuse Engine parser)
        out_lines = eng.handle_text(cmd)
        panels(out_lines)


# ------------------------------ main menu ------------------------------------

def main(argv: Optional[list] = None) -> int:
    #banner("Timesheet BOT agent — PALO IT123")
    banner("CLI Tool")

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
