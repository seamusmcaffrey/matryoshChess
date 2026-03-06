# Phase 4: Corrected Design Run (Depth 3)

Kill any running phase4 first:
```bash
pkill -f "outputs_phase4" 2>/dev/null
```

Then run (~2 hours):
```bash
cd /Users/seamus/Documents/prometheus/matryoshChess

WORKERS=12 SAMPLE_SCALE=0.25 TARGET_DEPTH=3 MAX_NODES=80000 SEARCH_NOISE=0.02 \
  EXTRA_ARGS="--configs v4_baseline,v4_window2,v4_any_target,v4_no_ret,v4_no_king_permakill,v4_defender_strike --force-extended --allow-high-draw" \
  ./run_optimizer_open.sh outputs_phase4_corrected_design
```
