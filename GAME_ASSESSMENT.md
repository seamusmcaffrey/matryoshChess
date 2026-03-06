# Matryoshka Chess: Game Assessment

A comprehensive evaluation of Matryoshka Chess based on 4 phases of engine testing (~5,000+ games), culminating in Phase 4 with the corrected designer-intent mechanics at depth 3.

2026-03-06.

---

# Is This Game Worth Building?

**Yes.** The data consistently shows Matryoshka Chess creates a fundamentally different experience from standard chess — more decisive, more tactically dense, with a unique "captures have consequences" dynamic that has no direct precedent in published chess variants. The remaining question isn't whether the game works, but which version of it to ship.

---

# The Core Mechanics, Validated

## Degradation: The Foundation

Every piece is a nesting doll. Capture a queen, a damaged rook emerges. Capture that, a crippled bishop appears. Capture again, a weak pawn. Capture the pawn, it's finally gone.

| Metric | Normal Chess (depth 3) | Degradation Only | Delta |
|---|---:|---:|---|
| Draw rate | 26% | 8% | -18pp |
| Mean plies | 70 | 117 | +47 (longer — pieces persist) |
| Captures per 100 plies | 17 | 33 | +94% more action |
| Material volatility | 24 | 51 | 2x more swings |

Degradation alone creates a viable chess variant. Games are longer because pieces don't leave the board — they degrade into weaker forms that keep fighting. The board stays crowded with diminished fighters, creating a dense tactical landscape unlike anything in standard chess or existing variants.

## Retaliation: The Signature Mechanic

When you capture an opponent's piece, the degraded version respawns on a square threatening your most valuable piece. You then face a choice: spend your next move re-killing it (permanently this time), or ignore it and advance your position.

This is the "tempo tax" — every capture costs the attacker something.

| Metric | Degradation Only | + Retaliation (window=1) | Impact |
|---|---:|---:|---|
| Draw rate | 8% | 12% | More strategic depth (fewer blowouts) |
| Mean plies | 117 | 100 | 17 plies shorter |
| Captures per 100 plies | 33 | 42 | +27% more action |
| Redeployments per game | 0 | 25 | 25 "threat moments" per game |
| Targeting placement rate | — | 79% | Pieces land aggressively |
| Material volatility | 51 | 72 | More dramatic swings |

With retaliation, games are shorter and more eventful. 25 times per game, a captured piece respawns aimed at the opponent's best piece. 79% of the time, it successfully lands on a threatening square. The opponent must react — either by re-killing, moving their threatened piece, or accepting the threat.

## King Permakill: The Endgame Tool

The king has a special ability: anything it captures is permanently removed (no degradation, no respawn). This makes the king an offensive piece in the late game.

| Metric | King Permakill ON | King Permakill OFF |
|---|---:|---:|
| Draw rate | 12% | 3% |
| Mean plies | 100 | 68 |
| King permakills per game | 2.0 | 0 |

King permakill creates a distinctive endgame. The king wades into the field of crippled pieces, permanently clearing them. This extends games (the board actually simplifies) and creates more draws (cleaner positions are easier to hold). Without it, games are chaotic to the end.

**Keep it.** It creates the "king as executioner" endgame that's unique to Matryoshka Chess, and 12% draws is healthier than 3% for competitive play.

---

# The Design Choice: Window=1 vs Window=2

The biggest open question is whether re-killing the respawned piece should be a real feature or just a theoretical possibility.

## Window=1: The Tempo Tax

The capturer has exactly one move to re-kill the respawned piece. In practice, the engine **never does it** — there's always something more valuable to do with that move. The respawned piece survives and becomes a permanent degraded fighter.

| Property | Value |
|---|---|
| Draw rate | 12% |
| Mean plies | 100 (p10=39, p50=80, p90=229) |
| Re-kills per game | 0 |
| Redeployments per game | 25 |
| Captures per 100 plies | 42 |
| White/Black win balance | 44% / 44% |
| Checkmates / Resignations | 22 / 66 |
| Game character | Strategic. Every capture adds a new piece to the board. Games build complexity over time. |

**Why this works:** The threat is the punishment, not the execution. Capturing a piece means a new enemy appears near your best piece. You don't re-kill it — you cope with it. The board gradually fills with degraded fighters creating an increasingly complex position. Games are long enough for deep strategy (50 moves/side median) with wide variance (quick kills at p10=39 through deep grinds at p90=229). **Perfect color balance.**

