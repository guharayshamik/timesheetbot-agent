#!/usr/bin/env bash
set -e

APP_NAME="tsbot"
INSTALL_DIR="$HOME/Applications/tsbot"
BIN_DIR="$HOME/.local/bin"
TARGET="$INSTALL_DIR/$APP_NAME"
LINK="$BIN_DIR/$APP_NAME"

echo "🚀 Installing tsbot..."

# Install app bundle
rm -rf "$INSTALL_DIR"
mkdir -p "$INSTALL_DIR"
cp -R ./* "$INSTALL_DIR/"
chmod +x "$TARGET"

# Create symlink
mkdir -p "$BIN_DIR"
ln -sf "$TARGET" "$LINK"

# Remove quarantine (best-effort)
xattr -dr com.apple.quarantine "$INSTALL_DIR" >/dev/null 2>&1 || true

# ---- PATH setup (robust) ----
PATH_LINE='export PATH="$HOME/.local/bin:$PATH"'

add_path_line() {
  local file="$1"
  mkdir -p "$(dirname "$file")"
  touch "$file"

  # Add only if the exact PATH line is not already present
  if ! grep -qsF "$PATH_LINE" "$file"; then
    echo "" >> "$file"
    echo "# tsbot" >> "$file"
    echo "$PATH_LINE" >> "$file"
  fi
}

# Most macOS users use zsh; interactive shells read ~/.zshrc reliably.
add_path_line "$HOME/.zshrc"
# Login shells read ~/.zprofile.
add_path_line "$HOME/.zprofile"
# If someone uses bash, these help.
add_path_line "$HOME/.bashrc"
add_path_line "$HOME/.bash_profile"

echo ""
echo "✅ Installed!"
echo "👉 Run anywhere using: tsbot"
echo ""
echo "ℹ️ If 'tsbot' is not found in a new terminal, run:"
echo "   source ~/.zshrc  (or restart your terminal)"
