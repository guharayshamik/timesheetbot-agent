# timesheetbot_agent/ui.py
from __future__ import annotations
from typing import Iterable

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.prompt import Prompt
from rich.text import Text
from rich import box
from rich.box import ROUNDED


console = Console()

# Default border color for our panels
BORDER = "bright_blue"


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


def input_prompt(prompt_text: str = "›") -> str:
    """Unified input prompt (styled)."""
    return Prompt.ask(f"[bold cyan]{prompt_text}[/]")


def note(msg: str) -> None:
    """Dim, inline note."""
    console.print(f"[dim]{msg}[/]")


# ── Vibrant help block (LLM chip + Examples + Commands) ─────────────────────────
def _bullet_line(s: str, style: str = "bold green") -> Text:
    return Text("• ", style="dim") + Text(s, style=style)


def show_vibrant_help() -> None:
    """Pretty 'Chat mode ON + examples + commands' block."""

    # Chip-like header
    chip = Text.assemble(("⚡  Chat mode", "bold"), ("  ON", "bold bright_green"))
    console.print(
        Panel(chip, border_style="bright_green", padding=(0, 1), box=box.SQUARE)
    )

    # Subtitle
    console.print(Text("Describe your work/leave in plain English, e.g.:", style="bold cyan"))

    # Examples panel
    ex_tbl = Table.grid(padding=(0, 1))
    ex_tbl.add_column()
    ex_tbl.add_row(_bullet_line('"generate timesheet for August"'))
    ex_tbl.add_row(_bullet_line('"annual leave 11–13 Aug"'))
    ex_tbl.add_row(_bullet_line('"sick leave on 11 Aug"'))
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

    # Commands panel (include /comment)
    cmds = Text(
        "/show   /clear   /deregister   /generate   /comment   /help   /back   /email   /quit",
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

# ── Fitnet (Leave) help blocks ──────────────────────────────────────────────────
def fitnet_header() -> None:
    chip = Text.assemble(("🧭  Fitnet", "bold"), ("  LEAVE", "bold bright_green"))
    console.print(Panel(chip, border_style="bright_green", padding=(0, 1), box=box.SQUARE))
    console.print(Text("Type your leave in plain English, then preview or commit to Fitnet.", style="bold cyan"))

def fitnet_commands() -> None:
    # Examples
    ex_tbl = Table.grid(padding=(0, 1))
    ex_tbl.add_column()
    ex_tbl.add_row(_bullet_line('"mc on 11 Sep"'))
    ex_tbl.add_row(_bullet_line('"annual leave 1–3 Aug"'))
    ex_tbl.add_row(_bullet_line('"/comment 11 Sep OIL"'))
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
    cmds = Text("/login   /preview   /commit   /show   /clear   /help   /back   /quit", style="bold magenta")
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
