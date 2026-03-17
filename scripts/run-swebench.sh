#!/usr/bin/env bash
# Run SWE-bench lite with observability.
# Usage: ./scripts/run-swebench.sh [--slice 0:10] [--workers 10] [--redo]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Defaults
SLICE="${SLICE:-0:10}"
WORKERS="${WORKERS:-10}"
SUBSET="${SUBSET:-lite}"
SPLIT="${SPLIT:-dev}"
OUTPUT_DIR="${OUTPUT_DIR:-$PROJECT_ROOT/results/swebench_run}"
REDO=""

VLLM_BASE="${VLLM_BASE:-http://localhost:8066/v1}"
MODEL="${MODEL:-openai/Qwen/Qwen2.5-32B-Instruct}"

# Parse args
while [[ $# -gt 0 ]]; do
    case "$1" in
        --slice)   SLICE="$2"; shift 2 ;;
        --workers) WORKERS="$2"; shift 2 ;;
        --subset)  SUBSET="$2"; shift 2 ;;
        --split)   SPLIT="$2"; shift 2 ;;
        --output)  OUTPUT_DIR="$2"; shift 2 ;;
        --redo)    REDO="--redo-existing"; shift ;;
        *)         echo "Unknown arg: $1"; exit 1 ;;
    esac
done

export OPENAI_API_KEY="${OPENAI_API_KEY:-EMPTY}"
export MSWEA_PUSHGATEWAY_URL="${MSWEA_PUSHGATEWAY_URL:-http://localhost:9091}"
export MSWEA_OTLP_ENDPOINT="${MSWEA_OTLP_ENDPOINT:-http://localhost:4318}"

source "$PROJECT_ROOT/.venv/bin/activate"

echo "SWE-bench run: subset=$SUBSET split=$SPLIT slice=$SLICE workers=$WORKERS"
echo "Model: $MODEL"
echo "vLLM: $VLLM_BASE"
echo "Output: $OUTPUT_DIR"
echo "---"

python -m minisweagent.run.benchmarks.swebench \
  --subset "$SUBSET" \
  --split "$SPLIT" \
  --slice "$SLICE" \
  -w "$WORKERS" \
  $REDO \
  -m "$MODEL" \
  -c swebench.yaml \
  -c "model.model_kwargs.api_base=$VLLM_BASE" \
  -c model.model_kwargs.drop_params=true \
  -c model.model_kwargs.temperature=0.0 \
  -c model.cost_tracking=ignore_errors \
  -o "$OUTPUT_DIR"
