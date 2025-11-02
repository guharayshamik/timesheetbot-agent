# timesheetbot_agent/cli.py
from __future__ import annotations
from .config_loader import load_config


import sys
from typing import Optional

# UI
from .ui import (
    console,
    banner,
    menu,
    input_prompt,
    panel,
    panels,
    show_vibrant_help,
)

# Napta
from .napta import NaptaClient

# Engine / storage / registration
from .engine import Engine
from .storage import (
    load_profile,
    load_session,
    clear_session,
    clear_profile,
)
from .registration import run_registration_interactive

# Pretty blocks
from rich.text import Text
from rich.panel import Panel
from rich import box
from rich.table import Table  # used in Napta help block

from pathlib import Path

# Matches napta.py’s screenshot directory:
_SHOT_DIR = (Path.home() / ".tsbot" / "napta" / "shots").resolve()

# def _maybe_add_shot_hint(text: str) -> str:
#     # Only append when a screenshot was mentioned
#     if "Screenshot ->" in (text or ""):
#         return f"{text}\n📁 All screenshots are saved in: {_SHOT_DIR}"
#     return text

def _maybe_add_shot_hint(text: str) -> str:
    # Add help for timeouts
    if text.startswith("⏰"):
        return f"{text}\n💡 Tip: Check your internet connection or re-login with `login` if the issue persists."

    # Add screenshot path info
    if "Screenshot ->" in (text or ""):
        return f"{text}\n📁 All screenshots are saved in: {_SHOT_DIR}"

    return text


# ------------------------------ profile helpers ------------------------------

def ensure_profile() -> dict:
    prof = load_profile()
    if not prof:
        panel("⚠️ No registration found. Let's get you set up.")
        prof = run_registration_interactive()
    return prof


# ------------------------------ generic helpers ------------------------------

# def show_help() -> None:
#     panels(["Type your timesheet details or use the commands shown in the help blocks."])
def show_help() -> None:
    show_govtech_help_detailed()

def show_session_box() -> None:
    sess = load_session()
    month = sess.get("month", "—")
    leaves = sess.get("leave_details", [])
    lines = [f"Month: {month}", f"Leave details: {leaves}"]
    panels(lines)

def _drain_stdin_nonblocking() -> None:
    """Swallow any pending newlines so we don't print a duplicate prompt."""
    try:
        import select
        while select.select([sys.stdin], [], [], 0)[0]:
            sys.stdin.readline()
    except Exception:
        pass  # best effort

def _normalize_engine_cmd(cmd: str) -> str:
    """
    Make engine commands work without a leading slash.
    Maps: email, comment, generate  -> /email, /comment, /generate
    Leaves other text unchanged.
    """
    if not cmd:
        return cmd
    if cmd.startswith("/"):
        return cmd

    keywords = ("email", "comment", "generate")
    for kw in keywords:
        if cmd == kw or cmd.startswith(f"{kw} "):
            return f"/{cmd}"
    return cmd

def _normalize_command(raw: str) -> str:
    """
    Normalize user input using config:
    - Map aliases to canonical commands (help/clear/back/quit/email/comment/generate/show)
    - Auto-prefix engine commands with '/' when user omits it (email/comment/generate)
    """
    cfg = load_config()
    s = (raw or "").strip()
    if not s:
        return s

    # Split first token to check command-like words
    parts = s.split(maxsplit=1)
    head = parts[0].lower()
    tail = parts[1] if len(parts) > 1 else ""

    # 1) Alias map (help, clear, back, quit, email, comment, generate, show)
    aliases = getattr(cfg.cli, "command_aliases", {}) or {}
    canonical = None
    for canon, alist in aliases.items():
        if head == canon or head in (a.lower() for a in alist):
            canonical = canon
            break

    if canonical:
        # Reconstruct with canonical head
        s = f"{canonical} {tail}".strip()
        head = canonical

    # 2) Auto-prefix engine commands with '/'
    engine_cmds = set(cfg.cli.engine_keywords or [])
    if head in engine_cmds and not s.startswith("/"):
        s = "/" + s  # e.g., 'email bob@x' -> '/email bob@x'

    return s


