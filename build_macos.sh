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

# 6. Bundle Playwright browsers where frozen app expects them
PW_DST="dist/tsbot/_internal/ms-playwright"
mkdir -p "$PW_DST"

if [[ -d "$HOME/Library/Caches/ms-playwright" ]]; then
  cp -R "$HOME/Library/Caches/ms-playwright/"* "$PW_DST/"
else
  echo "❌ Playwright browser cache not found at $HOME/Library/Caches/ms-playwright"
  exit 1
fi

# 7. Add user installer + uninstaller + readme
cp user-install.sh dist/tsbot/install.sh
cp user-uninstall.sh dist/tsbot/uninstall.sh

# Copy README for users (plain text)
cp readme.txt dist/tsbot/readme.txt

chmod +x dist/tsbot/install.sh
chmod +x dist/tsbot/uninstall.sh
chmod +x dist/tsbot/tsbot

# Sanity checks (fail fast if something missing)
for f in dist/tsbot/tsbot dist/tsbot/install.sh dist/tsbot/uninstall.sh dist/tsbot/readme.txt; do
  if [[ ! -e "$f" ]]; then
    echo "❌ Missing expected file: $f"
    exit 1
  fi
done


echo ""
echo "✅ Build complete!"
echo ""
echo "📂 Output:"
echo "   dist/tsbot"
echo ""
echo "📦 Zip it with:"
echo "   cd dist && zip -r tsbot-macos-$(uname -m).zip tsbot"

