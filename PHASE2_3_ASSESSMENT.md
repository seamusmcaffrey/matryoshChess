# Matryoshka Chess: Engine Upgrade Assessment

Phases 2-3 results from the v2 engine (alpha-beta depth 2, PSTs, iterative deepening).
3,060 games across 17 configurations. 2026-03-05.

---

# The Engine Is Now Credible

The old 1-ply engine with 22% random moves produced 93% draws in normal chess — useless as a reference. The v2 engine produces **26% draws in normal chess**, consistent with real-world engine data (TCEC: 50-75% at top level, lower at moderate strength). Findings from this engine are directionally trustworthy.

| Engine | Normal Chess Draws | Matryoshka Draws | Credible? |
|---|---:|---:|---|
| v1 (1-ply, 22% random) | 93% | 61% | No |
| v2 (alpha-beta depth 2) | 26% | 0.5-4% | Yes |

---

# Finding 1: Degradation Is the Foundation

The piece degradation system (full -> damaged -> crippled -> pawn) is the most impactful mechanic in the game. With zero other Matryoshka features enabled:

| | Normal Chess | Degradation Only |
|---|---:|---:|
| Draw rate | 26.0% | 1.5-4.0% |
| Captures per 100 plies | 16.6 | 34-36 |
| Material volatility | 23.8 | 27-29 |
| Mean plies | 70 | 66-72 |

Degradation alone cuts draws by 22 percentage points and doubles capture density. Games stay roughly the same length but are far more eventful. Pieces persist on the board in weakened form, creating a crowded mid-game with many tactical interactions.

**This mechanic works.** It creates a genuinely different game from chess regardless of what else is layered on top.

---

# Finding 2: Safe Retaliation Was a Dead Letter

The Phase 1 optimizer's "retaliation" mechanic required placing the demoted piece on a square where it could attack an opponent's high-value piece AND not be attacked back. In practice, with degraded piece ranges (tier 2 = 5 squares, tier 3 = 1 square), this almost never succeeded:

| Metric | Safe Retaliation | Result |
|---|---|---|
| Pieces landing on targeting squares | 3-8% | Almost never fires |
| Pieces falling back to Circe (home) squares | 70% | The default behavior |
| Pieces placed randomly | 22-25% | More common than targeting |
| Retaliation strikes per game | 0.2-0.5 | Less than once per game |
| Threat moves available per game | 0.6-1.0 | Barely registers |

The game's narrative hook — "pieces don't die, they get angry and fight back" — was mechanically inert. Pieces were respawning on their home squares 70% of the time, playing almost identically to Circe chess with extra steps.

**The safety requirement killed the mechanic.** A demoted piece with range 1-5 almost never finds a square that threatens a high-value target while being safe from counterattack.

---

# Finding 3: Unsafe Retaliation Brings the Mechanic to Life

Removing the safety check (pieces land on attacking squares even if threatened back) transforms the game:

| Metric | Safe Targeting | Highest Unsafe | Any Unsafe |
|---|---:|---:|---:|
| Targeting placement rate | 3.9% | 45.2% | 100% |
| Circe fallback rate | 69.6% | 37.8% | 0% |
| Strike rate (per capture) | 1.4% | 17.2% | 38.9% |
| Permanent kills via strike/game | 0.26 | 2.27 | 3.83 |
| Threat moves available/game | 0.9 | 5.6 | 6.9 |
| Material volatility | 25.8 | 33.3 | 44.6 |
| Mean plies | 58 | 61 | 40 |
| Interestingness | 0.838 | 0.837 | 0.946 |

Two modes emerged:

**`any_unsafe`** — Maximum chaos. Every piece lands on an attacking square (100%). Strikes happen on 39% of captures. Games are 40 plies (~20 moves/side). Material volatility doubles. This is the "every capture has consequences" version. Very fast, very violent.

**`highest_unsafe`** — Controlled aggression. Pieces target only the opponent's most valuable piece. 45% land on attacking squares. Strikes happen on 17% of captures (~2.3 per game). Games are 61 plies (~30 moves/side). Material swing is elevated but not chaotic. This preserves more strategic depth.

---

# Finding 4: The Structural Locks May Be Overpowered

Phase 1 identified `stalemate_is_loss` and `king_capture_line` as the dominant anti-draw levers. Phase 2 locked both in. The result: every Matryoshka configuration produces 0.5-4% draws, regardless of other settings.

This is too decisive. For reference:
- Standard chess (human GM): 25-35% draws
- Chess960: ~22% draws
- Crazyhouse: ~5-15% draws
- Atomic chess: ~5% draws
- Matryoshka (any config): 0.5-4% draws

When draws are this rare, there's no room for retaliation tuning to show its impact on decisiveness. The structural locks are doing all the work. Testing with relaxed locks (stalemate = draw, normal king movement) showed that degradation alone still produces only 1.5% draws — the game is inherently decisive even without the locks.

**Implication**: `stalemate_is_loss` and `king_capture_line` can be dialed back or removed. The degradation system provides enough decisiveness on its own, and removing these locks creates a simpler ruleset.

---

# Finding 5: Retaliation Parameter Comparison (Full Locks)

All retaliation variants tested with full structural locks (Phase 2):

| Config | Draw% | Plies | Strikes/Game | Volatility | Score |
|---|---:|---:|---:|---:|---:|
| ret_close_fast (r=2, w=1) | 1.5% | 60 | 0.50 | 25.9 | 0.892 |
| ret_king_dash | 1.0% | 55 | 0.25 | 24.3 | 0.883 |
| ret_plus_doom (doom=32) | 4.0% | 63 | 0.31 | 27.3 | 0.878 |
| ret_aggressive_targeting | 0.5% | 51 | 0.44 | 22.5 | 0.865 |
| ret_wide_long (r=5, w=3) | 1.5% | 60 | 0.21 | 27.0 | 0.846 |
| matryoshka_ret_baseline | 0.5% | 56 | 0.24 | 24.5 | 0.806 |