# ------------------------------ GovTech flow ---------------------------------

def govtech_loop(profile: dict) -> None:
    eng = Engine(profile)
    eng.reset_session()  # start fresh so old leaves aren’t carried over

    banner(f"{profile.get('name')} <{profile.get('email')}>")
    #show_vibrant_help()
    _show_govtech_examples_compact()
    print()  # one blank line before the prompt

    while True:
        try:
            s = input_prompt("govtech_timesheet›")
        except (EOFError, KeyboardInterrupt):
            panel("👋 Bye!")
            return

        if not s:
            continue

       # cmd = s.strip()
        cmd = _normalize_command(s.strip())

        # Core commands
        if cmd in ("/quit", "/q", "quit", "q", "/exit", "exit"):
            panel("👋 Bye!")
            sys.exit(0)

        if cmd in ("/back", "back"):
            panel("↩️  Back to main menu.")
            return

        if cmd in ("/help", "help"):
            show_help(); continue

        if cmd in ("/show", "show"):
            show_session_box(); continue

        if cmd in ("/clear", "clear"):
            clear_session(); panel("🧹 Session cleared."); continue

        if cmd in ("/deregister", "deregister"):
            clear_profile(); clear_session()
            panel("👋 Deregistered and session cleared. Returning to main menu.")
            return

        #cmd = _normalize_engine_cmd(cmd)
        #cmd = _normalize_command(cmd)
        # Hand over to engine (includes /generate handling and /comment)
        out_lines = eng.handle_text(cmd)
        panels(out_lines)


# ------------------------------ Napta (chat) ---------------------------------

def _bullet_line(s: str, style: str = "bold green") -> Text:
    return Text("• ", style="dim") + Text(s, style=style)

def _show_govtech_examples_compact() -> None:
    """Compact teaser: a few natural-language examples + a tip to type help."""
    ex_tbl = Table.grid(padding=(0, 1))
    ex_tbl.add_column()
    ex_tbl.add_row(_bullet_line('"generate timesheet/gen ts for August/Aug"'))
    ex_tbl.add_row(_bullet_line('"annual leave/al 11–13 Aug"'))
    ex_tbl.add_row(_bullet_line('"sick leave/sl/mc on 11 Aug"'))
    ex_tbl.add_row(_bullet_line('"child care/cc on 12–13 Aug"'))
    ex_tbl.add_row(_bullet_line('"half day on 12–13 Aug"'))
    ex_tbl.add_row(_bullet_line('"ns leave on 12–13 Aug"'))
    #ex_tbl.add_row(_bullet_line('"generate" — Create a new timesheet'))
    ex_tbl.add_row(_bullet_line('"email/eml" — Email generated timesheet to your registered manager'))
    ex_tbl.add_row(_bullet_line('"help/h/hlp" — Show available commands'))
    console.print(
        Panel(
            ex_tbl,
            title="NLP/Commands",
            title_align="left",
            border_style="cyan",
            box=box.ROUNDED,
            padding=(0, 1),
        )
    )
    console.print(
    Panel(
        "Type 'help' to see the full list of commands you can use for GovTech timesheets.",
        title="Tip",
        title_align="left",
        border_style="magenta",
        box=box.ROUNDED,
        padding=(0, 1),
    )
)


