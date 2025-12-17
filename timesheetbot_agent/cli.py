# timesheetbot_agent/cli.py
from __future__ import annotations
from .config_loader import load_config
from .errors import catch_all
from .ui import banner, input_prompt, panel, suppress_ctrlc_echo, UserCancelled
from .napta import NaptaClient
from rich.table import Table
from rich.text import Text
from rich.panel import Panel
from rich.box import ROUNDED
import json
import subprocess
import textwrap

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
    interrupt_policy,    
    UserCancelled,    
)

from .storage import (
    clear_session,
    clear_profile,
    clear_napta,
    clear_generated,
    clear_all,
    clear_govtech_only, 
)

# Napta
from .napta import NaptaClient

# Engine / storage / registration
from .engine import Engine, _split
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

import os

def _configure_playwright_for_frozen_app() -> None:
    # Only apply for PyInstaller/frozen builds
    if not getattr(sys, "frozen", False):
        return

    base_dir = Path(sys.executable).resolve().parent
    browsers_dir = base_dir / "_internal" / "ms-playwright"

    # Tell Playwright where the bundled browsers are
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(browsers_dir)

    # Avoid Playwright trying to download browsers on user machines
    os.environ.setdefault("PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD", "1")

_configure_playwright_for_frozen_app()


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

# def show_session_box() -> None:
#     sess = load_session()
#     month = sess.get("month", "—")
#     leaves = sess.get("leave_details", [])
#     lines = [f"Month: {month}", f"Leave details: {leaves}"]
#     panels(lines)
def show_session_box():
    from collections import defaultdict
    sess = load_session() or {}
    details = sess.get("leave_details", [])

    # Group by month from the tuple's start date (format: "DD-MonthFullName")
    grouped = defaultdict(list)
    for tup in details:
        if not isinstance(tup, (list, tuple)) or len(tup) < 3:
            continue
        start, end, ltype = tup[0], tup[1], tup[2]
        try:
            _d, mon = _split(start)  # returns (day_int, MonthFullName)
        except Exception:
            mon = "Unknown"
        grouped[mon].append((start, end, ltype))

    # Render helpers
    def _short_mon(full: str) -> str:
        return full[:3].title() if full else "—"

    def _show_range(s: str, e: str) -> str:
        ds, ms = _split(s)
        de, me = _split(e)
        if s == e:
            return f"{ds} {_short_mon(ms)}"
        return f"{ds}–{de} {_short_mon(ms)}"

    # Preserve month order as first-seen in 'details'
    seen_order = []
    for item in details:
        if not isinstance(item, (list, tuple)) or len(item) < 1:
            continue
        s = item[0]
        try:
            _, m = _split(s)
        except Exception:
            m = "Unknown"
        if m not in seen_order:
            seen_order.append(m)

    lines = []
    for mon in seen_order:
        if mon not in grouped:
            continue
        lines.append(f"[bold]Month: {mon}[/bold]")
        lines.append("")
        for (s, e, t) in grouped[mon]:
            lines.append(f"{t} — {_show_range(s, e)}")
        lines.append("")

    body = "\n".join(lines).rstrip() if lines else "No leaves recorded yet."
    console.print(Panel(body, title="Leave details"))

def _drain_stdin_nonblocking() -> None:
    """Swallow any pending newlines so we don't print a duplicate prompt."""
    try:
        import select
        while select.select([sys.stdin], [], [], 0)[0]:
            sys.stdin.readline()
    except Exception:
        pass  # best effort



# def _run_napta_action(action: str):
#     with suppress_ctrlc_echo():
#         try:
#             ok, msg = _napta_run(action)
#         except (KeyboardInterrupt, EOFError):
#             return False, "↩️ Cancelled."
#     return ok, msg

# def _napta_run(action: str) -> tuple[bool, str]:
#     """
#     Run a Napta action in a clean subprocess (isolated from any running asyncio loop),
#     and return (ok, msg).

#     Supported actions:
#       - "login"
#       - "view_current", "view_next"
#       - "save_current", "save_next"
#       - "submit_current", "submit_next"
#       - "save_and_submit_current"
#     """
#     import sys, json, textwrap, subprocess

