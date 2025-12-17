#!/usr/bin/env bash
set -e

APP_NAME="tsbot"
ENTRYPOINT="timesheetbot_agent/tsbot_entry.py"

echo "🏗 Building tsbot for macOS ($(uname -m))"

# 1. Activate venv if present
if [[ -d ".venv" ]]; then
  source .venv/bin/activate
fi

# 2. Ensure tools
pip install -U pip
pip install pyinstaller playwright

# 3. Install Playwright Chromium (for bundling)
python -m playwright install chromium

# 4. Clean previous builds
rm -rf build dist

# 5. Build binary
pyinstaller \
  --name "$APP_NAME" \
  --onedir \
  --clean \
  --collect-all playwright \
  --collect-all rich \
  --add-data "timesheetbot_agent/config:timesheetbot_agent/config" \
  "$ENTRYPOINT"

# 6. Bundle Playwright browsers
mkdir -p dist/tsbot/ms-playwright
cp -R "$HOME/Library/Caches/ms-playwright/"* dist/tsbot/ms-playwright/

# 7. Add user installer
cp user-install.sh dist/tsbot/install.sh
chmod +x dist/tsbot/install.sh
chmod +x dist/tsbot/tsbot

echo ""
echo "✅ Build complete!"
echo ""
echo "📂 Output:"
echo "   dist/tsbot"
echo ""
echo "📦 Zip it with:"
echo "   cd dist && zip -r tsbot-macos-$(uname -m).zip tsbot"