def show_govtech_help_detailed() -> None:
    """Full help: natural-language commands + other command list (unchanged style)."""
    ex_tbl = Table.grid(padding=(0, 1))
    ex_tbl.add_column()

    # Natural language / short-form intents
    ex_tbl.add_row(_bullet_line('"generate timesheet for August"'))
    ex_tbl.add_row(_bullet_line('"gen ts Oct"'))
    ex_tbl.add_row(_bullet_line('"annual leave 11–13 Aug"'))
    ex_tbl.add_row(_bullet_line('"al on 11–13 Aug"'))
    ex_tbl.add_row(_bullet_line('"al on 11 Aug"'))
    ex_tbl.add_row(_bullet_line('"sick leave on 11 Aug"'))
    ex_tbl.add_row(_bullet_line('"sl on 11 Aug"'))
    ex_tbl.add_row(_bullet_line('"mc on 11 Aug"'))
    ex_tbl.add_row(_bullet_line('"national service on 11th aug"'))
    ex_tbl.add_row(_bullet_line('"ns on 25 Sept"'))
    ex_tbl.add_row(_bullet_line('"childcare leave on 12 Aug"'))
    ex_tbl.add_row(_bullet_line('"cc on 12–13 Aug"'))
    ex_tbl.add_row(_bullet_line('"child care on 5 Oct"'))
    ex_tbl.add_row(_bullet_line('"childcare on 21 Sept"'))
    ex_tbl.add_row(_bullet_line('"weekend effort on 29 Sep 4h"'))
    ex_tbl.add_row(_bullet_line('"we 3h on 6 Oct"'))

    # Other commands (unchanged style/format)
    ex_tbl.add_row(_bullet_line('"show" — Display current saved data'))
    ex_tbl.add_row(_bullet_line('"clear/clr" — Clear current entries'))
    ex_tbl.add_row(_bullet_line('"deregister" — Remove your profile from bot'))
    ex_tbl.add_row(_bullet_line('"generate/gen ts" — Create a new timesheet'))
    ex_tbl.add_row(_bullet_line('"comment/remarks" — Add remarks to a specific date; This will add comments in the "Remarks" column inside excel'))
    ex_tbl.add_row(_bullet_line('"email/eml" — Email generated timesheet to your registered manager'))
    ex_tbl.add_row(_bullet_line('"help/h/hlp" — Show available commands'))
    ex_tbl.add_row(_bullet_line('"back" — Return to previous menu'))
    ex_tbl.add_row(_bullet_line('"quit/q" — Exit the tool'))

    console.print(
        Panel(
            ex_tbl,
            title="NLP/Commands",
            title_align="left",
            border_style="cyan",
            box=box.ROUNDED,
            padding=(0, 1),
        )
    )


def _show_napta_simple_help_block() -> None:
    """Pretty 'NAPTA Chat mode ON + examples + commands' block (edit features removed)."""
    # Chip header
    chip = Text.assemble(("⚡  NAPTA Chat mode", "bold"), ("  ON", "bold bright_green"))
    console.print(Panel(chip, border_style="bright_green", padding=(0, 1), box=box.SQUARE))

    # Subtitle
    console.print(Text("Describe your Napta action in plain English, e.g.:", style="bold cyan"))

    # Examples (only view/save/submit flows)
    ex_tbl = Table.grid(padding=(0, 1))
    ex_tbl.add_column()
    ex_tbl.add_row(_bullet_line("'login' — Sign in once (SSO) using cli and save the session"))
    ex_tbl.add_row(_bullet_line("'view' — Show CURRENT week entries"))
    ex_tbl.add_row(_bullet_line("'view next week' (or 'vnw') — Show NEXT week entries"))
    ex_tbl.add_row(_bullet_line("'save' — Save CURRENT week (draft)"))
    ex_tbl.add_row(_bullet_line("'submit' — Submit CURRENT week for approval"))
    ex_tbl.add_row(_bullet_line("'save next week' (or 'snw') — Save NEXT week (draft)"))
    ex_tbl.add_row(_bullet_line("'submit next week' (or 'sbnw') — Submit NEXT week for approval"))
    ex_tbl.add_row(_bullet_line("'ss' — Save then Submit (CURRENT week)"))
    console.print(
        Panel(ex_tbl, title="Examples", title_align="left", border_style="cyan", box=box.ROUNDED, padding=(0, 1))
    )

    # Commands — one per line
    cmds = Text(
        "\n".join([
            "login",
            "view",
            "vnw (view next week)",
            "save",
            "snw (save next week)",
            "submit",
            "sbnw (submit next week)",
            "ss (save & submit this week)",
            "back",
            "quit",
        ]),
        style="bold magenta",
    )
    console.print(
        Panel(cmds, title="Commands", title_align="left", border_style="magenta", box=box.ROUNDED, padding=(0, 1))
    )


