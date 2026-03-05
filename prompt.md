You are an expert game systems designer + simulation engineer. You will design and implement an automated “variant optimization pass” for my Matryoshka Chess project.

Context
- We are exploring “Matryoshka Chess”: pieces demote (range-decay tiers), captured pieces can redeploy (“retaliation placement”), retaliation strike can cause permanent removal, king-capture may insta-kill, etc.
- Baseline study suggests draw rates around ~48–52% for current Matryoshka variants, with mean ~115–135 plies. We want a ruleset that is:
  1) fair (balanced win rates, no obvious first-move advantage beyond normal noise)
  2) fun (more decisive than chess; fewer draws; more tactical tension; not grindy)
  3) differentiated from standard chess and common variants
  4) still “learnable” (rules shouldn’t be wildly baroque)

Goal
Build a simulation/optimization harness that:
- Proposes a diverse family of candidate variants (not only ones we already tried)
- Runs batched self-play (or policy vs policy) studies per variant
- Tracks metrics, confidence intervals, and a composite “interestingness score”
- Surfaces a shortlist of “interesting spots” on the Pareto frontier (not a single winner)

Deliverables
1) A new script (or set of scripts) to generate a variant suite, run simulations, aggregate results, and produce:
   - a markdown table
   - a CSV summary
   - a “frontier” shortlist (top 10) with rationale
2) A schema/config format for defining variants (JSON or Python dataclass)
3) A metric glossary and clear definitions
4) A “draw forensics” output that tells us what draw means in practice (board characteristics), not just %
5) All code should be deterministic with seeds, reproducible, and runnable locally

Non-negotiables
- Keep implementation modular: variant definition → rules engine hooks → simulator → aggregator → reporting.
- Include a stable seeding strategy and stratified sampling (multiple seeds per variant).
- Report uncertainty (95% CI) for draw%, win%, mean plies, etc.
- Do not “overfit” to a single heuristic; output a Pareto set.

Core questions to answer in the report
A) Why is draw rate high in Matryoshka? What do drawn end positions look like?
B) Which rule levers reduce draws without making the game feel like Circe (i.e., too capture-crazy)?
C) Which variants are most differentiated from chess while still stable/fair?

Variant Search Space (you must go beyond these, but include them)
Define a parameterized family including at least these levers:

1) King “spice” (must test several; avoid the broken “sniper king” that increases draws for Matryoshka)
- king_move_mode:
  - normal (king moves/captures 1)
  - king_dash_k (king may move up to K squares in straight line if path squares are not attacked; captures remain 1)
  - king_k_range (king moves/captures like king but with max step 2 (Chebyshev distance 2) OR 3)
  - king_capture_line_k (king moves 1, but captures like rook/bishop/queen lines with max range K; path unobstructed; landing on capture square)
- king_capture_insta_kill: on/off (default on, but include off or “adjacent only”)

2) Range decay tiers (sliding pieces)
- tier model variants:
  - (full → 4 → 1 → pawn → removed)  [current]
  - (full → 5 → 2 → pawn → removed)
  - (full → 3 → 1 → pawn → removed)  [faster collapse]
  - continuous HP model (Q8→Q7→...→Q1→pawn) but bucketed for UI (optional)
- collapse target:
  - pawn
  - “crippled pawn” (pawn that cannot double-step or promote easily)
- collapse removal:
  - pawn captured = removed (current)
  - pawn captured = removed AND increments “doom counter” (see anti-draw)

3) Knights / non-sliders decay
- (knight → wazir → pawn → removed) [current]
- (knight → camel OR zebra-like reduced jump) (if supported)
- (knight → king-step diagonal only) as an alternate
If not supported by engine, propose a compatible approximation.

4) Retaliation redeploy (must test both with and without)
- retaliation_enabled: on/off
- retaliation_targeting:
  - highest-value piece that can be safely threatened (“safe from target piece only”)
  - highest-value reachable target within N squares (localization)
  - “target pool” = top 2 pieces by value, choose randomly among them if placeable
- “safe” definition: ALWAYS “safe from target piece only” (not global)
- tie-breaks:
  - random
  - maximize additional threatened value
  - minimize distance to enemy king
- fallback modes:
  - Circe square → random empty
  - Circe square → nearest empty to Circe square
  - “reserve pocket” (piece waits offboard 1 ply then tries again) [optional digital-only]

5) Retaliation Strike (perma-kill on next move)
- strike_window:
  - next move only (current)
  - next 2 moves
  - until target moves (expires once target moves)
