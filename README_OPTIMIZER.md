# Variant Optimizer Harness

## Run (full, ~8h target)
```bash
python3 run_variant_optimization.py \
  --config optimizer_config.example.json \
  --out outputs_run_$(date +%Y%m%d_%H%M)
```

## Run (open / no wall cap)
```bash
WORKERS=12 TARGET_HOURS=6.0 ./run_optimizer_open.sh
```
Stop any time with `Ctrl+C`; partial outputs are preserved.

## Run (smoke)
```bash
python3 run_variant_optimization.py \
  --config optimizer_config.example.json \
  --out outputs_run_smoke_$(date +%Y%m%d_%H%M) \
  --smoke
```

## Live Progress / Partial Artifacts
- Terminal progress meter prints continuously during each variant:
  - per-variant `% complete` bar
  - per-stage `% complete` bar (games completed vs planned stage games)
- `progress.jsonl` (append-only event log)
- `run_state.json` (latest run status)
- `variants/<variant_id>/partial_summary.json` (updated during run)
- `variants/<variant_id>/games_partial.csv` (streaming game rows)
- `variants/<variant_id>/draw_forensics.jsonl`

## Final Aggregates
- `variant_summary.csv`
- `variant_table.md`
- `pareto_frontier.md`
- `optimizer_report.md`
- `metric_glossary.md`
- `draw_forensics/top_signatures.md`
