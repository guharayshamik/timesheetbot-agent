TimesheetBot Agent (macOS)

Install:
  1) Unzip the downloaded file
  2) Open Terminal and go into the folder
  3) Run:
     ./install.sh
  4) Close and reopen your terminal

Run:
  tsbot

Data stored in:
  ~/.tsbot

This folder contains:
  - Your registration details
  - GovTech session data
  - Napta session and screenshots
  - Generated Excel timesheets

Reset (inside the app):
  factory reset

Factory reset will:
  - Delete all data inside ~/.tsbot
  - Remove registrations, sessions, and generated files
  - Keep tsbot installed

Uninstall (remove tsbot completely):
  cd ~/Applications/tsbot
  ./uninstall.sh

Uninstall will remove:
  - The tsbot application
  - The tsbot command from PATH
  - All data under ~/.tsbot

Reinstall anytime:
  Unzip again and run:
  ./install.sh

If macOS blocks execution (Gatekeeper):
  xattr -dr com.apple.quarantine ~/Applications/tsbot

Notes:
  - No admin rights required
  - Runs fully locally
  - Safe to install and uninstall anytime

