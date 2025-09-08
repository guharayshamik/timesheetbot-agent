# timesheetbot_agent/cli.py (top of file)
from __future__ import annotations
from .ui import console
import sys
from typing import Optional, List
from datetime import datetime

from .napta import NaptaClient
from .ui import banner, panel, panels, input_prompt, show_vibrant_help 
from . import fitnet

# Pretty UI helpers (safe optional imports)
try:
    from .ui import fitnet_header, fitnet_commands
except Exception:
    fitnet_header = None
    fitnet_commands = None

from .storage import (
    load_profile,
    save_profile,
    load_session,
    clear_session,
    clear_profile,
)
from .registration import run_registration_interactive
from .engine import Engine

from .ui import (
    banner,
    menu,
    input_prompt,
    panel,
    panels,
    note,
    show_vibrant_help,
)

from .napta import NaptaClient
from .ui import banner, panel, panels, input_prompt, console
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
def _bullet_line(s: str, style: str = "bold green") -> Text:
    return Text("• ", style="dim") + Text(s, style=style)

def _show_napta_help_block() -> None:
    chip = Text.assemble(("⚡  NAPTA chat mode", "bold"), ("  ON", "bold bright_green"))
    console.print(Panel(chip, border_style="bright_green", padding=(0, 1), box=box.SQUARE))
    console.print(Text("Describe your Napta action in plain English, e.g.:", style="bold cyan"))

    ex_tbl = Table.grid(padding=(0, 1))
    ex_tbl.add_column()
    ex_tbl.add_row(_bullet_line("'all day worked' — keep current grid, just save"))
    ex_tbl.add_row(_bullet_line("'all day 1d' — (planned) fill first row Mon–Fri with 1d each, then save"))
    ex_tbl.add_row(_bullet_line("'/save' — Save this week (draft)"))
    ex_tbl.add_row(_bullet_line("'/submit' — Submit this week for approval"))
    ex_tbl.add_row(_bullet_line("'/submit month' — choose All weeks / 1st / 2nd / 3rd / 4th (5th if any)"))
    console.print(Panel(ex_tbl, title="Examples", title_align="left",
                        border_style="cyan", box=box.ROUNDED, padding=(0, 1)))
    cmds = Text("/save   /submit   /save month   /submit month   /back   /quit", style="bold magenta")
    console.print(Panel(cmds, title="Commands", title_align="left",
                        border_style="magenta", box=box.ROUNDED, padding=(0, 1)))

def _ask_week_choice() -> str:
    """Return 'all' or '1'..'5'."""
    console.print(Panel("Choose weeks for the **current month**:", border_style="cyan", box=box.ROUNDED))
    while True:
        ans = input_prompt("napta› weeks? (all / 1 / 2 / 3 / 4 / 5)")
        t = ans.strip().lower()
        if t in ("all", "1", "2", "3", "4", "5"):
            return t
        panel("⚠️ Please type: all / 1 / 2 / 3 / 4 / 5")

def napta_loop(profile: dict) -> None:
    banner(f"{profile.get('name')} <{profile.get('email')}>")
    client = NaptaClient()
    panels([f"Napta auth status: {client.status()}"])
    _show_napta_help_block()
    panels([
        "This uses your browser’s SSO cookies.",
        "If a save/submit fails, please open https://app.napta.io in your browser and login once, then retry.",
    ])

    # Optional one-time mode line (we keep the flag for future, but no cell edits yet)
    _ = input_prompt("napta› enter 'all day worked' or 'all day 1d' (optional, Enter to skip)").strip().lower()

    while True:
        cmd = input_prompt("napta› (/save, /submit, /save month, /submit month, /back, /quit)").strip().lower()
        if not cmd:
            continue
        if cmd in ("/quit", "/q"):
            panel("👋 Bye!"); sys.exit(0)
        if cmd == "/back":
            panel("↩️  Back to main menu."); return

        if cmd == "/save":
            ok, msg = client.save_current_week(all_day_1d=False)
            panel(msg); continue

        if cmd == "/submit":
            ok, msg = client.submit_current_week()
            panel(msg); continue

        if cmd == "/save month":
            wk = _ask_week_choice()  # 'all' / '1'..'5'
            ok, msg = client.save_month_choice("all" if wk == "all" else int(wk))
            panel(msg); continue

        if cmd == "/submit month":
            wk = _ask_week_choice()
            # NOTE: “All weeks” path is fully automatic — no extra inputs.
            ok, msg = client.submit_month_choice("all" if wk == "all" else int(wk))
            panel(msg); continue

        panel("⚠️ Unknown command. Try: /save, /submit, /save month, /submit month, /back, /quit")


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
    if fitnet_header:
        try:
            fitnet_header()
            if fitnet_commands:
                fitnet_commands()
        except Exception:
            pass
    else:
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
        # /login flow for non-technical users (device flow)
        # /login (device flow — SSO, no secrets)
        if cmd == "/login":
            try:
                ok, msg = client.device_login()
                panel(msg)
                if ok:
                    try:
                        me = client.whoami()
                        panels([f"✅ User OK: {me.get('data', {}).get('id', 'unknown')}"])
                    except Exception as e:
                        panels([f"⚠️ Logged in but /user failed: {e}"])
            except NaptaAuthError as e:
                panels([f"❌ {e}", "Tip: /login set-client <PUBLIC_CLIENT_ID>"])
            continue

        # optional: set the public client id once (if not shipped via env)
        if cmd.startswith("/login set-client"):
            parts = cmd.split()
            if len(parts) == 3:
                _, _, cid = parts
                try:
                    client.configure_device_client(cid)
                    panels(["✅ Saved public client id. Now run /login."])
                except NaptaAuthError as e:
                    panels([f"❌ {e}"])
            else:
                panels(["⚠️ Usage: /login set-client <PUBLIC_CLIENT_ID>"])
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
            fitnet_loop(profile)   # if you have this; otherwise remove this option
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