#     helper = textwrap.dedent("""
#         import json, sys, signal
#         from timesheetbot_agent.napta import NaptaClient

#         def main():
#             action = sys.argv[1] if len(sys.argv) > 1 else ""
#             c = NaptaClient()
#             try:
#                 if action == "login":
#                     ok, msg = c.login()
#                 elif action == "view_current":
#                     ok, msg = c.view_week("current")
#                 elif action == "view_next":
#                     ok, msg = c.view_week("next")
#                 elif action == "save_current":
#                     ok, msg = c.save_current_week()
#                 elif action == "save_next":
#                     ok, msg = c.save_next_week()
#                 elif action == "submit_current":
#                     ok, msg = c.submit_current_week()
#                 elif action == "submit_next":
#                     ok, msg = c.submit_next_week()
#                 elif action == "save_and_submit_current":
#                     ok, msg = c.save_and_submit_current_week()
#                 else:
#                     ok, msg = False, f"Unknown napta action: {action}"
#             except KeyboardInterrupt:
#                 ok, msg = False, "↩️ Cancelled."
#             except EOFError:
#                 ok, msg = False, "↩️ Cancelled."
#             except Exception as e:
#                 ok, msg = False, f"⚠️ Unexpected Napta error: {e}"
#             finally:
#                 try:
#                     c.close()
#                 except Exception:
#                     pass
#             print(json.dumps({"ok": ok, "msg": msg}))

#         if __name__ == "__main__":
#             main()
#     """).strip()

#     proc = subprocess.run(
#         [sys.executable, "-c", helper, action],
#         capture_output=True, text=True
#     )
#     if proc.returncode != 0:
#         return False, (proc.stdout.strip() or proc.stderr.strip() or "Napta helper failed")

#     try:
#         data = json.loads(proc.stdout.strip() or "{}")
#         return bool(data.get("ok")), str(data.get("msg", ""))
#     except Exception as e:
#         return False, f"Napta helper parse error: {e}\nSTDOUT: {proc.stdout}\nSTDERR: {proc.stderr}"

def _run_napta_action(action: str, *, timeout_sec: int = 180) -> tuple[bool, str]:
    """
    Run a Napta action.

    - In frozen (PyInstaller) mode: run inline (no subprocess) to avoid recursion
      where sys.executable == the tsbot binary.
    - In dev/venv mode: use a subprocess helper to keep things isolated.
    """
    # 1) FROZEN: run inline (no subprocess)
    if getattr(sys, "frozen", False):
        with suppress_ctrlc_echo():
            try:
                c = NaptaClient()
                try:
                    if action == "login":
                        ok, msg = c.login()
                    elif action == "view_current":
                        ok, msg = c.view_week("current")
                    elif action == "view_next":
                        ok, msg = c.view_week("next")
                    elif action == "view_previous":              
                        ok, msg = c.view_week("previous")
                    elif action == "save_current":
                        ok, msg = c.save_current_week()
                    elif action == "save_next":
                        ok, msg = c.save_next_week()
                    elif action == "submit_current":
                        ok, msg = c.submit_current_week()
                    elif action == "submit_next":
                        ok, msg = c.submit_next_week()
                    elif action == "save_and_submit_current":
                        ok, msg = c.save_and_submit_current_week()
                    else:
                        ok, msg = False, f"Unknown napta action: {action}"
                finally:
                    try:
                        c.close()
                    except Exception:
                        pass
            except (KeyboardInterrupt, EOFError):
                ok, msg = False, "↩️ Cancelled."
            except Exception as e:
                ok, msg = False, f"⚠️ Unexpected Napta error: {e}"
        return ok, msg

    # 2) DEV / VENV: keep the subprocess isolation
    helper = textwrap.dedent("""
        import json, sys
        from timesheetbot_agent.napta import NaptaClient

        def main():
            action = sys.argv[1] if len(sys.argv) > 1 else ""
            c = NaptaClient()
            try:
                if action == "login":
                    ok, msg = c.login()
                elif action == "view_current":
                    ok, msg = c.view_week("current")
                elif action == "view_next":
                    ok, msg = c.view_week("next")
                elif action == "view_previous":
                    ok, msg = c.view_week("previous")
                elif action == "save_current":
                    ok, msg = c.save_current_week()
                elif action == "save_next":
                    ok, msg = c.save_next_week()
                elif action == "submit_current":
                    ok, msg = c.submit_current_week()
                elif action == "submit_next":
                    ok, msg = c.submit_next_week()
                elif action == "save_and_submit_current":
                    ok, msg = c.save_and_submit_current_week()
                else:
                    ok, msg = False, f"Unknown napta action: {action}"
            except KeyboardInterrupt:
                ok, msg = False, "↩️ Cancelled."
            except EOFError:
                ok, msg = False, "↩️ Cancelled."
            except Exception as e:
                ok, msg = False, f"⚠️ Unexpected Napta error: {e}"
            finally:
                try:
                    c.close()
                except Exception:
                    pass
            print(json.dumps({"ok": ok, "msg": msg}))

        if __name__ == "__main__":
            main()
    """).strip()

    with suppress_ctrlc_echo():
        try:
            proc = subprocess.run(
                [sys.executable, "-c", helper, action],
                capture_output=True,
                text=True,
                timeout=timeout_sec,
            )
        except subprocess.TimeoutExpired:
            return False, (
                "⚠️ Napta action took too long and was aborted.\n"
                "• Check VPN/network speed.\n"
                "• If this keeps happening, run `tsbot start` (venv version) to see detailed errors."
            )

    if proc.returncode != 0:
        stdout = (proc.stdout or "").strip()
        stderr = (proc.stderr or "").strip()
        msg = stdout or stderr or "Napta helper failed"
        return False, msg

    try:
        data = json.loads(proc.stdout.strip() or "{}")
        return bool(data.get("ok")), str(data.get("msg", ""))
    except Exception as e:
        return False, f"Napta helper parse error: {e}\nSTDOUT: {proc.stdout}\nSTDERR: {proc.stderr}"

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

