# timesheetbot_agent/ui.py
from __future__ import annotations
from typing import Iterable
import os
import asyncio

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.prompt import Prompt
from rich.text import Text
from rich import box
from rich.box import ROUNDED

console = Console()
BORDER = "bright_blue"

# ---------- Backspace/Delete fix (GNU readline & macOS libedit) ----------
def _fix_backspace_delete() -> None:
    """
    Ensure Backspace and Delete keys erase characters instead of printing '^?'.
    Safe to call multiple times (no-ops if readline missing).
    """
    try:
        import readline  # type: ignore
        doc = (getattr(readline, "__doc__", "") or "").lower()
        if "libedit" in doc:
            # macOS libedit key names & commands
            # Backspace: ^? and ^H  |  Delete: ESC [ 3 ~
            readline.parse_and_bind("bind ^? ed-delete-prev-char")
            readline.parse_and_bind("bind ^H ed-delete-prev-char")
            readline.parse_and_bind("bind \\e[3~ ed-delete-next-char")
        else:
            # GNU readline key names & commands
            readline.parse_and_bind('"\C-?": backward-delete-char')
            readline.parse_and_bind('"\C-h": backward-delete-char')
            readline.parse_and_bind('"\e[3~": delete-char')
    except Exception:
        # If readline not present or binding fails, just skip quietly.
        pass

# Apply once on import (harmless to run again later)
_fix_backspace_delete()

# ---------- Optional: prompt_toolkit for nicer input ----------
try:
    from prompt_toolkit import prompt as pt_prompt
    from prompt_toolkit.styles import Style as PTKStyle
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
    from prompt_toolkit.key_binding import KeyBindings

    HAVE_PTK = True
    _TSBOT_HISTORY = FileHistory(os.path.expanduser("~/.timesheetbot_history"))
    _kb = KeyBindings()

    @_kb.add("c-c")  # Ctrl+C
    def _(event):
        raise KeyboardInterrupt

    @_kb.add("c-d")  # Ctrl+D on empty buffer => EOF
    def _(event):
        buf = event.app.current_buffer
        if not buf.text:
            event.app.exit(result="")
        else:
            buf.delete()

    # Make sure Delete works in PTK too.
    @_kb.add("delete")
    def _(event):
        event.current_buffer.delete(1)

except Exception:
    HAVE_PTK = False


def _event_loop_running() -> bool:
    """Return True if an asyncio loop is already running (e.g., Jupyter/VSCode)."""
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False


def input_prompt(prompt_text: str = "›", *, highlight_typed: bool = False) -> str:
    """
    Unified input prompt.

    - Uses prompt_toolkit (history, suggestions, keybindings) when available
      AND no event loop is already running.
    - Only the label is cyan by default; typed text is not highlighted.
      Pass highlight_typed=True if you ever want bold input.
    - Falls back to Rich Prompt.ask otherwise; we re-apply the
      readline/libedit fix so Backspace/Delete always work.
    """
    if HAVE_PTK and not _event_loop_running():
        style = PTKStyle.from_dict({
            "prompt": "bold cyan",
            "": "bold white" if highlight_typed else "",  # style for typed characters
        })
        return pt_prompt(
            [("class:prompt", f"{prompt_text} ")],
            style=style,
            history=_TSBOT_HISTORY,
            auto_suggest=AutoSuggestFromHistory(),
            key_bindings=_kb,
        )
    else:
        # Ensure erase keys behave correctly in fallback too
        _fix_backspace_delete()
        return Prompt.ask(f"[bold cyan]{prompt_text}[/]")

# ── Top banner ──────────────────────────────────────────────────────────────────
def banner(profile_line: str) -> None:
    """Show the welcome banner."""
    title = Text("Timesheet BOT agent — PALO IT", style="bold cyan")
    subtitle = Text(profile_line, style="dim")
    body = Text("I am here to assist in filling up your timesheet.", style="white")
    console.print(
        Panel(
            body,
            title=title,
            subtitle=subtitle,
            box=ROUNDED,
            border_style=BORDER,
            expand=True,
        )
    )

