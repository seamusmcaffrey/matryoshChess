# Phase 2 Run Commands

## Step 1: Full sweep at depth 2 (~20 min)

All 9 configs, 200 games each, statistically powered.

```bash
WORKERS=12 SAMPLE_SCALE=0.50 TARGET_DEPTH=2 MAX_NODES=20000 SEARCH_NOISE=0.02 \
  ./run_optimizer_open.sh outputs_phase2_d2_full
```

## Step 2: Targeted depth 3 (optional, ~2-3 hrs)

Run after reviewing Step 1 results. Pick the 2-3 most interesting configs.

```bash
WORKERS=12 SAMPLE_SCALE=0.50 TARGET_DEPTH=3 MAX_NODES=80000 SEARCH_NOISE=0.02 \
  EXTRA_ARGS="--configs matryoshka_ret_baseline,matryoshka_no_ret,<best_retaliation_variant>" \
  ./run_optimizer_open.sh outputs_phase2_d3_targeted
```

## Reading results

```bash
cat outputs_phase2_d2_full/study_report.md
cat outputs_phase2_d2_full/study_summary.csv
cat outputs_phase2_d2_full/run_state.json
```
