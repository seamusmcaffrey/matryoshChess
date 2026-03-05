# Phase 2 Optimizer Harness

## Run (open / no wall cap)
```bash
WORKERS=12 SAMPLE_SCALE=0.25 TARGET_DEPTH=3 MAX_NODES=80000 ./run_optimizer_open.sh
```

## Run (2h wall cap)
```bash
WORKERS=12 WALL_HOURS=2.0 SAMPLE_SCALE=0.25 TARGET_DEPTH=3 MAX_NODES=80000 ./run_optimizer_2h.sh
```

## Run (smoke sanity)
```bash
python3 run_phase2_study.py \
  --out outputs_phase2_smoke_$(date +%Y%m%d_%H%M) \
  --smoke
```
Stop any time with `Ctrl+C`; partial outputs are preserved.

## Live Progress / Partial Artifacts
- Terminal progress meter prints continuously during each config.
- `progress.jsonl` (append-only event log)
- `run_state.json` (latest run status)
- `config_<name>/summary.json` (per-config metrics)
- `config_<name>/games.jsonl` (raw per-game rows)
- `config_<name>/retaliation_analysis.md` (when retaliation is enabled)

## Final Aggregates
- `study_summary.csv`
- `study_report.md`
