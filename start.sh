#!/usr/bin/env sh
# Convenience launcher for a checkout (no install needed).
#   ./start.sh                # 0.0.0.0:8848
#   ./start.sh --local-only --port 9000
cd "$(dirname "$0")" || exit 1
exec "${PYTHON:-python3}" -m agdeck serve "$@"
