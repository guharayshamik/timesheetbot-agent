#!/usr/bin/env bash
set -e

APP_NAME="tsbot"                        # executable name (what users run)
DIST_DIR="tsbot-timesheet-only"         # folder name in dist/ (won’t clash with full build)
ENTRYPOINT="timesheetbot_agent/tsbot_entry.py"

echo "🏗 Building ${APP_NAME} (GovTech-only) for macOS ($(uname -m))"

# Activate venv if present
if [[ -d ".venv" ]]; then
  source .venv/bin/activate
fi

pip install -U pip
pip install pyinstaller

# Clean only this build’s artifacts (do NOT wipe dist/tsbot)
rm -rf "build/${DIST_DIR}" "dist/${DIST_DIR}"
rm -f ./*.spec 2>/dev/null || true

pyinstaller \
  --name "$APP_NAME" \
  --onedir \
  --clean \
  --distpath "dist/${DIST_DIR}" \
  --workpath "build/${DIST_DIR}" \
  --collect-all rich \
  --exclude-module playwright \
  --exclude-module playwright.sync_api \
  --exclude-module playwright.async_api \
  --exclude-module timesheetbot_agent.napta \
  --add-data "timesheetbot_agent/config:timesheetbot_agent/config" \
  "$ENTRYPOINT"

# Drop correct installer/uninstaller/readme (tsbot command)
cp user-install.sh "dist/${DIST_DIR}/${APP_NAME}/install.sh"
cp user-uninstall.sh "dist/${DIST_DIR}/${APP_NAME}/uninstall.sh"
cp readme.txt "dist/${DIST_DIR}/${APP_NAME}/readme.txt"

chmod +x "dist/${DIST_DIR}/${APP_NAME}/install.sh"
chmod +x "dist/${DIST_DIR}/${APP_NAME}/uninstall.sh"
chmod +x "dist/${DIST_DIR}/${APP_NAME}/${APP_NAME}"

# Sanity: ensure no playwright/napta artifacts
if find "dist/${DIST_DIR}/${APP_NAME}" | egrep -i -q "playwright|ms-playwright|chromium|napta"; then
  echo "❌ Playwright/Napta artifacts detected. This build should be GovTech-only."
  exit 1
fi

echo ""
echo "✅ Build complete!"
echo "📂 Output: dist/${DIST_DIR}/${APP_NAME}"
echo "📦 Zip it with:"
echo "   cd dist/${DIST_DIR} && zip -r tsbot-timesheet-only-macos-$(uname -m).zip ${APP_NAME}"

