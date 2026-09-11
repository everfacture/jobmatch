#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"

echo "JobMatch first launch may download Python and extras. Leave this window open."
echo

export PATH="$HOME/.local/bin:$PATH"
if ! command -v uv >/dev/null 2>&1; then
  echo "Installing the JobMatch engine..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "Could not install uv. Check your internet connection and try again."
  exit 1
fi

uv python install 3.12
uv sync
uv run playwright install chromium
uv run jobmatch app