# ------------------------------ Forget / Reset helper ---------------------------------

def _confirm(prompt: str) -> bool:
    """Ask the user to type yes/no and return True only on yes."""
    ans = input_prompt(f"{prompt} (yes/no)").strip().lower()
    return ans in ("y", "yes")

def handle_forget_command(text: str, *, flow: str | None = None, napta_client=None):
    """
    Intercepts 'forget …' or 'reset …' commands and performs local cleanup.
    Returns list[str] messages if handled, or None if not a forget/reset command.

    flow: 'govtech' | 'napta' | None  (controls which commands are allowed)
    """
    t = (text or "").strip().lower()
    if not (t.startswith("forget") or t.startswith("reset") or t in ("factory reset", "reset everything")):
        return None

    # normalize to simpler keys
    key = t.replace("really ", "").strip()

    # ---------- Hard blocks / flow scoping ----------
    if flow == "govtech":
        if key in ("reset napta", "forget napta"):
            return [
                "⛔ That command is only available in Napta flow.",
                "If you want to clear GovTech data (profile, session, registration details, settings), type: `reset my data`.",
                "For a full wipe including generated files, type: `factory reset`.",
            ]
        if key == "reset":
            return [
                "❗ Ambiguous command.",
                "To clear GovTech data like profile, session, and settings, type: `reset all my data` or `reset my data`.",
            ]

    if flow == "napta":
        if key in ("reset my data", "reset all my data", "forget profile", "reset profile", "forget session", "reset session"):
            return [
                "⛔ GovTech data resets aren’t available inside Napta flow.",
                "To clear Napta session/cache only, type: `reset napta`.",
                "For a full wipe including generated files, type: `factory reset`.",
            ]
        if key == "reset":
            return [
                "❗ Ambiguous command.",
                "If you want to clear Napta data (saved session, cookies, screenshots), type: `reset napta`.",
            ]

    # Session-only (GovTech working memory)
    if key in ("forget", "forget session", "reset timesheet data"):
        if flow == "napta":
            return [
                "⛔ GovTech session reset isn’t available in Napta flow.",
                "Use `reset napta` to clear only Napta data here.",
            ]
        if _confirm("This will clear your GovTech in-progress session"):
            clear_session()
            return ["🧹 Cleared session data."]
        return ["❌ Cancelled. ✅ No changes made."]

    # Registration profile
    if key in ("forget profile", "deregister", "reset profile"):
        if flow == "napta":
            return [
                "⛔ GovTech profile reset isn’t available in Napta flow.",
                "Use `reset napta` for Napta data, or run this from GovTech flow.",
            ]
        if _confirm("This will remove your GovTech registration/profile"):
            clear_profile()
            return ["🧹 Cleared registration/profile info."]
        return ["❌ Cancelled. ✅ No changes made."]

    # Napta cache/state
    if key in ("forget napta", "reset napta"):
        if flow == "govtech":
            return [
                "⛔ Napta reset is only available in Napta flow.",
                "Switch to Napta and type: `reset napta`.",
            ]
        if _confirm("This will remove Napta session/cache (storage state, cookies, screenshots)"):
            clear_napta()
            # important: tear down the live browser/context so next command reloads fresh state
            try:
                if napta_client is not None and hasattr(napta_client, "close"):
                    napta_client.close()
            except Exception:
                pass
            return ["🧹 Cleared Napta browser/session data."]
        return ["❌ Cancelled. No changes made."]


    # Generated files only
    if key in ("forget generated", "reset generated", "reset timesheet files"):
        if _confirm("This will delete ALL generated timesheet files"):
            clear_generated()
            return ["🧹 Removed all generated timesheet files."]
        return ["❌ Cancelled. ✅ No changes made."]

    # Flow-scoped safe reset
    if key in ("reset my data", "reset all my data", "reset all data", "forget all"):
        if flow == "napta":
            return [
                "⛔ GovTech reset isn’t available in Napta flow.",
                "To clear only Napta data, type: `reset napta`.",
                "For a full wipe, type: `factory reset`.",
            ]
        if _confirm("This will clear your GovTech profile, session, and settings (keeps Napta and generated files)"):
            clear_govtech_only()
            return [
                "🧼 Cleared GovTech data (profile, session, settings).",
                "✅ Napta data and generated timesheets are not cleared.",
            ]
        return ["❌ Cancelled. ✅ No changes made."]

    # Full factory reset (including generated files)
    if key in ("forget really all", "factory reset", "reset everything"):
        if _confirm("This will WIPE ALL data, including generated files"):
            clear_all(preserve_generated=False)
            return ["⚠️ Performed FULL reset — all data, including generated files, deleted."]
        return ["❌ Cancelled. ✅ No changes made."]

    # Flow-specific unknowns
    if flow == "govtech":
        return [
            "ℹ️ Unknown reset/forget command.",
            "GovTech tips: `reset my data` (clears profile/session/settings), `reset profile`, `reset generated`, or `factory reset`.",
        ]
    if flow == "napta":
        return [
            "ℹ️ Unknown reset/forget command.",
            "Napta tips: `reset napta` (clears Napta session/cache), or `factory reset` to wipe EVERYTHING.",
        ]
    return [
        "ℹ️ Unknown reset/forget command.",
        "Try: `forget session`, `forget profile`, `forget napta`, `forget generated`, `reset my data`, or `factory reset`.",
    ]


