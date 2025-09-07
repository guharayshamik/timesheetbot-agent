# timesheetbot_agent/registration.py
from __future__ import annotations

from .storage import load_profile, save_profile
from .security import mask_email

def _ask(label: str, default: str = "", *, validator=None, allow_empty: bool = False, free_text: bool = False) -> str:
    """
    Generic prompt helper.
    - validator: callable(str) -> (ok: bool, msg: str)
    - free_text: when True, no validation is applied; the raw input is returned.
    """
    while True:
        prompt = f"{label}"
        if default:
            prompt += f" [{default}]"
        prompt += ": "
        val = input(prompt).strip()

        if not val:
            val = default

        if free_text:
            # Accept exactly what the user typed.
            return val

        if validator and val:
            ok, msg = validator(val)
            if not ok:
                print(msg or "Invalid value. Please try again.")
                continue

        if not val and not allow_empty:
            print("Value cannot be empty. Please try again.")
            continue

        return val


def run_registration_interactive() -> dict:
    """
    Interactive registration for local profile (stored in ~/.tsbot/user.json).
    PO Date is captured as raw free text.
    """
    print("\n— Registration —")
    print("Enter your details. Press Enter to keep defaults shown in [brackets].\n")

    profile = load_profile() or {}

    # Simple validators (kept for other fields)
    def _non_empty(v: str): 
        return (bool(v.strip()), "This field cannot be empty.")
    def _email(v: str):
        ok = ("@" in v and "." in v.split("@")[-1])
        return (ok, "Please enter a valid email (e.g., jane@palo-it.com).")

    name = _ask("Name", profile.get("name", "Jane Doe"), validator=_non_empty)
    skill_level = _ask("Skill Level [Senior Consultant]:", profile.get("skill_level", "Professional"), validator=_non_empty)
    role = _ask("Role Specialization [Data & AI]:", profile.get("role_specialization", "DevOps Engineer – II"), validator=_non_empty)
    group = _ask("Group/Specialization [Emerging Technology]:", profile.get("group_specialization", "Consulting"), validator=_non_empty)

    contractor = _ask("Contractor [PALO IT]:", profile.get("contractor", "PALO IT"), validator=_non_empty)

    po_ref = _ask("PO Ref:", profile.get("po_ref", "GVT000ECN20300028"), validator=_non_empty)

    # 👇 Free text PO Date — no validation, store exactly as typed
    po_date = _ask("PO Date:", profile.get("po_date", "01 May 2025 to 31 May 2026"), free_text=True)

    description = _ask(
        "Description:",
        profile.get("description", "WINS Provision of Augmented resources (PALO) for 01 May 2025 to 31 May 2026 (PR24-01272)"),
        validator=_non_empty,
    )

    reporting_officer = _ask("Reporting Officer:", profile.get("reporting_officer", "John Doe"), validator=_non_empty)

    email = _ask("Work email [name@palo-it.com]:", profile.get("email", "jane.doe@palo-it.com"), validator=_email)

    # Preferred daily unit (GovTech uses 1.0 by default; your Excel supports 1.0 or 8.5)
    pref = _ask("Timesheet unit per day [1.0 or 8.5]:", str(profile.get("timesheet_preference", "1.0")), validator=None)
    try:
        timesheet_preference = float(pref)
        if timesheet_preference not in (1.0, 8.5):
            raise ValueError
    except Exception:
        timesheet_preference = 1.0

    client = _ask("Client (GovTech/Napta):", profile.get("client", "GovTech"), validator=_non_empty)
    govtech_project = _ask("GovTech Project (e.g., MOM–WINS):", profile.get("govtech_project", "MOM–WINS"), validator=_non_empty)

    # NEW (minimal change): ask only for manager email
    manager_email = _ask("Manager email (default recipient for /email)", profile.get("manager_email", ""), validator=_email)

    # NEW (minimal change): auto-derive manager_first_name from reporting officer
    manager_first_name = ""
    if reporting_officer.strip():
        manager_first_name = reporting_officer.strip().split()[0]

    profile.update({
        "name": name,
        "skill_level": skill_level,
        "role_specialization": role,
        "group_specialization": group,
        "contractor": contractor,
        "po_ref": po_ref,
        "po_date": po_date,                    
        "description": description,
        "reporting_officer": reporting_officer,
        "email": email,
        "timesheet_preference": timesheet_preference,
        "client": client,
        "govtech_project": govtech_project,
        "manager_email": manager_email,        # <- added
        "manager_first_name": manager_first_name,  # <- derived, not prompted
    })

    save_profile(profile)
    print(f"\n✅ Saved locally at ~/.tsbot/profile.json")
    print(f"   Email: {mask_email(email)}")
    return profile
