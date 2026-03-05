# Agent Run Notes

## Runtime Calibration (2026-03-05)
- Hardware: MacBook Pro M4 Max.
- Command used:
  - `cd /Users/seamus/Documents/prometheus/matryoshChess && WORKERS=12 TARGET_HOURS=6.0 ./run_optimizer_open.sh`
- Empirical checkpoint after ~40 minutes:
  - stage: `stage1`
  - progress: approximately variant `55/120` completed and variant `56/120` in progress
  - stage games progress around `~38,500 / 84,000` (`~45.8%`)
  - typical per-variant completion at this point: roughly `~20s-30s` for a 700-game variant
- Practical note:
  - The planning `TARGET_HOURS=6.0` appears conservative under this throughput.
  - Current observed pace suggests materially more coverage can be achieved in less wall-clock time.