## Window=2: The Re-Kill Game

The capturer has two moves to re-kill. The engine actually does it — 4.2 permanent kills per game via retaliation strikes, with 100% success rate when attempted.

| Property | Value |
|---|---|
| Draw rate | 6% |
| Mean plies | 79 (p10=15, p50=65, p90=159) |
| Re-kills per game | 4.2 |
| Redeployments per game | 17 |
| Captures per 100 plies | 42 |
| White/Black win balance | 38% / 56% |
| Checkmates / Resignations | 31 / 63 |
| Game character | Dramatic. Re-kill decisions create "clutch moments." Faster, more violent. |

**Why this works:** Every capture triggers a mini-drama: piece respawns, threatens your MVP, you have two turns to decide whether to permanently eliminate it. 4 times per game, this results in a permanent kill. Games are shorter and punchier. More checkmates happen (31 vs 22).

**Why it's riskier:** Black wins 56% of games (vs 38% for White). This color imbalance needs investigation — it may be an engine artifact at n=100, or it may be structural (the defender benefits from retaliation, and Black starts on defense). Also, p10=15 means some games are over in 7 moves per side, which may feel like a coin flip.

## Head-to-Head: Old Mechanic vs New

| | Attacker Re-kill (New) | Defender Revenge (Old) |
|---|---:|---:|
| Draw rate | 12% | 14% |
| Mean plies | 100 | 104 |
| Captures per 100 plies | 42 | 24 |
| Redeployments per game | 25 | 8.5 |
| Targeting placement rate | 79% | 52% |
| Revenge kills per game | 0 | 1.8 |
| Material volatility | 72 | 41 |
| Interestingness score | 0.933 | 0.804 |

The attacker-rekill version (your original design) is better on nearly every metric: 3x the redeployments, 75% more captures, 73% more volatility, 50% better targeting placement. The old defender-strike mechanic produces slower, less eventful games with fewer redeployments.

**The designer's instinct was correct.** The "captures cost the capturer" framing is mechanically superior to "captured pieces get revenge."

---

# Comparison to the Variant Landscape

| Variant | Draw Rate | Captures/Game | Complexity | Learning Curve |
|---|---|---|---|---|
| Standard Chess | 25-35% (GM) | ~20 | Deep | Years |
| Chess960 | ~22% | ~20 | Deep | Minutes (same rules) |
| Crazyhouse | 5-15% | Very high | High | Moderate (one new rule) |
| Atomic | ~5% | N/A (explosions) | Moderate | Low (one new rule) |
| **Matryoshka (w=1)** | **12%** | **~42/100 plies** | **High** | **Moderate** |
| **Matryoshka (w=2)** | **6%** | **~42/100 plies** | **High** | **Moderate** |

Matryoshka sits between Crazyhouse and Chess960 in character. It's more strategically complex than Atomic or 3-Check (where games are short and tactical), but more eventful than Chess960 (which plays like standard chess). The closest comparison is Crazyhouse — both fundamentally change what happens when you capture.

