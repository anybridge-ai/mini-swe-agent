#!/usr/bin/env bash
# Stop and remove all local infrastructure containers.
set -euo pipefail

CONTAINERS="mswea-swerex mswea-pushgateway mswea-tempo mswea-grafana mswea-prometheus"

echo "Stopping containers..."
docker stop $CONTAINERS 2>/dev/null || true

echo "Removing containers..."
docker rm $CONTAINERS 2>/dev/null || true

echo "Cleaning up Tempo data..."
rm -rf /tmp/tempo

echo "Done."
