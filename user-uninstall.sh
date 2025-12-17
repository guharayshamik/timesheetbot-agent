#!/usr/bin/env bash
set -e

APP_NAME="tsbot"
INSTALL_DIR="$HOME/Applications/tsbot"
BIN_LINK="$HOME/.local/bin/tsbot"
DATA_DIR="$HOME/.tsbot"

echo "🗑 Uninstalling tsbot..."

# Remove binary + runtime
if [[ -d "$INSTALL_DIR" ]]; then
  rm -rf "$INSTALL_DIR"
  echo "✔ Removed $INSTALL_DIR"
else
  echo "ℹ️ $INSTALL_DIR not found"
fi

# Remove symlink
if [[ -L "$BIN_LINK" || -f "$BIN_LINK" ]]; then
  rm -f "$BIN_LINK"
  echo "✔ Removed $BIN_LINK"
else
  echo "ℹ️ $BIN_LINK not found"
fi

# Remove user data
if [[ -d "$DATA_DIR" ]]; then
  rm -rf "$DATA_DIR"
  echo "✔ Removed $DATA_DIR (all tsbot data)"
else
  echo "ℹ️ $DATA_DIR not found"
fi

# Clean PATH entry (best-effort, safe)
PROFILE="$HOME/.zprofile"
if [[ -f "$PROFILE" ]] && grep -q '# tsbot' "$PROFILE"; then
  sed -i '' '/# tsbot/,+1d' "$PROFILE"
  echo "✔ Cleaned tsbot PATH entry from $PROFILE"
fi

echo ""
echo "✅ tsbot fully uninstalled."
echo "You may close and reopen your terminal."