- strike_effect:
  - perma-kill (no demotion)
  - extra demotion (demote 2 tiers instead of perma-kill)
- strike_requires_direct_capture: yes (keep yes)

6) Anti-draw packages (must test multiple)
- stalemate = loss
- repetition illegal (“Ko”): cannot recreate a previous position; if no legal move, lose
- “doom clock”: if X consecutive full moves with no permanent removal, apply a rule:
  - demote a random non-king piece on both sides
  - or force weakest-tier piece to collapse
  - or add +1 capture damage to the next capture
- remove 50-move rule vs keep vs replace with smaller number

7) Win conditions (test minimally; keep chess-like but consider one extra)
- standard checkmate only
- checkmate OR “king captured” (if your engine already treats capture as terminal)
- optional: “royal capture wins” if king is ever taken
(Do not introduce health-based king unless explicitly stated.)

Metrics to compute per variant (minimum)
- Win rate by color (white/black), draw rate
- Mean/median plies, distribution (p10/p50/p90)
- Captures per 100 plies
- Permanent removals per 100 plies (perma-kills + pawn removals + collapse-to-removed)
- Check rate: checks per 100 plies (if detectable)
- Swinginess: stddev of evaluation proxy (or material delta) over time (approx ok)
- Balance: |win_white - win_black| and |white_advantage|
- “Novelty” proxy: KL divergence of piece-type counts over time vs normal chess baseline (approx ok)
- “Board clog” proxy: mean number of pieces on board at ply N (e.g., 40, 80, 120)

Draw Forensics (required)
For every draw, log a compact “ending signature”:
- termination reason (stalemate / repetition / move-limit / insufficient-material / other)
- piece counts by tier and type at end
- whether kings were in check in last 10 plies (perpetual-like)
- last 20 plies SAN/coordinate moves
- a hash of final board + a small ASCII board snapshot
Aggregate:
- top 10 most common draw signatures
- “typical draw board states” as examples saved to a folder

Optimization Approach (must implement)
This is multi-objective. Do NOT just rank by a single score.
Implement:
1) A broad random / latin-hypercube sweep across the parameter space (e.g., 100–300 variants).
2) A second-stage local search around the best 10–20 (mutate parameters).
3) Report Pareto frontier for:
   - minimize draw%
   - minimize mean plies
   - maximize balance/fairness
   - maximize novelty proxy (within reason)
Also produce a single “interestingness score” for convenience, but treat it as secondary.

Suggested “interestingness score” (implement but keep tunable)
- Hard constraints (filter out):
  - draw% > 55% (unless novelty extremely high)
  - mean plies > 170
  - imbalance: |(white win%) - (black win%)| > 10 pts
- Score components (normalize 0..1):
  - decisiveness = 1 - draw%
  - pace = inverse of mean plies
  - tactics = captures/100 plies + checks/100 plies (capped)
  - novelty proxy
  - fairness penalty
Compute score and also show component breakdown.

Simulation details
- Use multiple random seeds per variant (e.g., 20 seeds × 200 games each, or whatever is feasible)
- Ensure both colors are symmetric (swap colors half the time)
- If using an AI policy, keep it consistent across variants; if using random/legal-move, note limitations
- Timebox per variant to avoid infinite loops; log timeouts separately

Implementation constraints
- Produce files in an outputs directory:
  - variant_summary.csv
  - variant_table.md
  - pareto_frontier.md
  - draw_forensics/...
  - raw_games.csv (optional, can be large)
- Ensure a single command can run:
  - python run_variant_optimization.py --config config.json --out outputs_run_YYYYMMDD
- Add a “small smoke run” mode for fast sanity checks.

Final report requirements
At the end of the run, write a short markdown report:
- Top 10 variants with metrics + why they’re interesting
- Pareto frontier plot (or table) + commentary
- Draw forensics: what draw boards look like, common causes
- Recommendations: 3 variants to iterate next, and which levers seem most impactful

Important: Propose new variant ideas beyond our current ones
Include at least 10 additional rule lever ideas, such as:
- localized retaliation (within radius)
- retaliations that spawn as “ghost threats” (marker) rather than piece teleport
- capture damage scaling with piece value
- “momentum” rules (capturer becomes temporarily vulnerable)
- forced trade resolution mechanics
These should be included in the suite if implementable, or described if not.

Now do the work:
- Inspect existing project structure
- Design the variant schema and rules hooks
- Implement the optimization harness
- Generate a first suite and run a smoke test
- Output the code + usage + an example config + the report template
Do not ask me questions unless absolutely blocked; make reasonable assumptions and document them.