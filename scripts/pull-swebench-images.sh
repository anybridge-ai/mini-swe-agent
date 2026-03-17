#!/usr/bin/env bash
# Pre-pull SWE-bench Docker images for a given slice.
# Usage: ./scripts/pull-swebench-images.sh [--subset lite] [--slice 0:10]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

SUBSET="${1:-lite}"
SLICE="${2:-0:10}"

source "$PROJECT_ROOT/.venv/bin/activate"

echo "Fetching image list for subset=$SUBSET slice=$SLICE ..."

IMAGES=$(python -c "
from datasets import load_dataset

DATASET_MAPPING = {
    'full': 'princeton-nlp/SWE-Bench',
    'verified': 'princeton-nlp/SWE-Bench_Verified',
    'lite': 'princeton-nlp/SWE-Bench_Lite',
}
ds = list(load_dataset(DATASET_MAPPING['$SUBSET'], split='dev'))
values = [int(x) if x else None for x in '$SLICE'.split(':')]
ds = ds[slice(*values)]
for inst in ds:
    iid = inst['instance_id'].replace('__', '_1776_')
    print(f'swebench/sweb.eval.x86_64.{iid}:latest'.lower())
")

echo "Pulling $(echo "$IMAGES" | wc -l) images in parallel..."

for img in $IMAGES; do
    docker pull "$img" &
done
wait

echo "All pulls complete."
