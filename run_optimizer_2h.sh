#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STAMP="$(date +%Y%m%d_%H%M%S)"

CONFIG_PATH="${CONFIG_PATH:-$ROOT_DIR/optimizer_config.example.json}"
OUT_DIR="${1:-$ROOT_DIR/outputs_run_2h_${STAMP}}"
WORKERS="${WORKERS:-14}"
TARGET_HOURS="${TARGET_HOURS:-2.0}"
WALL_HOURS="${WALL_HOURS:-2.0}"

mkdir -p "$OUT_DIR"
LOG_PATH="$OUT_DIR/console.log"

echo "[run] config=$CONFIG_PATH" | tee -a "$LOG_PATH"
echo "[run] out=$OUT_DIR" | tee -a "$LOG_PATH"
echo "[run] workers=$WORKERS target_hours=$TARGET_HOURS wall_hours=$WALL_HOURS" | tee -a "$LOG_PATH"

python3 -u "$ROOT_DIR/run_variant_optimization.py" \
  --config "$CONFIG_PATH" \
  --out "$OUT_DIR" \
  --workers "$WORKERS" \
  --target-hours "$TARGET_HOURS" \
  2>&1 | tee -a "$LOG_PATH" &
RUN_PID=$!

python3 - "$RUN_PID" "$WALL_HOURS" <<'PY' &
import os
import signal
import sys
import time

pid = int(sys.argv[1])
wall_hours = float(sys.argv[2])
sleep_sec = max(1.0, wall_hours * 3600.0)
time.sleep(sleep_sec)
try:
    os.kill(pid, 0)
except OSError:
    raise SystemExit(0)
print(f"[timer] wall-clock limit reached ({wall_hours}h). Sending SIGINT to {pid}.", flush=True)
os.kill(pid, signal.SIGINT)
PY
TIMER_PID=$!

set +e
wait "$RUN_PID"
RUN_STATUS=$?
set -e

kill "$TIMER_PID" 2>/dev/null || true
wait "$TIMER_PID" 2>/dev/null || true

echo "[done] exit_code=$RUN_STATUS" | tee -a "$LOG_PATH"
echo "[done] outputs in $OUT_DIR" | tee -a "$LOG_PATH"
echo "[done] key files: run_state.json, progress.jsonl, variant_summary.csv, pareto_frontier.md, optimizer_report.md" | tee -a "$LOG_PATH"

exit "$RUN_STATUS"
