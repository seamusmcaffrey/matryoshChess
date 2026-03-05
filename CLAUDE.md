# Claude Runtime Notes

## Optimizer Throughput Record (2026-03-05)
- Host: MacBook Pro M4 Max.
- Run mode: open/no-cap launcher.
- Invocation:
  - `WORKERS=12 TARGET_HOURS=6.0 ./run_optimizer_open.sh`
- Observed checkpoint around 40 minutes:
  - `stage1` near `55/120` variants complete
  - roughly `38.5k / 84k` stage1 games complete (`~45.8%`)
  - per-variant runtime at this phase approximately `~20-30` seconds for 700 games
- Interpretation:
  - The planner’s `TARGET_HOURS=6.0` budget is conservative for this hardware.
  - It is feasible to gather substantial useful data faster than the nominal target window.

# NEVER MODIFY RULES 

Never modify /Users/seamus/Documents/prometheus/matryoshChess/RULES.md