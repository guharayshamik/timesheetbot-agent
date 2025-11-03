# timesheetbot_agent/mailer.py
from __future__ import annotations

import os
import platform
import subprocess
import urllib.parse
from pathlib import Path
from typing import Optional


def _esc(s: str) -> str:
    """Escape for AppleScript string literals."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _as_outlook_body_appleexpr(s: str) -> str:
    """
    Build an AppleScript expression that concatenates lines with CRLF:
    "Hi," & (ASCII character 13) & (ASCII character 10) & ...
    """
    lines = s.splitlines()
    if not lines:
        return '""'
    joiner = '" & (ASCII character 13) & (ASCII character 10) & "'
    return '"' + joiner.join(_esc(line) for line in lines) + '"'


def send_via_graph(
    *, tenant_id: str, client_id: str, from_upn: str,
    to_email: str, subject: str, body_text: str,
    attachment_path: Optional[Path] = None
) -> None:
    from .emailer import send_mail_via_graph  # thin wrapper
    return send_mail_via_graph(
        tenant_id=tenant_id,
        client_id=client_id,
        from_upn=from_upn,
        to_email=to_email,
        subject=subject,
        body_text=body_text,
        attachment_path=attachment_path,
    )


def compose_outlook_mac(
    to, subject, body, attachment, cc=None, bcc=None,
) -> None:
    if platform.system() != "Darwin":
        raise RuntimeError("Outlook AppleScript compose is only supported on macOS.")

    lines = body.splitlines() or [""]
    first = _esc(lines[0])
    rest = [
        f'set content of newMsg to (content of newMsg) & (ASCII character 10) & "{_esc(l)}"'
        for l in lines[1:]
    ]
    append_lines_script = "\n".join(rest)

    to_lines = "\n".join(
        f'make new recipient at newMsg with properties {{email address:{{address:"{_esc(addr)}"}}}}'
        for addr in (to or [])
    )
    cc = cc or []
    bcc = bcc or []
    cc_lines = "\n".join(
        f'make new cc recipient at newMsg with properties {{email address:{{address:"{_esc(addr)}"}}}}'
        for addr in cc
    )
    bcc_lines = "\n".join(
        f'make new bcc recipient at newMsg with properties {{email address:{{address:"{_esc(addr)}"}}}}'
        for addr in bcc
    )

    script = f'''
    tell application "Microsoft Outlook"
        activate
        set newMsg to make new outgoing message with properties {{subject:"{_esc(subject)}", content:""}}
        -- Write the body FIRST, one line at a time (prevents Outlook from flattening)
        set content of newMsg to "{first}"
        {append_lines_script}
        -- Now add recipients and attachment
        {to_lines}
        {cc_lines}
        {bcc_lines}
        make new attachment at newMsg with properties {{file:(POSIX file "{_esc(str(attachment))}")}}
        open newMsg
        activate
    end tell
    '''
    subprocess.run(["osascript", "-e", script], check=True)


def compose_with_best_available(to, subject, body, attachment=None, cc=None):
    """Prefer native Outlook on macOS; otherwise fall back to mailto."""
    system = platform.system()
    try:
        if system == "Darwin":
            try:
                compose_outlook_mac(to, subject, body, attachment, cc)
                return
            except Exception:
                # Fallback: open default mail client via mailto
                pass

        to_str = ";".join(to) if isinstance(to, (list, tuple)) else to
        cc_str = ";".join(cc) if cc else ""
        mailto = (
            f"mailto:{urllib.parse.quote(to_str)}"
            f"?subject={urllib.parse.quote(subject)}"
            f"&body={urllib.parse.quote(body)}"
        )
        if cc_str:
            mailto += f"&cc={urllib.parse.quote(cc_str)}"

        if system == "Darwin":
            subprocess.run(["open", mailto], check=False)
        elif system == "Windows":
            os.startfile(mailto)  # type: ignore[arg-type]
        else:
            subprocess.run(["xdg-open", mailto], check=False)
    except Exception as e:
        print(f"Could not launch mail client: {e}")