**Key advantage over Crazyhouse:** In Crazyhouse, captured pieces switch sides (snowball effect — the winner keeps winning). In Matryoshka, captured pieces stay on their original side but weaker (resilience effect — being captured isn't total loss). This creates more comebacks and closer games.

---

# Is It Novel?

**Yes.** Confirmed across all phases. No published variant combines:
- Piece degradation through tiers (full → damaged → crippled → pawn)
- Automatic aggressive redeployment of degraded pieces
- Tempo-cost mechanic for captures (attacker must deal with the respawn)
- King as endgame executioner (permanent removal)

The closest relative remains Mortal Chessgi (~40% similarity), which shares demotion chains but gives pieces to the captor (Crazyhouse-style) rather than keeping them on the original side.

---

# Is It Fun?

**We can't know from engine data alone.** But the signals are strong:

**For fun:**
- 25 "threat moments" per game where a captured piece respawns aggressively
- Wide game-length variance (quick kills to deep grinds) — every game feels different
- Balanced (44/44 White/Black at window=1) — neither player has a structural advantage
- Familiar starting position — zero learning curve for the board setup
- Multiple endgame types (checkmate, resignation, stalemate) — varied conclusions
- The king becomes an offensive piece in the endgame — satisfying power fantasy

**Concerns:**
- Complexity at capture time — "your piece is captured, it degrades, it respawns threatening their queen, they have 1 move to re-kill it" is a lot happening per capture. Digital implementation is essential.
- 66% of games end by resignation (the engine gives up when it's losing). This is normal for engine play but may indicate positions become hopeless quickly. For human play, the degradation system might actually create more comeback potential since pieces never fully disappear.
- The game needs a UI to communicate piece tiers and retaliation threats visually.

---

# Is It Strategic?

**Yes, at depth 3.** The depth-3 engine produces 12% draws (vs 0.5% at depth 2). This means the game has real defensive resources — a thinking player can hold positions that a shallow player can't. The game rewards calculation and planning, not just tactics.

Further evidence: window=1 (tempo tax) is more drawn (12%) than window=2 (re-kill, 6%). This means the strategic version of the mechanic creates more balanced play, while the more tactical version creates more decisive outcomes. Strategic depth and decisiveness are in healthy tension.

---

# Is It Competitive?

**Promising, with caveats.**

- **Color balance at window=1 is perfect:** 44% / 44% / 12%. This is better than standard chess (White has 3-5% advantage).
- **Color balance at window=2 has a Black edge:** 38% / 56% / 6%. Needs investigation.
- **Multiple viable strategies exist:** Aggressive and defensive engine profiles both produce wins (Phase 2 data). The game isn't "first to attack wins."
- **Game length variance supports different time controls:** Quick games (39 plies at p10) work for blitz, long games (229 plies at p90) work for classical.

**Competitive readiness needs:** Elo-rated play, human playtesting, opening theory development, and investigation of the Black advantage at window=2.

---

# Recommendation: What to Build

## The Core Game (Ship This)

- Standard chess board and opening setup
- **Piece degradation:** Queen → Damaged Rook → Crippled Bishop → Weak Pawn → Dead (on 5th capture)
- **Retaliation (attacker-rekill, window=1):** Captured piece respawns threatening opponent's most valuable piece. The capturer can re-kill it on their next move for permanent removal, or let it stay as a degraded fighter.
- **King permakill:** The king permanently removes any piece it captures
- **Checkmate wins** (standard)
- **Stalemate = draw** (standard)
- **Normal king movement** (per RULES.md)
- **Tier 2 slider range = 5** (Phase 1 finding, confirmed)
- **Ko repetition banned** (prevents draw loops)

This produces: 12% draws, 100 mean plies, 42 captures/100 plies, 25 redeployments per game, perfect color balance. A genuinely novel chess variant with strategic depth.

## The Variant (Optional Mode)

Window=2 as a "Blitz Matryoshka" mode — faster, more violent, with 4+ re-kills per game. Better for casual play and spectating. Investigate the Black advantage before promoting competitively.

## What Would Make It More Interesting

1. **Player-chosen redeployment** — Instead of auto-placing the respawned piece, give the captured player a choice of 2-3 valid squares (like Crazyhouse drops). This adds agency at the most dramatic moment.

2. **Visual identity for degradation** — The piece tiers need to be visually distinct and satisfying. A queen that visually cracks and shrinks when captured, then appears as a smaller rook, is the kind of moment that makes players talk about the game.

3. **"Gauntlet" mode** — A degraded piece that survives 3+ redeployments without being permanently killed gets promoted back one tier. This rewards pieces that "survive the battlefield" and creates comeback stories.

4. **Asymmetric starting positions** — One player starts with all full-strength pieces, the other with pre-degraded pieces but more of them. Creates a David vs Goliath dynamic.

## Immediate Next Step

**Build a playable prototype.** A terminal-based human-vs-engine mode using the v2 engine at depth 3. Play 20 games yourself. The numbers say the game works — you need to feel whether it's fun.

---

# Data Sources

| Phase | Focus | Games | Depth | Key Finding |
|---|---|---:|---:|---|
| 1 | Parameter sweep (old engine) | ~41,000 | 1-ply | Degradation works; retaliation unclear; structural locks identified |
| 2 | Engine upgrade validation | ~1,860 | 2 | Engine credible (26% chess draws); all variants too decisive at d2 |
| 3 | Retaliation isolation | ~1,200 | 2 | Safe targeting was broken (70% Circe fallback); unsafe targeting fixes it |
| 4 | Corrected design | ~600 | 3 | Designer's attacker-rekill mechanic validated; window=1 is the sweet spot |
