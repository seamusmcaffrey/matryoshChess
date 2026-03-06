# Phase 2 Run Commands

## Step 1 (DONE): Core 3 configs at depth 2

Results in `outputs_phase2_d2_full/`. Normal chess 26% draws, matryoshka no-ret 4%, matryoshka ret 0.5%.

## Step 2: All retaliation variants + playstyle diversity (~25 min)

Gate logic fixed. This will run core 3 + extended 5 + playstyle 3 = all 11 configs.

```bash
WORKERS=12 SAMPLE_SCALE=0.50 TARGET_DEPTH=2 MAX_NODES=20000 SEARCH_NOISE=0.02 \
  ./run_optimizer_open.sh outputs_phase2_d2_all
```

## Step 3: Targeted depth 3 (optional, overnight)

Run after reviewing Step 2 results. Pick the 2-3 most interesting configs.

```bash
WORKERS=12 SAMPLE_SCALE=0.50 TARGET_DEPTH=3 MAX_NODES=80000 SEARCH_NOISE=0.02 \
  EXTRA_ARGS="--configs matryoshka_ret_baseline,matryoshka_no_ret,<best_retaliation_variant>" \
  ./run_optimizer_open.sh outputs_phase2_d3_targeted
```

## Reading results

```bash
cat outputs_phase2_d2_all/study_report.md
cat outputs_phase2_d2_all/study_summary.csv
cat outputs_phase2_d2_all/run_state.json
```