With safe targeting, parameter tuning barely matters — all configs produce similar outcomes because the mechanic rarely fires. The differences are within noise.

---

# Finding 6: Playstyle Diversity Works

Testing aggressive vs defensive vs balanced engine profiles on the best retaliation config:

| Matchup | Draw% | White Win% | Black Win% | Plies |
|---|---:|---:|---:|---:|
| Balanced vs Balanced | 4.2% | 42.5% | 53.3% | 67 |
| Aggressive vs Balanced | 2.5% | 36.7% | 60.8% | 69 |
| Aggressive vs Defensive | 1.7% | 45.0% | 53.3% | 71 |

Black has a slight advantage across all matchups (53-61% win rate). The aggressive profile doesn't dominate — defensive play is viable, producing the longest games. This suggests the game has room for different strategic approaches.

**Note**: The Black advantage may be an artifact of the engine or the specific locked rules (king_capture_line may favor the defender). Worth investigating but not alarming.

---

# The Complete Picture

| Game Version | Draw% | Plies | Captures/100 | Strikes/Game | Volatility | Character |
|---|---:|---:|---:|---:|---:|---|
| Normal chess | 26% | 70 | 17 | — | 24 | Strategic, slow |
| Degradation only | 1.5-4% | 66-72 | 34-36 | 0 | 27-29 | Attritional grind |
| + Safe retaliation | 0.5-2.5% | 55-59 | 37 | 0.2-0.5 | 24-26 | Same as above (mechanic doesn't fire) |
| + Highest unsafe ret. | 4.5% | 61 | 29 | 2.3 | 33 | Controlled chaos |
| + Any unsafe ret. | 1.5% | 40 | 37 | 3.8 | 45 | Maximum chaos |

---

# Design Questions to Resolve

## 1. How chaotic should the game be?

`any_unsafe` creates the most dramatic version — every capture triggers a revenge placement, strikes happen constantly, games are short and volatile. But 40-ply games (~20 moves/side) may be too short for meaningful strategy. `highest_unsafe` is more measured (61 plies, 2.3 strikes/game) but still creates real retaliation events.

The choice is between "party game energy" (any_unsafe) and "serious variant energy" (highest_unsafe).

## 2. Should placed pieces be vulnerable?

The current unsafe mode places pieces where they CAN be immediately recaptured. This creates a "sacrifice for a strike" dynamic — you lose the piece but get a retaliation strike opportunity. Is that fun, or is it just noise? Players might feel the placement is pointless if the piece immediately dies.

An alternative: place unsafely but give the piece one move of invulnerability (like Crazyhouse's drop immunity to capturing).

## 3. Do we keep the structural locks?

The data shows degradation alone produces 1.5-4% draws — the game is inherently decisive. `stalemate_is_loss` and `king_capture_line` are unnecessary for decisiveness and add rule complexity. Removing them simplifies the game (closer to RULES.md) while keeping the core character.

Counter-argument: `king_capture_line` creates the "king as executioner" endgame hook — the king charges in to permanently remove degraded pieces. Losing this removes a distinctive feature.

## 4. Is the strike window the right mechanic?

Currently: captured piece is placed, opponent has N moves to "strike back" (capture the retaliation target) for a permanent kill. With unsafe placement, this fires 17-39% of the time. But it's complex to explain and track.

Simpler alternative: the placed piece simply threatens whatever it attacks, and any capture it makes in its next move is a permanent kill. No window, no target tracking — just "angry pieces kill what they hit."

## 5. What's the minimum viable ruleset?

Based on the data, the core game needs:
- Standard chess board and setup
- Piece degradation on capture (full -> damaged -> crippled -> pawn)
- Unsafe retaliation placement (captured piece respawns threatening an enemy)
- Some form of permanent removal (strikes, king captures, or both)

Everything else (doom clock, stalemate_is_loss, king_capture_line, ko repetition) is optional seasoning. The simpler the rules, the more adoptable the game.

---

# Recommended Next Steps

## If exploring further:

1. **Test `highest_unsafe` with strike windows 2-4** under relaxed locks. This is the best candidate for a balanced game — targeted enough to create drama, paced enough for strategy.

2. **Test a "one move of immunity" variant** — placed piece can't be captured on the opponent's immediate next move. This ensures the retaliation placement actually matters rather than being immediately recaptured.

3. **Test at depth 3** for the top 2-3 candidates. The depth-2 engine may undervalue retaliation because it can't fully perceive multi-move strike sequences. Depth 3 would confirm whether the findings hold with deeper tactical play. Estimated ~2-3 hours for 3 configs.

## If making design decisions now:

The data supports a game built on:
- **Degradation** as the backbone (proven, massive impact)
- **`highest_unsafe` retaliation** as the signature mechanic (2+ strikes/game, 45% targeted placement, 61-ply games)
- **Relaxed structural locks** (stalemate = draw, normal king movement) for simplicity
- **Permanent kill on retaliation strike** as the payoff for the mechanic

This produces a game that's dramatically more decisive than chess (4.5% draws), tactically dense (29-37 captures/100 plies), and creates 2+ "revenge" moments per game — enough to be a consistent feature of play without overwhelming strategic planning.

---

# Data Sources

| Run | Configs | Games/Config | Output |
|---|---:|---:|---|
| Phase 2 Core | 3 | 100-200 | `outputs_phase2_d2_full/` |
| Phase 2 Full | 11 | 100-200 | `outputs_phase2_d2_all/` |
| Phase 3 Retaliation | 6 | 200 | `outputs_phase3_retaliation_isolation/` |