def napta_loop(profile: dict) -> None:
    banner(f"{profile.get('name')} <{profile.get('email')}>")

    client = NaptaClient()
    panel(f"Napta auth status: {client.status()}")

    # Napta chat mode UI (chip + examples + commands)
    _show_napta_simple_help_block()

    # Notes
    console.print(Panel(
        "Tool uses your saved session or browser SSO cookies. ‘login’ will open a browser window to sign in once (SSO), to let the bot save your session.\n"
        "Just ‘login’ once.",
        border_style="white", box=box.ROUNDED,
    ))
    # console.print(Panel(
    #     "If headless login/save/submit fails, open https://app.napta.io once and sign in, then retry.",
    #     border_style="white", box=box.ROUNDED,
    # ))
    # console.print(Panel(
    #     "Performance tip: Using a VPN can slow down Napta actions (page loads, navigation, submit) due to latency. "
    #     "For the fastest results, run this tool **without VPN** when possible, then reconnect after you’re done.",
    #     border_style="yellow",
    #     box=box.ROUNDED,
    # ))
    console.print(Panel(Text(
    "🚀 Performance Tip: Using a VPN can slow down Napta actions (page loads, navigation, submit). "
    "For best speed, run this tool WITHOUT VPN, then reconnect when done.",
    style="bold bright_red",
    )))

    while True:
        try:
            _drain_stdin_nonblocking()
            raw = input_prompt("napta›")
        except (EOFError, KeyboardInterrupt):
            panel("👋 Bye!")
            return

        if not raw:
            continue

        cmd = raw.strip().lower()

        # Generic exits
        if cmd in ("/quit", "/q", "quit", "q", "/exit", "exit"):
            panel("👋 Bye!")
            sys.exit(0)

        if cmd in ("back", "/back"):
            panel("↩️  Back to main menu.")
            return

        # ---------- Allowed simple commands only ----------
        if cmd in ("login", "/login"):
            ok, msg = client.login(); panel(_maybe_add_shot_hint(msg)); continue

        if cmd in ("view", "/view", "show", "/show"):
            ok, msg = client.view_week("current"); panel(_maybe_add_shot_hint(msg)); continue

        if cmd in ("view next week", "/view next week", "view-next-week", "/view-next-week", "vnw", "/vnw"):
            ok, msg = client.view_week("next"); panel(_maybe_add_shot_hint(msg)); continue

        if cmd in ("save", "/save"):
            ok, msg = client.save_current_week(); panel(_maybe_add_shot_hint(msg)); continue

        if cmd in ("save next week", "/save next week", "save-next-week", "/save-next-week", "snw", "/snw"):
            ok, msg = client.save_next_week(); panel(_maybe_add_shot_hint(msg)); continue

        if cmd in ("submit", "/submit"):
            ok, msg = client.submit_current_week(); panel(_maybe_add_shot_hint(msg)); continue

        if cmd in ("submit next week", "/submit next week", "submit-next-week", "/submit-next-week", "sbnw", "/sbnw"):
            ok, msg = client.submit_next_week(); panel(_maybe_add_shot_hint(msg)); continue

        if cmd in ("ss", "/ss"):
            ok, msg = client.save_and_submit_current_week(); panel(_maybe_add_shot_hint(msg)); continue

        # Fallback
        panel(
            "⚠️ Unknown command. Use one of:\n"
            "login\nview\nvnw (view next week)\nsave\nsnw (save next week)\n"
            "submit\nsbnw (submit next week)\nss (save & submit this week)\nback\nquit"
        )


# ------------------------------ main menu ------------------------------------

def main(argv: Optional[list] = None) -> int:
    banner("CLI Tool")
    while True:
        choice = menu("Choose an option:", [
        "GovTech Timesheet",
        "Registration (GovTech entries)",
        "Napta Timesheet",
        "Quit",
    ])

        if choice == "1":
            profile = ensure_profile()
            govtech_loop(profile)                 # GovTech first
        elif choice == "2":
            run_registration_interactive()        # Registration second
        elif choice == "3":
            profile = ensure_profile()
            napta_loop(profile)                   # Napta third
        elif choice == "4":
            panel("Goodbye! 👋")
            return 0
        else:
            panel("Please pick 1–4.")

if __name__ == "__main__":
    raise SystemExit(main())
