# timesheetbot_agent/ph_sg.py
"""
Singapore Public Holidays (ISO date -> name) used by the GovTech sheet.

Notes:
- 2024–2026 are taken from the Ministry of Manpower’s official lists.
- 2027 uses forward-looking calendar estimates; update once MOM publishes the official list.
"""

PUBLIC_HOLIDAYS = {
    # ---------- 2024 (MOM official) ----------
    "2024-02-10": "Chinese New Year",
    "2024-02-11": "Chinese New Year",
    "2024-03-29": "Good Friday",
    "2024-04-10": "Hari Raya Puasa",
    "2024-05-01": "Labour Day",
    "2024-05-22": "Vesak Day",
    "2024-06-17": "Hari Raya Haji",
    "2024-08-09": "National Day",
    "2024-10-31": "Deepavali",
    "2024-12-25": "Christmas Day",

    # ---------- 2025 (MOM official) ----------
    "2025-01-01": "New Year's Day",
    "2025-01-29": "Chinese New Year",
    "2025-01-30": "Chinese New Year",
    "2025-03-31": "Hari Raya Puasa",
    "2025-04-18": "Good Friday",
    "2025-05-01": "Labour Day",
    "2025-05-12": "Vesak Day",
    "2025-06-07": "Hari Raya Haji",
    "2025-08-09": "National Day",
    "2025-10-20": "Deepavali",
    "2025-12-25": "Christmas Day",

    # ---------- 2026 (MOM official) ----------
    "2026-01-01": "New Year's Day",
    "2026-02-17": "Chinese New Year",
    "2026-02-18": "Chinese New Year",
    "2026-03-21": "Hari Raya Puasa",   # subject to further confirmation by MUIS
    "2026-04-03": "Good Friday",
    "2026-05-01": "Labour Day",
    "2026-05-31": "Vesak Day",
    "2026-05-27": "Hari Raya Haji",    # subject to further confirmation by MUIS
    "2026-08-09": "National Day",
    "2026-11-08": "Deepavali",
    "2026-12-25": "Christmas Day",

    # ---------- 2027 (provisional; update when MOM publishes) ----------
    # Source: timeanddate / consolidated holiday calendars (estimates).
    "2027-01-01": "New Year's Day",
    "2027-02-06": "Chinese New Year",
    "2027-02-07": "Chinese New Year",
    "2027-03-10": "Hari Raya Puasa",   # estimated
    "2027-03-26": "Good Friday",
    "2027-05-01": "Labour Day",
    "2027-05-17": "Hari Raya Haji",    # estimated (some sources list 2027-05-16; verify when MOM releases)
    "2027-05-20": "Vesak Day",         # estimated
    "2027-08-09": "National Day",
    "2027-10-29": "Deepavali",         # estimated
    "2027-12-25": "Christmas Day",
}
