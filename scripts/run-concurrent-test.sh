#!/usr/bin/env bash
# Run 10 concurrent agents against a shared swerex server.
# Requires: swerex-server running on :8000, vLLM server accessible.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

export OPENAI_API_KEY="${OPENAI_API_KEY:-EMPTY}"
export MSWEA_PUSHGATEWAY_URL="${MSWEA_PUSHGATEWAY_URL:-http://localhost:9091}"
export MSWEA_OTLP_ENDPOINT="${MSWEA_OTLP_ENDPOINT:-http://localhost:4318}"

source "$PROJECT_ROOT/.venv/bin/activate"
python "$PROJECT_ROOT/examples/concurrent_test.py"
