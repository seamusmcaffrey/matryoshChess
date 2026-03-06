#!/bin/bash
OUT="/Users/seamus/Documents/prometheus/matryoshChess/outputs_phase2_d2_all"
LOG="$OUT/monitor.log"

while true; do
    STATUS=$(python3 -c "import json; d=json.load(open('$OUT/run_state.json')); print(d.get('status','running'))" 2>/dev/null)
    if [ "$STATUS" = "complete" ]; then
        echo "[monitor $(date +%H:%M:%S)] Run complete!" >> "$LOG"
        echo "DONE" >> "$LOG"
        break
    fi
    # Log current state
    python3 -c "
import json, os
try:
    d = json.load(open('$OUT/run_state.json'))
    cfg = d.get('active_config', '?')
    done = d.get('completed_games', 0)
    total = d.get('total_games', 0)
    elapsed = d.get('elapsed_sec', 0)
    execs = d.get('executed_configs', [])
    print(f'[monitor] config={cfg} {done}/{total} elapsed={elapsed:.0f}s completed_configs={len(execs)}')
except:
    print('[monitor] waiting...')
" >> "$LOG" 2>&1
    sleep 120
done
