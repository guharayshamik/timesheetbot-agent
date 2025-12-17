#!/usr/bin/env bash
set -e

APP_NAME="tsbot"
BIN_DIR="$HOME/.local/bin"

echo "🔧 Installing tsbot (dev mode)..."

# Install editable
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e .

# Ensure bin dir
mkdir -p "$BIN_DIR"

# Link tsbot command
ln -sf "$(which tsbot)" "$BIN_DIR/tsbot"

echo ""
echo "✅ Dev install done"
echo "👉 Run: tsbot"

