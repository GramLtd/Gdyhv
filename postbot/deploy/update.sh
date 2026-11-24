#!/usr/bin/env bash
# Обновление: bash postbot/deploy/update.sh
set -euo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)"
git -C "$DIR" pull --ff-only
"$DIR/.venv/bin/pip" install -q -r "$DIR/requirements.txt"
systemctl restart postbot
systemctl --no-pager status postbot | head -5