# ── Menu ────────────────────────────────────────────────────────────────────────
def menu(title: str, options: list[str]) -> str:
    """Render a numbered menu and return the chosen option (as a string)."""
    table = Table(
        box=ROUNDED, show_header=False, expand=True, border_style=BORDER, padding=(0, 1)
    )
    table.add_column(justify="center", style="bold")
    table.add_column()
    for i, label in enumerate(options, start=1):
        table.add_row(f"[cyan]{i}[/]", label)
    console.print(Panel.fit(table, title=title, border_style=BORDER, box=ROUNDED))
    return Prompt.ask(
        f"[bold]Enter choice[/] (1–{len(options)})",
        choices=[str(i) for i in range(1, len(options) + 1)],
        show_choices=False,
    )

# ── Message panels ──────────────────────────────────────────────────────────────
def panel(msg: str) -> None:
    """Pretty-print a single message in a colored box based on its emoji/severity."""
    style = "white"
    if msg.startswith(("✅", "🟢", "🎉")):
        style = "green"
    elif msg.startswith(("⚠️", "❗", "🧐")):
        style = "yellow"
    elif msg.startswith(("❌", "⛔")):
        style = "red"
    elif msg.startswith(("📊", "💾", "📁")) or "Saved ->" in msg:
        style = "cyan"
    elif msg.startswith(("📝", "✍️")):
        style = "magenta"
    console.print(Panel(msg, border_style=style, box=ROUNDED))

def panels(lines: Iterable[str]) -> None:
    """Render a list of lines as individual panels."""
    for line in lines:
        panel(line)

def note(msg: str) -> None:
    """Dim, inline note."""
    console.print(f"[dim]{msg}[/]")

# ── Vibrant help block (LLM chip + Examples + Commands) ─────────────────────────
def _bullet_line(s: str, style: str = "bold green") -> Text:
    return Text("• ", style="dim") + Text(s, style=style)

def show_vibrant_help() -> None:
    """Pretty 'Chat mode ON + examples + commands' block."""
    chip = Text.assemble(("⚡  Chat mode", "bold"), ("  ON", "bold bright_green"))
    console.print(Panel(chip, border_style="bright_green", padding=(0, 1), box=box.SQUARE))
    console.print(Text("Describe your work/leave in plain English, e.g.:", style="bold cyan"))

    ex_tbl = Table.grid(padding=(0, 1))
    ex_tbl.add_column()
    ex_tbl.add_row(_bullet_line('"generate timesheet for August"'))
    ex_tbl.add_row(_bullet_line('"annual leave 11–13 Aug"'))
    ex_tbl.add_row(_bullet_line('"sick leave on 11 Aug"'))
    console.print(
        Panel(ex_tbl, title="Examples", title_align="left",
              border_style="cyan", box=box.ROUNDED, padding=(0, 1))
    )

    cmds = Text(
        "/show   /clear   /deregister   /generate   /comment   /help   /back   /email   /quit",
        style="bold magenta",
    )
    console.print(
        Panel(cmds, title="Commands", title_align="left",
              border_style="magenta", box=box.ROUNDED, padding=(0, 1))
    )

# ── Fitnet (Leave) help blocks ──────────────────────────────────────────────────
def fitnet_header() -> None:
    chip = Text.assemble(("🧭  Fitnet", "bold"), ("  LEAVE", "bold bright_green"))
    console.print(Panel(chip, border_style="bright_green", padding=(0, 1), box=box.SQUARE))
    console.print(Text("Type your leave in plain English, then preview or commit to Fitnet.", style="bold cyan"))

def fitnet_commands() -> None:
    ex_tbl = Table.grid(padding=(0, 1))
    ex_tbl.add_column()
    ex_tbl.add_row(_bullet_line('"mc on 11 Sep"'))
    ex_tbl.add_row(_bullet_line('"annual leave 1–3 Aug"'))
    ex_tbl.add_row(_bullet_line('"/comment 11 Sep OIL"'))
    console.print(
        Panel(ex_tbl, title="Examples", title_align="left",
              border_style="cyan", box=box.ROUNDED, padding=(0, 1))
    )

    cmds = Text("/login   /preview   /commit   /show   /clear   /help   /back   /quit", style="bold magenta")
    console.print(
        Panel(cmds, title="Commands", title_align="left",
              border_style="magenta", box=box.ROUNDED, padding=(0, 1))
    )
