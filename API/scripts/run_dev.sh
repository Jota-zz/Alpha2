#!/usr/bin/env bash
# Arranque de desarrollo del Alpha Bot API (reemplaza el WebhookServer.start del notebook).
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONUNBUFFERED=1
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --reload
