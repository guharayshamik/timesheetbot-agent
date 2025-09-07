# timesheetbot_agent/ui.py
from __future__ import annotations
from typing import Iterable, Optional

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.prompt import Prompt
from rich.box import ROUNDED
from rich.text import Text

console = Console()

BORDER = "bright_blue"

def banner(profile_line: str) -> None:
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

def menu(title: str, options: list[str]) -> str:
    table = Table(box=ROUNDED, show_header=False, expand=True, border_style=BORDER, padding=(0,1))
    table.add_column(justify="center", style="bold")
    table.add_column()
    for i, label in enumerate(options, start=1):
        table.add_row(f"[cyan]{i}[/]", label)
    console.print(Panel.fit(table, title=title, border_style=BORDER, box=ROUNDED))
    return Prompt.ask("[bold]Enter choice[/] (1–{n})".format(n=len(options)),
                      choices=[str(i) for i in range(1, len(options)+1)],
                      show_choices=False)

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
    for line in lines:
        panel(line)

def input_prompt(prompt_text: str = "›") -> str:
    return Prompt.ask(f"[bold cyan]{prompt_text}[/]")

def note(msg: str) -> None:
    console.print(f"[dim]{msg}[/]")
