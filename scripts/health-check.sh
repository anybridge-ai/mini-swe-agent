#!/usr/bin/env bash
# Health check all components at once.
set -euo pipefail

VLLM_BASE="${VLLM_BASE:-http://143.248.136.10:8066}"
SWEREX_HOST="${SWEREX_HOST:-http://127.0.0.1:8000}"
PUSHGATEWAY_URL="${PUSHGATEWAY_URL:-http://localhost:9091}"
OTLP_ENDPOINT="${OTLP_ENDPOINT:-http://143.248.136.10:4318}"
GRAFANA_URL="${GRAFANA_URL:-http://localhost:3000}"

check() {
    local name="$1" cmd="$2"
    printf "%-20s" "$name"
    if eval "$cmd" 2>/dev/null; then
        echo "OK"
    else
        echo "FAIL"
    fi
}

check "vLLM" "curl -sf ${VLLM_BASE}/v1/models > /dev/null"
check "swerex-server" "curl -sf ${SWEREX_HOST}/ > /dev/null; [ \$? -eq 22 ]"
check "Push Gateway" "curl -sf ${PUSHGATEWAY_URL}/-/ready > /dev/null"
check "OTLP (HTTP)" "curl -sf -o /dev/null -w '' ${OTLP_ENDPOINT}/v1/traces; [ \$? -eq 22 ] || [ \$? -eq 0 ]"
check "Grafana" "curl -sf ${GRAFANA_URL}/api/health > /dev/null"
