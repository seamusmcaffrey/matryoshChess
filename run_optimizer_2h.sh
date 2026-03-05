#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STAMP="$(date +%Y%m%d_%H%M%S)"

OUT_DIR="${1:-$ROOT_DIR/outputs_phase2_2h_${STAMP}}"
WORKERS="${WORKERS:-14}"
SAMPLE_SCALE="${SAMPLE_SCALE:-0.25}"
MAX_PLIES="${MAX_PLIES:-300}"
TARGET_DEPTH="${TARGET_DEPTH:-3}"
MAX_NODES="${MAX_NODES:-80000}"
SEARCH_NOISE="${SEARCH_NOISE:-0.02}"
SEED="${SEED:-42}"
WALL_HOURS="${WALL_HOURS:-2.0}"
EXTRA_ARGS="${EXTRA_ARGS:-}"

mkdir -p "$OUT_DIR"
LOG_PATH="$OUT_DIR/console.log"

echo "[run] mode=phase2-2h" | tee -a "$LOG_PATH"
echo "[run] out=$OUT_DIR" | tee -a "$LOG_PATH"
echo "[run] workers=$WORKERS sample_scale=$SAMPLE_SCALE wall_hours=$WALL_HOURS" | tee -a "$LOG_PATH"
echo "[run] depth=$TARGET_DEPTH max_nodes=$MAX_NODES noise=$SEARCH_NOISE max_plies=$MAX_PLIES seed=$SEED" | tee -a "$LOG_PATH"

python3 -u "$ROOT_DIR/run_phase2_study.py" \
  --out "$OUT_DIR" \
  --workers "$WORKERS" \
  --sample-scale "$SAMPLE_SCALE" \
  --max-plies "$MAX_PLIES" \
  --target-depth "$TARGET_DEPTH" \
  --max-nodes "$MAX_NODES" \
  --noise "$SEARCH_NOISE" \
  --seed "$SEED" \
  $EXTRA_ARGS \
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
echo "[done] key files: run_state.json, progress.jsonl, study_summary.csv, study_report.md" | tee -a "$LOG_PATH"

exit "$RUN_STATUS"
