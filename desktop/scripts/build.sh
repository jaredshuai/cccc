#!/usr/bin/env bash
#
# Unified desktop build wrapper (macOS/Linux).
# Delegates to desktop/scripts/build.py to keep a single source of truth.
#
# Example:
#   ./build.sh --stage all --platform linux --channel stable --version auto
#   ./build.sh --stage app --channel canary --force app
#   ./build.sh --clean --stage all

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD_PY="$SCRIPT_DIR/build.py"

if [[ ! -f "$BUILD_PY" ]]; then
  echo "[ERROR] Missing orchestrator script: $BUILD_PY"
  exit 1
fi

if command -v python3 >/dev/null 2>&1; then
  PYTHON="python3"
elif command -v python >/dev/null 2>&1; then
  PYTHON="python"
else
  echo "[ERROR] Python 3 is required but not found in PATH."
  exit 1
fi

exec "$PYTHON" "$BUILD_PY" "$@"
