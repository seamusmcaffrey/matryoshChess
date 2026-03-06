# Design Correction: What We Tested vs What We Meant to Build

2026-03-05. Documenting a critical design divergence discovered during Phase 2-3 review.

---

# The Three Divergences

## 1. Retaliation Mechanic (Major)

### Designer's Intent

The retaliation mechanic is meant to make captures costly for the **attacker**:

1. White captures Black's knight
2. Knight degrades one tier (e.g., full knight becomes damaged knight / wazir)
3. Degraded knight is placed on a square threatening White's most valuable piece
4. **White** now faces a choice: spend their next move re-killing the knight, or pursue their attack
5. If White re-captures the knight within 1 move → knight is **permanently dead** (no further degradation, just gone)
6. If White ignores it → knight stays on the board as a normal degraded piece, eligible for further degradation if captured again later

**Key properties:**
- The **attacker** bears the cost (tempo loss to re-kill)
- The **defender** is passive (their piece just exists as a problem)
- Captures become a risk/reward decision: "is taking this piece worth losing a tempo?"
- Simple to explain: "capture a piece, it comes back weaker aimed at your best piece — kill it again or it sticks around"

### What Was Implemented

The implementation inverted the mechanic:

1. White captures Black's knight
2. Knight degrades and is placed threatening White's most valuable piece
3. **Black** gets a strike window — Black can use the degraded knight to capture White's MVP
4. If Black captures the target within X moves → **White's MVP is permanently dead**
5. If the window expires → knight is just a normal degraded piece

**Key differences:**
- The **defender** has the active decision (execute the revenge kill or not)
- The **attacker's best piece** is at risk of permadeath, not the respawned piece
- The mechanic rewards the defender for being captured (you get a free assassination attempt)
- Requires tracking: which piece is the "target," how many moves remain in the window

### Impact on Data

All Phase 1-3 retaliation data tested the inverted version. The findings about safe vs unsafe targeting, strike rates, and placement percentages describe a mechanic that doesn't match the design intent. The core finding that "safe targeting barely fires" (70% Circe fallback) likely applies to both versions since the placement geometry is the same. But the strategic implications are completely different.

### What Needs to Happen

Implement the designer's version:
- After capture + degradation + placement, mark the **respawned piece** as the target (not the opponent's MVP)
- The **capturer's side** must re-capture the respawned piece within N moves for a permanent kill
- If the window expires, the piece loses its "target" status and becomes a normal degraded piece
- No special behavior needed from the defender — the piece just exists and threatens

## 2. Win Condition (Medium)

### Designer's Intent

The game ends by **checkmate only**, as specified in RULES.md. Standard chess win condition.

### What Was Implemented

The Phase 1 optimizer introduced `win_condition: checkmate_or_king_capture` as an anti-draw lever. This means if a player leaves their king in a position where it can be captured, the opponent captures it and wins immediately. This is a different (looser) win condition than checkmate.

The Phase 2 locked rules carried this forward as a structural lock.

### Impact on Data

34% of game endings in the best Phase 1 variant were king captures, not checkmates. This inflated decisiveness numbers. With checkmate-only, some of those games might have been draws (the attacker can't force mate but could have captured the king under the looser rule).

### What Needs to Happen

Revert to `win_condition: checkmate_only`. All future testing uses standard checkmate as the only win condition.

## 3. King Permakill (Minor — Needs Isolation)

### Designer's Intent

The king has a special property: any piece the king captures is permanently removed from the game (no degradation, no respawn). This is specified in RULES.md and is part of the intended design.

### What Was Implemented

This was implemented correctly. However, it has never been tested in isolation — it was always bundled with other anti-draw levers (stalemate_is_loss, king_capture_line, etc.).

### What Needs to Happen

Test with king permakill on vs off to measure its independent contribution. It may be doing significant work in the endgame (clearing degraded pieces) or it may be negligible.

---

# Summary of Required Changes

| Change | Type | Effect |
|---|---|---|
| Retaliation: attacker must re-kill respawned piece | **Mechanic redesign** | Captures cost tempo instead of giving defender assassination attempts |
| Win condition: checkmate only | **Revert to RULES.md** | No more king-capture wins |
| King permakill: test on vs off | **Isolation test** | Measure independent contribution |
| Remove stalemate_is_loss default | **Simplification** | Stalemate = draw per standard chess |
| Remove king_capture_line default | **Simplification** | Normal king movement per RULES.md |

---

# What Carries Forward from Phases 1-3

Despite the retaliation inversion, several findings remain valid:

- **Degradation is the foundation** — 26% → 1.5-4% draws, 2x capture density. This doesn't depend on retaliation mechanics.
- **Safe targeting barely fires** — the geometry problem (degraded piece with range 1-5 can't find safe attacking squares) applies regardless of what happens after placement. Unsafe targeting is needed for either version.
- **The game is inherently decisive** — degradation alone crushes draws. Structural anti-draw locks are unnecessary.
- **Engine v2 is credible** — 26% draws in normal chess validates the engine for comparative testing.
- **The game creates novel board states** — KL divergence of 1.2-1.7 vs normal chess. Degraded piece types create genuinely different positions.
