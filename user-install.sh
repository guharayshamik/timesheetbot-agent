#!/usr/bin/env bash
set -e

APP_NAME="tsbot"
INSTALL_DIR="$HOME/Applications/tsbot"
BIN_DIR="$HOME/.local/bin"

echo "🚀 Installing tsbot..."

rm -rf "$INSTALL_DIR"
mkdir -p "$INSTALL_DIR"
cp -R ./* "$INSTALL_DIR/"
chmod +x "$INSTALL_DIR/$APP_NAME"

mkdir -p "$BIN_DIR"
ln -sf "$INSTALL_DIR/$APP_NAME" "$BIN_DIR/$APP_NAME"

xattr -dr com.apple.quarantine "$INSTALL_DIR" || true

if ! echo "$PATH" | grep -q "$BIN_DIR"; then
  echo '' >> "$HOME/.zprofile"
  echo '# tsbot' >> "$HOME/.zprofile"
  echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/.zprofile"
  echo "ℹ️ Added tsbot to PATH. Restart terminal."
fi

echo ""
echo "✅ Installed!"
echo "👉 Run anywhere using: tsbot"

