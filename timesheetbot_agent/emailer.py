# timesheetbot_agent/emailer.py
from __future__ import annotations
import base64
from pathlib import Path
from typing import Optional
import requests
from msal import PublicClientApplication


GRAPH_SCOPE = ["https://graph.microsoft.com/Mail.Send"]
GRAPH_BASE = "https://graph.microsoft.com/v1.0"


def _b64_file(path: Path) -> str:
    data = path.read_bytes()
    return base64.b64encode(data).decode("ascii")


def send_mail_via_graph(
    tenant_id: str,
    client_id: str,
    from_upn: str,                  # e.g. "sguharay@palo-it.com"
    to_email: str,
    subject: str,
    body_text: str,
    attachment_path: Optional[Path] = None,
) -> None:
    """
    Uses Microsoft Graph Device Code flow to send an email with optional attachment.
    First run will prompt a device-code login in your terminal.
    """
    app = PublicClientApplication(client_id=client_id, authority=f"https://login.microsoftonline.com/{tenant_id}")

    # Try cached token first
    accounts = app.get_accounts(username=from_upn)
    result = None
    if accounts:
        result = app.acquire_token_silent(GRAPH_SCOPE, account=accounts[0])
    if not result:
        flow = app.initiate_device_flow(scopes=GRAPH_SCOPE)
        if "user_code" not in flow:
            raise RuntimeError("Device flow failed to start.")
        # Show the login instructions to the user
        print(f"\n🔐 To authorize email sending, go to: {flow['verification_uri']}")
        print(f"   Enter the code: {flow['user_code']}\n")
        result = app.acquire_token_by_device_flow(flow)

    if "access_token" not in result:
        raise RuntimeError(f"Auth failed: {result.get('error_description') or result}")

    headers = {"Authorization": f"Bearer {result['access_token']}", "Content-Type": "application/json"}

    message = {
        "subject": subject,
        "body": {"contentType": "Text", "content": body_text},
        "toRecipients": [{"emailAddress": {"address": to_email}}],
    }

    if attachment_path:
        p = Path(attachment_path)
        message.setdefault("attachments", []).append(
            {
                "@odata.type": "#microsoft.graph.fileAttachment",
                "name": p.name,
                "contentType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "contentBytes": _b64_file(p),
            }
        )

    payload = {"message": message, "saveToSentItems": True}

    # Use /users/{from_upn}/sendMail so it sends as your account
    url = f"{GRAPH_BASE}/users/{from_upn}/sendMail"
    resp = requests.post(url, headers=headers, json=payload)
    if resp.status_code not in (202, 200):
        raise RuntimeError(f"Graph sendMail failed ({resp.status_code}): {resp.text}")
