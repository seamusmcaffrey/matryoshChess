#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STAMP="$(date +%Y%m%d_%H%M%S)"

CONFIG_PATH="${CONFIG_PATH:-$ROOT_DIR/optimizer_config.example.json}"
OUT_DIR="${1:-$ROOT_DIR/outputs_run_open_${STAMP}}"

# Planning target only (not a hard cap). Increase/decrease as desired.
TARGET_HOURS="${TARGET_HOURS:-6.0}"
WORKERS="${WORKERS:-12}"

mkdir -p "$OUT_DIR"
LOG_PATH="$OUT_DIR/console.log"

echo "[run] mode=open (no wall-clock cap)" | tee -a "$LOG_PATH"
echo "[run] config=$CONFIG_PATH" | tee -a "$LOG_PATH"
echo "[run] out=$OUT_DIR" | tee -a "$LOG_PATH"
echo "[run] workers=$WORKERS target_hours=$TARGET_HOURS" | tee -a "$LOG_PATH"
echo "[run] stop with Ctrl+C (graceful partial-output preservation)" | tee -a "$LOG_PATH"

set +e
python3 -u "$ROOT_DIR/run_variant_optimization.py" \
  --config "$CONFIG_PATH" \
  --out "$OUT_DIR" \
  --workers "$WORKERS" \
  --target-hours "$TARGET_HOURS" \
  2>&1 | tee -a "$LOG_PATH"
RUN_STATUS=$?
set -e

echo "[done] exit_code=$RUN_STATUS" | tee -a "$LOG_PATH"
echo "[done] outputs in $OUT_DIR" | tee -a "$LOG_PATH"
echo "[done] key files: run_state.json, progress.jsonl, variant_summary.csv, pareto_frontier.md, optimizer_report.md" | tee -a "$LOG_PATH"

exit "$RUN_STATUS"
