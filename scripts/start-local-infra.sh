#!/usr/bin/env bash
# Start local observability infrastructure (Push Gateway, Tempo, Grafana)
# and swerex server.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
SWEREX_AUTH_TOKEN="${SWEREX_AUTH_TOKEN:-test123}"

echo "=== Building swerex-server image ==="
docker build -t swerex-server "$PROJECT_ROOT/docker/swerex-server/"

echo "=== Starting swerex-server (:8000) ==="
docker run -d \
  -p 8000:8000 \
  --name mswea-swerex \
  swerex-server \
  --auth-token "$SWEREX_AUTH_TOKEN"

echo "=== Starting Push Gateway (:9091) ==="
docker run -d \
  --name pushgateway \
  -p 9091:9091 \
  prom/pushgateway:latest

echo "=== Starting Grafana Tempo (:3200, :4317, :4318) ==="
mkdir -p /tmp/tempo
cat > /tmp/tempo/tempo.yaml << 'EOF'
server:
  http_listen_port: 3200

distributor:
  receivers:
    otlp:
      protocols:
        http:
          endpoint: "0.0.0.0:4318"
        grpc:
          endpoint: "0.0.0.0:4317"

storage:
  trace:
    backend: local
    wal:
      path: /var/tempo/wal
    local:
      path: /var/tempo/blocks
EOF

docker run -d \
  --name tempo \
  -p 3200:3200 \
  -p 4317:4317 \
  -p 4318:4318 \
  -v /tmp/tempo/tempo.yaml:/etc/tempo.yaml \
  grafana/tempo:latest \
  -config.file=/etc/tempo.yaml

echo "=== Starting Grafana (:3000) ==="
docker run -d \
  --name grafana \
  -p 3000:3000 \
  -e GF_AUTH_ANONYMOUS_ENABLED=true \
  -e GF_AUTH_ANONYMOUS_ORG_ROLE=Admin \
  grafana/grafana:latest

echo ""
echo "Waiting for services to start..."
sleep 3

echo ""
"$SCRIPT_DIR/health-check.sh"