# ------------------------------ GovTech flow ---------------------------------

@catch_all(flow="GovTech", on_cancel="exit")  # Ctrl-C at prompt exits tool
def govtech_loop(profile: dict) -> None:
    eng = Engine(profile)
    eng.reset_session()  # start fresh so old leaves aren’t carried over

    banner(f"{profile.get('name')} <{profile.get('email')}>")
    _show_govtech_examples_compact()
    print()  # one blank line before the prompt

    while True:
        # NOTE: Do NOT catch UserCancelled here; let the decorator handle it (exit)
        s = input_prompt("govtech_timesheet›")
        if not s:
            continue

        cmd = _normalize_command(s.strip())

        # Intercept forget/reset (GovTech-scoped)
        reply = handle_forget_command(s.strip().lower(), flow="govtech")
        if reply is not None:
            for line in reply:
                panel(line)
            # Live-refresh profile/engine after reset operations
            from .storage import load_profile as _lp
            new_prof = _lp() or {}
            if not new_prof:
                panel("🧾 No registration found after reset.")
                yn = input_prompt("Register now? (yes/no)").strip().lower()
                if yn in ("y", "yes"):
                    new_prof = run_registration_interactive()
                    if not new_prof:
                        panel("↩️ Returning to main menu.")
                        return
                    eng = Engine(new_prof)
                    continue
                else:
                    panel("↩️ Returning to main menu. ...")
                    return
            eng = Engine(new_prof)
            continue

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

        if cmd in ("/clear", "clear", "cln", "clr"):
            clear_session(); panel("🧹 Session cleared."); continue

        if cmd in ("/deregister", "deregister"):
            clear_profile(); clear_session()
            panel("👋 Deregistered and session cleared. Returning to main menu.")
            return

        # Keep engine profile in sync with disk each turn
        try:
            from .storage import load_profile as _lp
            eng.profile = _lp() or eng.profile
        except Exception:
            pass

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
    ex_tbl.add_row(_bullet_line('"factory reset" — Wipe ALL data including old generated timesheets'))

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
    # Reset / Forget (GovTech + global)
    ex_tbl.add_row(_bullet_line('"reset profile" — Clear registration (re-register next time)'))
    ex_tbl.add_row(_bullet_line('"reset generated" — Delete generated timesheet files only'))
    ex_tbl.add_row(_bullet_line('"reset my data" — Clear GovTech profile/session/settings (keeps Napta & generated)'))
    ex_tbl.add_row(_bullet_line('"factory reset" / "reset everything" — Wipe ALL data including old generated timesheets'))

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

    # Commands — one per line
    cmds = Text(
        "\n".join([
            "login",
            "view",
            "vnw (view next week)",
            "vpw (view previous week)",
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

    # Examples (only view/save/submit flows)
    ex_tbl = Table.grid(padding=(0, 1))
    ex_tbl.add_column()
    ex_tbl.add_row(_bullet_line("'login' — Sign in once (SSO) using cli and save the session"))
    ex_tbl.add_row(_bullet_line("'view' — Show CURRENT week entries"))
    ex_tbl.add_row(_bullet_line("'view next week' (or 'vnw') — Show NEXT week entries"))
    ex_tbl.add_row(_bullet_line("'view previous week' (or 'vpw') — Show PREVIOUS week entries"))
    ex_tbl.add_row(_bullet_line("'save' — Save CURRENT week (draft)"))
    ex_tbl.add_row(_bullet_line("'submit' — Submit CURRENT week for approval"))
    ex_tbl.add_row(_bullet_line("'save next week' (or 'snw') — Save NEXT week (draft)"))
    ex_tbl.add_row(_bullet_line("'submit next week' (or 'sbnw') — Submit NEXT week for approval"))
    ex_tbl.add_row(_bullet_line("'ss' — Save then Submit (CURRENT week)"))
    console.print(
        Panel(ex_tbl, title="Examples", title_align="left", border_style="cyan", box=box.ROUNDED, padding=(0, 1))
    )

    # --- Napta Note (bulleted) ---
    bullets = [
        "1. Run 'login' once to open browser SSO and save your session.",
        "2. `reset napta` or `forget napta` — clear Napta session/cache (forces re-login).",
        "🚀 Performance Tip: Using a VPN can slow down Napta actions. For best speed, run the tool WITHOUT VPN, then reconnect when done.",
    ]

    bt = Table.grid(padding=(0, 1))
    bt.add_column()

    for line in bullets:
        bt.add_row(Text("• ") + Text(line, style="bold yellow"))

    console.print(
        Panel(
            bt,
            title="Notes",
            title_align="left",
            border_style="yellow",
            box=ROUNDED,
            padding=(0, 1),
        )
    )
    

@catch_all(flow="Napta", on_cancel="exit")  # Ctrl-C/Ctrl-D => show panel once, then exit tool
def napta_loop(profile: dict) -> None:
    banner("Napta Timesheet")

    # Lightweight status (actions go through subprocess helper)
    client = NaptaClient()
    try:
        panel(f"Napta auth status: {client.status()}")
    except Exception:
        pass  # status is best-effort; don't block the flow

    _show_napta_simple_help_block()

    # def _run_napta_action(action: str):
    #     # Only hide the literal ^C; do NOT swallow interrupts.
    #     with suppress_ctrlc_echo():
    #         ok, msg = _napta_run(action)  # If Ctrl-C, this raises; decorator will exit.
    #     return ok, msg

    while True:
        # NOTE: Do NOT catch UserCancelled here; decorator will handle and exit.
        raw = input_prompt("napta›")
        if not raw:
            continue

        cmd = raw.strip().lower()

        # Forget/reset (Napta-scoped). This may call client.close() internally.
        reply = handle_forget_command(cmd, flow="napta", napta_client=client)
        if reply is not None:
            for line in reply:
                panel(line)
            continue

        # --- Exit / Back ---
        if cmd in ("/quit", "/q", "quit", "q", "/exit", "exit"):
            panel("👋 Bye!")
            raise SystemExit(0)

        if cmd in ("back", "/back"):
            panel("↩️ Back to main menu.")
            try:
                client.close()
            except Exception:
                pass
            return

        # ---------- All Napta actions via isolated subprocess ----------
        if cmd in ("login", "/login"):
            ok, msg = _run_napta_action("login")
            panel(_maybe_add_shot_hint(msg))
            continue

        if cmd in ("view", "/view", "show", "/show"):
            ok, msg = _run_napta_action("view_current")
            panel(_maybe_add_shot_hint(msg))
            continue

        if cmd in ("view next week", "/view next week",
                   "view-next-week", "/view-next-week", "vnw", "/vnw"):
            ok, msg = _run_napta_action("view_next")
            panel(_maybe_add_shot_hint(msg))
            continue

        if cmd in ("view previous week", "/view previous week",
                    "view-prev-week", "/view-prev-week",
                    "vpw", "/vpw", "vp", "/vp"):
            ok, msg = _run_napta_action("view_previous")
            panel(_maybe_add_shot_hint(msg))
            continue


        if cmd in ("save", "/save"):
            ok, msg = _run_napta_action("save_current")
            panel(_maybe_add_shot_hint(msg))
            continue

        if cmd in ("save next week", "/save next week",
                   "save-next-week", "/save-next-week", "snw", "/snw"):
            ok, msg = _run_napta_action("save_next")
            panel(_maybe_add_shot_hint(msg))
            continue

        if cmd in ("submit", "/submit"):
            ok, msg = _run_napta_action("submit_current")
            panel(_maybe_add_shot_hint(msg))
            continue

        if cmd in ("submit next week", "/submit next week",
                   "submit-next-week", "/submit-next-week", "sbnw", "/sbnw"):
            ok, msg = _run_napta_action("submit_next")
            panel(_maybe_add_shot_hint(msg))
            continue

        if cmd in ("ss", "/ss"):
            ok, msg = _run_napta_action("save_and_submit_current")
            panel(_maybe_add_shot_hint(msg))
            continue

        # --- Unknown command ---
        panel(
            "⚠️ Unknown command. Use one of:\n"
            "login\nview\nvnw (view next week)\n"
            "save\nsnw (save next week)\n"
            "submit\nsbnw (submit next week)\n"
            "ss (save & submit this week)\n"
            "back\nquit\nreset napta"
        )

# ------------------------------ main menu ------------------------------------

@catch_all(flow="CLI", on_cancel="exit")   # Ctrl-C at the main menu exits cleanly
def main(argv: Optional[list] = None) -> int:
    banner("CLI Tool")
    try:
        while True:
            choice = menu("Choose an option:", [
            "GovTech Timesheet",
            "Registration (GovTech Entries)",
            "Napta Timesheet",
            "Quit",
        ])

            if choice == "1":
                profile = ensure_profile()
                if not profile:
                    panel("↩️ Returning to main menu.")
                    continue
                govtech_loop(profile)   
            elif choice == "2":
                run_registration_interactive()        # Registration second
            elif choice == "3":
                # Napta does NOT require GovTech registration
                try:
                    profile = load_profile() or {}
                except Exception:
                    profile = {}
                napta_loop(profile)                   # Napta third
            elif choice == "4":
                panel("Goodbye! 👋")
                return 0
            else:
                panel("Please pick 1–4.")
    except UserCancelled:
        panel("👋 Bye!")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
