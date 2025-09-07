# timesheetbot_agent/security.py

from __future__ import annotations

def mask_email(email: str) -> str:
    if not email or "@" not in email:
        return email
    local, domain = email.split("@", 1)
    if len(local) <= 2:
        return f"{local[0]}* @{domain}".replace(" *", "*")
    return f"{local[:2]}{'*'*(len(local)-2)}@{domain}"
