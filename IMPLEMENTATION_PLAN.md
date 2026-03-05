# Matryoshka Chess: Engine Upgrade and Phase 2 Optimization

End-to-end implementation guide for the coding agent.

---

# Context

The current simulator (`simulate_variant_study.py`) uses a 1-ply evaluator with 22% random moves. It is adequate for ranking rule configurations against each other but cannot answer strategic questions about the game. The normal chess anchor produces 93% draws (real-world engine chess: 50-75%), confirming the engine is too weak for absolute comparisons.

Phase 1 optimization identified a clear lever hierarchy. We now need to lock those structural findings, upgrade the engine, and re-test the open design questions around the game's two core mechanics:

1. **The degradation system** (the matryoshka nesting itself): pieces surviving capture at reduced strength, the tier chain, collapse to pawn. This IS the game's identity. The 1-ply engine confirmed it creates novel board states but couldn't tell us whether it creates interesting *decisions* for a thinking player. Does a strong engine find the degradation dynamics strategically rich, or does it play through them like noise?

2. **Retaliation** (aggressive redeployment + strike): the "pieces fight back" mechanic. The 1-ply engine couldn't perceive retaliation threats at all, making the Phase 1 finding ("retaliation doesn't matter much") potentially an artifact. A thinking engine that can see "if I capture that bishop, it comes back threatening my queen" might find retaliation transforms the game.

Both questions require a stronger engine to answer. Neither can be resolved by further 1-ply optimization.

**RULES.md must not be modified.** It is the canonical game definition. All locked configurations go into a new file.

---

# Phase 1: Lock Structural Rules

## File: `locked_rules.py`

Create a new file that defines the locked rule configuration derived from optimizer findings. This is NOT a replacement for RULES.md -- it is the optimizer's best-known structural configuration used as the default for all future runs.

```python
"""Locked structural rules from Phase 1 optimization (run_open_20260305).

These settings were determined to be robust across engine strengths
because they change what is legal/illegal, not how deeply you calculate.
RULES.md remains the canonical game definition and is not modified.
"""

from simulate_variant_study import RuleConfig

# Structural locks (high confidence, engine-independent)
LOCKED_STRUCTURAL = {
    "ruleset": "matryoshka",
    "tier2_slider_max_range": 5,         # t2=5 >> t2=4 >> t2=3 (mean interest 0.449 vs 0.310)
    "stalemate_is_loss": True,           # 9/10 top variants
    "win_condition": "checkmate_or_king_capture",
    "ko_repetition_illegal": True,       # cleans up repetition draws
}

# King mode: test both top performers, don't lock to one yet
KING_MODES_TO_TEST = [
    {"king_move_mode": "king_capture_line", "king_capture_line_range": 3},
    {"king_move_mode": "king_dash", "king_dash_max": 2},
]

# Retaliation parameters: the open design space for Phase 3
RETALIATION_SEARCH_SPACE = {
    "retaliation_enabled": [True, False],
    "retaliation_targeting": ["highest_safe", "localized_safe", "top2_pool_safe", "random_legal"],
    "retaliation_local_radius": [2, 3, 4, 5],
    "retaliation_strike_window": [1, 2, 3, 4],
    "retaliation_tiebreak": ["random", "max_threat", "min_king_distance"],
    "strike_effect": ["perma_kill", "double_demote"],
}

# Doom clock: keep as a variable, test whether strong engine still needs it
DOOM_CLOCK_SEARCH_SPACE = {
    "doom_clock_full_moves": [0, 24, 32],
    "doom_clock_effect": ["demote_random_non_king", "bonus_capture_damage"],
}

# Values to DROP from search (dead ends from Phase 1)
EXCLUDED = {
    "ruleset": ["normal", "circe", "anticirce"],  # different games
    "king_move_mode": ["king_k_range"],            # 6/15 bottom, 0/15 top
    "quiet_halfmove_limit": [0, 100],              # lock to 60
}


def build_locked_config(**overrides) -> RuleConfig:
    """Build a RuleConfig with structural locks applied, plus any overrides."""
    defaults = {
        **LOCKED_STRUCTURAL,
        "king_move_mode": "king_capture_line",
        "king_capture_line_range": 3,
        "king_dash_max": 2,
        "king_k_range": 2,
        "king_capture_insta_kill": "off",
        "king_infinite_kill": False,
        "quiet_halfmove_limit": 60,
        "retaliation_enabled": True,
        "retaliation_targeting": "highest_safe",
        "retaliation_local_radius": 4,
        "retaliation_tiebreak": "random",
        "retaliation_strike_window": 1,
        "strike_effect": "perma_kill",
        "fallback_policy": "random",
        "doom_clock_full_moves": 0,
        "doom_clock_effect": "demote_random_non_king",
        "knight_decay_mode": "wazir",
        "collapse_target": "pawn",
        "crippled_pawn_can_promote": False,
        "tier3_slider_max_range": 1,
    }
    defaults.update(overrides)
    return RuleConfig(**defaults)
```

---

# Phase 2: Engine Upgrade

## Goal

Replace the 1-ply random-exploration evaluator with a proper minimax search that can perceive multi-move tactics, especially:
- Retaliation threats ("if I capture X, it redeploys threatening my queen")
- Degradation consequences ("that piece is tier 2, worth less to capture")
- King augmentation tactics ("my king can dash to capture that crippled piece")
- Strike window plays ("I have 1 move to execute my retaliation kill")

## Architecture

Create a new file: `engine.py`

Do NOT modify `simulate_variant_study.py`'s `GameState`, `_apply_capture`, `_redeploy_with_retaliation`, `legal_moves`, or any game logic. The engine upgrade is purely in the move selection layer. The existing `choose_move` function stays as-is for backward compatibility; the new engine provides an alternative.

### 2.1 Evaluation Function: `evaluate_position_v2`

Replace the current evaluation (lines 1433-1457 of `simulate_variant_study.py`) with a richer heuristic. Keep it in the new `engine.py` file.

**Material evaluation** (keep existing `MATERIAL_VALUES` tier-aware dict, import it):
- Standard piece values already tier-aware. Good as-is.
- Add: bonus for having MORE pieces on board (matryoshka-specific: piece count matters because degraded pieces still fight).

**Positional evaluation** (new):
- Piece-square tables for each piece type. Use simplified tables (not full Stockfish PSTs). Center control, advancement, king proximity in endgame.
- Tier-aware PST adjustment: damaged/crippled pieces should prefer squares close to action (they can't reach far).
- King safety: count attackers near king, penalize open files toward king. More important than current simple "am I in check" binary.

**Matryoshka-specific evaluation** (new):
- Retaliation threat value: if a piece has `retaliation_window > 0` and its `retaliation_target` is still alive, evaluate the threat. Score = material value of target * probability-of-execution (heuristic: is the target still on the threatened square? can the retaliating piece reach it?).
- Degradation pressure: count enemy pieces at tier 2+ vs your pieces at tier 1. Advantage in "health" matters.
- Pawn collapse density: penalize positions where your side has many collapsed pawns (low mobility, clogged board).
- King execution potential: in endgame (few pieces), bonus for king proximity to enemy degraded pieces (king permanently removes on capture -- this is huge in endgames).

**Phase detection** (new):
- Opening (ply < 20): prioritize development, center.
- Middlegame (20 <= ply < 80): prioritize tactics, captures, retaliation.
- Endgame (ply >= 80 OR few non-pawn pieces): prioritize king activity, pawn promotion, permanent removal.

```python
# engine.py - skeleton

from simulate_variant_study import (
    GameState, Piece, Move, MoveEvent, RuleConfig,
    MATERIAL_VALUES, WHITE, BLACK, DRAW,
    NUM_SQUARES, sq_to_rc,
)
from typing import List, Tuple, Optional, Dict
import math

# --- Piece-Square Tables (8x8, white perspective, flip for black) ---
# Simplified: just center-bonus + advancement-bonus per piece type.
# Index by square (0-63), white POV. Negate and mirror for black.

def _make_pst() -> Dict[str, List[float]]:
    """Generate simple piece-square tables."""
    tables = {}
    # Pawns: value advancement
    pawn = [0.0] * 64
    for sq in range(64):
        r, c = sq // 8, sq % 8
        pawn[sq] = (r - 1) * 0.08  # further advanced = better
        if c in (3, 4):
            pawn[sq] += 0.1
        if c in (2, 5):
            pawn[sq] += 0.04
    tables["P"] = pawn

    # Knights/Wazirs/etc: center preference
    for kind in ("N", "W", "C", "D"):
        t = [0.0] * 64
        for sq in range(64):
            r, c = sq // 8, sq % 8
            # Distance from center
            dist = max(abs(r - 3.5), abs(c - 3.5))
            t[sq] = (3.5 - dist) * 0.08
        tables[kind] = t

    # Sliders: mild center preference, less than knights
    for kind in ("Q", "R", "B"):
        t = [0.0] * 64
        for sq in range(64):
            r, c = sq // 8, sq % 8
            dist = max(abs(r - 3.5), abs(c - 3.5))
            t[sq] = (3.5 - dist) * 0.04
        tables[kind] = t

    # King: stay safe in opening/middle, activate in endgame
    # (handled dynamically, not via static PST)
    tables["K"] = [0.0] * 64

    return tables

PST = _make_pst()


def evaluate_position_v2(game: GameState, color: str, ply: int = 0) -> float:
    """Tier-aware positional evaluation for matryoshka chess."""
    opp = game._opponent(color)
    score = 0.0

    piece_count = len(game.pieces)
    is_endgame = piece_count <= 12 or ply >= 80

    for piece in game.pieces.values():
        sign = 1.0 if piece.color == color else -1.0

        # Material
        mat = game._material_value(piece)
        score += sign * mat

        # PST
        sq = piece.square
        pst_sq = sq if piece.color == WHITE else (56 - (sq // 8) * 8 + sq % 8)
        # Flip rank for black: row 0 <-> row 7
        if piece.color == BLACK:
            r, c = sq // 8, sq % 8
            pst_sq = (7 - r) * 8 + c
        pst_table = PST.get(piece.kind)
        if pst_table:
            score += sign * pst_table[pst_sq]

        # Tier health advantage
        if piece.kind in ("Q", "R", "B") and piece.tier == 1:
            score += sign * 0.15  # full-health bonus
        elif piece.kind in ("Q", "R", "B") and piece.tier == 3:
            score -= sign * 0.1   # crippled penalty

        # Retaliation threat
        if (piece.retaliation_window > 0
                and piece.retaliation_target is not None
                and piece.retaliation_target in game.pieces):
            target = game.pieces[piece.retaliation_target]
            target_val = game._material_value(target)
            score += sign * target_val * 0.3  # threat discount

        # King activity in endgame
        if piece.kind == "K" and is_endgame:
            enemy_king_sq = game._king_square(opp if piece.color == color else color)
            if enemy_king_sq is not None:
                kr, kc = sq_to_rc(piece.square)
                ekr, ekc = sq_to_rc(enemy_king_sq)
                king_dist = max(abs(kr - ekr), abs(kc - ekc))
                # Closer to enemy king = better in endgame
                score += sign * (7 - king_dist) * 0.06

    # King safety (non-endgame): count attackers near own king
    if not is_endgame:
        for side, s in [(color, -1.0), (opp, 1.0)]:
            king_sq = game._king_square(side)
            if king_sq is None:
                continue
            kr, kc = sq_to_rc(king_sq)
            attacker_count = 0
            attacker_color = game._opponent(side)
            for piece in game.pieces.values():
                if piece.color != attacker_color or piece.kind in ("P", "K"):
                    continue
                pr, pc = sq_to_rc(piece.square)
                if max(abs(pr - kr), abs(pc - kc)) <= 2:
                    attacker_count += 1
            score += s * attacker_count * 0.12

    # Check bonuses
    if game.is_in_check(opp):
        score += 0.5
    if game.is_in_check(color):
        score -= 0.5

    return score
```

### 2.2 Search: Alpha-Beta with Iterative Deepening

```python
# Continuation of engine.py

INFINITY = 100_000.0


def _order_moves(game: GameState, moves: List[Move]) -> List[Move]:
    """Move ordering heuristic: captures first, checks, then quiet moves."""
    captures = []
    checks = []
    quiet = []
    for mv in moves:
        if mv.capture_id is not None:
            # MVV-LVA: prioritize capturing high-value with low-value
            victim_val = 0.0
            if mv.capture_id in game.pieces:
                victim_val = game._material_value(game.pieces[mv.capture_id])
            attacker_id = game.board[mv.from_sq]
            attacker_val = 0.0
            if attacker_id is not None and attacker_id in game.pieces:
                attacker_val = game._material_value(game.pieces[attacker_id])
            captures.append((victim_val - attacker_val * 0.1, mv))
        else:
            quiet.append(mv)
    captures.sort(key=lambda x: -x[0])
    return [mv for _, mv in captures] + quiet


def alpha_beta(
    game: GameState,
    depth: int,
    alpha: float,
    beta: float,
    maximizing_color: str,
    ply: int,
    node_count: list,  # mutable counter [count]
    max_nodes: int = 500_000,
) -> float:
    """Alpha-beta minimax search."""
    node_count[0] += 1

    if node_count[0] >= max_nodes:
        return evaluate_position_v2(game, maximizing_color, ply)

    if game.terminated:
        if game.winner == maximizing_color:
            return INFINITY - ply  # prefer faster wins
        if game.winner == DRAW:
            return 0.0
        return -INFINITY + ply  # prefer slower losses

    if depth <= 0:
        return evaluate_position_v2(game, maximizing_color, ply)

    side = game.side_to_move
    is_maximizing = (side == maximizing_color)
    legal = game.legal_moves(side)

    if not legal:
        # Checkmate or stalemate handled by game state
        # Force terminal evaluation
        sim = game.clone()
        if sim.is_in_check(side):
            return -INFINITY + ply if is_maximizing else INFINITY - ply
        # Stalemate
        if game.rules.stalemate_is_loss:
            return -INFINITY + ply if is_maximizing else INFINITY - ply
        return 0.0

    ordered = _order_moves(game, legal)

    if is_maximizing:
        value = -INFINITY
        for mv in ordered:
            sim = game.clone()
            sim.apply_move(mv)
            child_val = alpha_beta(sim, depth - 1, alpha, beta, maximizing_color, ply + 1, node_count, max_nodes)
            value = max(value, child_val)
            alpha = max(alpha, value)
            if alpha >= beta:
                break
        return value
    else:
        value = INFINITY
        for mv in ordered:
            sim = game.clone()
            sim.apply_move(mv)
            child_val = alpha_beta(sim, depth - 1, alpha, beta, maximizing_color, ply + 1, node_count, max_nodes)
            value = min(value, child_val)
            beta = min(beta, value)
            if alpha >= beta:
                break
        return value


def choose_move_v2(
    game: GameState,
    legal_moves: List[Move],
    target_depth: int = 4,
    max_nodes: int = 500_000,
    noise: float = 0.03,
) -> Move:
    """Iterative-deepening alpha-beta move selection.

    Args:
        game: current game state
        legal_moves: precomputed legal moves
        target_depth: maximum search depth in plies
        max_nodes: hard cap on nodes evaluated (controls thinking time)
        noise: small random perturbation to avoid deterministic play
    """
    side = game.side_to_move
    best_move = legal_moves[0]
    best_score = -INFINITY

    # Iterative deepening: search depth 1, 2, ..., target_depth
    for depth in range(1, target_depth + 1):
        node_count = [0]
        depth_best_move = legal_moves[0]
        depth_best_score = -INFINITY

        ordered = _order_moves(game, legal_moves)

        for mv in ordered:
            sim = game.clone()
            event = sim.apply_move(mv)

            if event.game_over and event.winner == side:
                return mv  # immediate win

            score = alpha_beta(
                sim, depth - 1, -INFINITY, INFINITY,
                side, game.ply + 1, node_count, max_nodes
            )

            # Add tiny noise
            score += game.rng.uniform(-noise, noise)

            if score > depth_best_score:
                depth_best_score = score
                depth_best_move = mv

            if node_count[0] >= max_nodes:
                break

        best_move = depth_best_move
        best_score = depth_best_score

        if node_count[0] >= max_nodes:
            break

    return best_move
```

### 2.3 Resignation Logic

Add to `engine.py`:

```python
def should_resign(game: GameState, color: str, ply: int) -> bool:
    """Return True if position is hopeless and engine should resign."""
    if ply < 40:
        return False  # too early to resign

    score = evaluate_position_v2(game, color, ply)

    # Resign if down massive material with no retaliation threats
    if score < -12.0:
        # Check for active retaliation threats that might save us
        has_retaliation = any(
            p.retaliation_window > 0 and p.color == color
            for p in game.pieces.values()
        )
        if not has_retaliation:
            return True

    return False
```

### 2.4 Engine Profiles (for playstyle testing)

```python
# engine.py continued

from dataclasses import dataclass

@dataclass
class EngineProfile:
    """Configurable engine personality."""
    name: str
    search_depth: int = 4
    max_nodes: int = 500_000
    noise: float = 0.03
    aggression: float = 0.0      # positive = prefer captures, negative = prefer quiet
    retaliation_weight: float = 1.0  # multiplier on retaliation threat evaluation
    king_safety_weight: float = 1.0  # multiplier on king safety eval
    resign_threshold: float = -12.0

# Predefined profiles
PROFILE_BALANCED = EngineProfile(name="balanced", search_depth=4)
PROFILE_AGGRESSIVE = EngineProfile(
    name="aggressive", search_depth=4,
    aggression=0.5, retaliation_weight=1.5, king_safety_weight=0.7,
)
PROFILE_DEFENSIVE = EngineProfile(
    name="defensive", search_depth=4,
    aggression=-0.3, retaliation_weight=0.8, king_safety_weight=1.5,
)
PROFILE_SHALLOW = EngineProfile(
    name="shallow", search_depth=2, max_nodes=50_000, noise=0.1,
)
```

### 2.5 Integration Point

The engine must be callable from the game loop without modifying `simulate_variant_study.py`'s game logic. The integration point is the `choose_move` function call at line 1560 of `simulate_variant_study.py`.

Create a new runner script (Phase 3 below) that imports `GameState` and `run_single_game`-equivalent logic but uses `choose_move_v2` instead of `choose_move`. Do NOT modify the existing `choose_move` or `run_single_game` -- they remain for backward compatibility and fast broad sweeps.

**Performance constraint**: The 1-ply engine runs ~700 games in 30 seconds. A depth-4 engine will be dramatically slower. Target: **~10 games/minute** with depth 4 and 500k node cap on M4 Max. Budget accordingly:
- Validation runs: 200 games per configuration (20 min each)
- Full comparison runs: 700 games per configuration (70 min each)
- The optimizer must account for this ~100x slowdown

---

# Phase 3: Re-Optimization Scripts

## File: `run_phase2_study.py`

New optimizer script that uses the upgraded engine and locked structural rules. Narrower search space, deeper games, focused on the two core design questions.

### 3.1 Design Questions to Answer

**Question 1: Does the degradation system create interesting play with a thinking engine?**
- Compare: matryoshka (locked rules) vs normal chess, both with depth-4 engine
- Metrics: draw rate, game length, material volatility, endgame piece diversity
- Expected: matryoshka should still be more decisive and more novel. If it isn't, the degradation system is engine-dependent and that's a fundamental problem.

**Question 2: Does retaliation create interesting play with a thinking engine?**
- Compare: matryoshka+retaliation vs matryoshka-without-retaliation, both locked rules, depth-4 engine
- Metrics: draw rate, retaliation events per game, successful strikes per game, material swings caused by retaliation
- This is THE key test. With a thinking engine, retaliation threats can be perceived, feared, and exploited.

**Question 3: What retaliation parameters work best with a real engine?**
- Only run if Q2 shows retaliation adds value
- Search space: targeting mode, radius, strike window, strike effect
- Cross with both king_capture_line and king_dash

**Question 4: Is doom clock needed with a stronger engine?**
- Compare: best retaliation config with and without doom clock
- A strong engine might convert without needing forced attrition

**Question 5: Do different playstyles produce different outcomes?**
- Run: aggressive vs defensive, balanced vs balanced, aggressive vs balanced
- Answers whether the game rewards diverse strategies or has a dominant playstyle

### 3.2 Script Structure

```python
#!/usr/bin/env python3
"""Phase 2 study: upgraded engine + locked structural rules.

Tests the two core design questions:
1. Does degradation create interesting play with a thinking engine?
2. Does retaliation create interesting play with a thinking engine?
"""

import argparse
import json
import os
import time
import multiprocessing as mp
from pathlib import Path
from typing import Dict, List, Optional

from simulate_variant_study import (
    GameState, RuleConfig, Move, WHITE, BLACK, DRAW,
    run_single_game,  # used for v1 comparison
)
from engine import (
    choose_move_v2, evaluate_position_v2, should_resign,
    EngineProfile, PROFILE_BALANCED, PROFILE_AGGRESSIVE, PROFILE_DEFENSIVE,
)
from locked_rules import build_locked_config, LOCKED_STRUCTURAL


def run_game_v2(
    seed: int,
    rules: RuleConfig,
    max_plies: int = 300,
    white_profile: EngineProfile = PROFILE_BALANCED,
    black_profile: EngineProfile = PROFILE_BALANCED,
    snapshot_plies: tuple = (40, 80, 120, 160),
) -> dict:
    """Run a single game with the v2 engine.

    Key differences from run_single_game:
    - Uses choose_move_v2 (alpha-beta) instead of choose_move (1-ply)
    - Supports per-side engine profiles
    - Adds resignation detection
    - Higher max_plies default (300 vs 220) since stronger engine should finish
    - Tracks retaliation-specific event counts
    """
    game = GameState(seed=seed, rules=rules)
    ply = 0
    move_log = []

    while not game.terminated and ply < max_plies:
        side = game.side_to_move
        profile = white_profile if side == WHITE else black_profile

        # Resignation check
        if should_resign(game, side, ply):
            game.terminated = True
            game.winner = game._opponent(side)
            game.termination_reason = "resignation"
            break

        legal = game.legal_moves(side)
        if not legal:
            if game.is_in_check(side):
                game.terminated = True
                game.winner = game._opponent(side)
                game.termination_reason = "checkmate"
            else:
                game.terminated = True
                if rules.stalemate_is_loss:
                    game.winner = game._opponent(side)
                    game.termination_reason = "stalemate_loss"
                else:
                    game.winner = DRAW
                    game.termination_reason = "stalemate"
            break

        move = choose_move_v2(
            game, legal,
            target_depth=profile.search_depth,
            max_nodes=profile.max_nodes,
            noise=profile.noise,
        )
        event = game.apply_move(move)
        ply += 1

        # Quiet move limit
        if rules.quiet_halfmove_limit > 0 and game.quiet_halfmoves >= rules.quiet_halfmove_limit:
            game.terminated = True
            game.winner = DRAW
            game.termination_reason = "quiet_limit"

    if not game.terminated:
        game.terminated = True
        game.winner = DRAW
        game.termination_reason = "max_plies"

    return {
        "seed": seed,
        "winner": game.winner,
        "termination": game.termination_reason,
        "plies": ply,
        "stats": dict(game.stats),
        "white_profile": white_profile.name,
        "black_profile": black_profile.name,
    }


# --- Study Configurations ---

STUDY_CONFIGS = {
    # Q1: Does degradation matter?
    "normal_chess_v2": {
        "description": "Normal chess with v2 engine (validation baseline)",
        "rules": RuleConfig(),  # all defaults = normal chess
        "games": 200,
    },
    "matryoshka_no_ret": {
        "description": "Matryoshka with degradation only, no retaliation",
        "rules": build_locked_config(retaliation_enabled=False),
        "games": 400,
    },

    # Q2: Does retaliation matter?
    "matryoshka_ret_baseline": {
        "description": "Matryoshka + retaliation (default params)",
        "rules": build_locked_config(retaliation_enabled=True),
        "games": 400,
    },

    # Q3: Retaliation parameter search (only if Q2 positive)
    "ret_close_fast": {
        "description": "Retaliation: close placement, short window, perma kill",
        "rules": build_locked_config(
            retaliation_enabled=True,
            retaliation_local_radius=2,
            retaliation_strike_window=1,
            strike_effect="perma_kill",
        ),
        "games": 400,
    },
    "ret_wide_long": {
        "description": "Retaliation: wide placement, long window, perma kill",
        "rules": build_locked_config(
            retaliation_enabled=True,
            retaliation_local_radius=5,
            retaliation_strike_window=3,
            strike_effect="perma_kill",
        ),
        "games": 400,
    },
    "ret_aggressive_targeting": {
        "description": "Retaliation: top2 pool targeting, max threat tiebreak",
        "rules": build_locked_config(
            retaliation_enabled=True,
            retaliation_targeting="top2_pool_safe",
            retaliation_tiebreak="max_threat",
            retaliation_strike_window=2,
            strike_effect="perma_kill",
        ),
        "games": 400,
    },
    "ret_king_dash": {
        "description": "Same as baseline but with king_dash instead of capture_line",
        "rules": build_locked_config(
            retaliation_enabled=True,
            king_move_mode="king_dash",
            king_dash_max=2,
        ),
        "games": 400,
    },

    # Q4: Doom clock needed?
    "ret_plus_doom": {
        "description": "Best retaliation config + doom clock 32",
        "rules": build_locked_config(
            retaliation_enabled=True,
            doom_clock_full_moves=32,
            doom_clock_effect="bonus_capture_damage",
        ),
        "games": 400,
    },

    # Q5: Playstyle diversity (use best config from Q2/Q3)
    # These are configured at runtime based on Q2/Q3 results
}


def run_study(
    config_name: str,
    output_dir: str,
    workers: int = 4,
    profile_white: EngineProfile = PROFILE_BALANCED,
    profile_black: EngineProfile = PROFILE_BALANCED,
):
    """Run a single study configuration and write results."""
    config = STUDY_CONFIGS[config_name]
    rules = config["rules"]
    num_games = config["games"]
    # Implementation: parallel game execution with progress tracking
    # Write per-game results to JSONL, compute summary stats
    # See run_variant_optimization.py for patterns
    ...
```

### 3.3 New Metrics to Track

Add these to the summary computation (they already exist in `game.stats` but aren't surfaced in the optimizer report):

```python
MATRYOSHKA_METRICS = {
    # Already tracked in game.stats:
    "retaliation_redeployments": "total retaliation placements",
    "retaliation_safe_target_placements": "placements that found a threatening square",
    "retaliation_circe_placements": "fallback to circe square",
    "retaliation_random_placements": "fallback to random/king-proximity",
    "retarget_captures_success": "successful retaliation strikes",
    "retarget_captures_attempted": "attempted retaliation captures",
    "permanent_removals_total": "pieces permanently removed",
    "permanent_removals_king_capture": "removed by king",
    "permanent_removals_retaliation_strike": "removed by successful strike",
    "capture_repeats_over_one": "pieces captured more than once (recycling indicator)",

    # NEW - add to game.stats tracking:
    "tier_distribution_final": "count of pieces at each tier at game end",
    "collapsed_pawns_created": "pieces that degraded all the way to pawn",
    "retaliation_threat_moves_available": "moves where side had an active retaliation window",
    "resignation_count": "games ending in resignation",
    "mean_search_depth_achieved": "average iterative deepening depth reached",
}
```

### 3.4 Comparative Report Output

The study script should produce a markdown report comparing all configurations:

```
outputs_phase2_YYYYMMDD/
  study_report.md          # main comparison table + analysis
  config_<name>/
    summary.json            # per-config statistics
    games.jsonl             # raw game results
    retaliation_analysis.md # retaliation-specific breakdown (if applicable)
```

The report should include:

1. **Baseline validation**: normal chess with v2 engine. Draw rate should be 50-70% (in line with TCEC). If it's still >85%, the engine needs more work.
2. **Degradation impact**: matryoshka_no_ret vs normal_chess_v2. Delta in draw rate, game length, material volatility.
3. **Retaliation impact**: matryoshka_ret_baseline vs matryoshka_no_ret. This is the money comparison.
4. **Retaliation tuning**: parameter variants ranked by interestingness.
5. **Playstyle diversity**: cross-profile results.

---

# Execution Order and Dependencies

```
1. Create locked_rules.py
   - No dependencies
   - Verify: import locked_rules; build_locked_config() returns valid RuleConfig

2. Create engine.py
   - Depends on: simulate_variant_study.py (import GameState, etc.)
   - Do NOT modify simulate_variant_study.py
   - Verify: engine can play a complete game
     - run_game_v2(seed=42, rules=build_locked_config()) completes without error
     - Normal chess: run_game_v2(seed=42, rules=RuleConfig()) draws less than 80%
       over 50 test games (sanity check)

3. Benchmark engine speed
   - Target: ~10 games/min at depth 4, 500k nodes on M4 Max
   - If too slow: reduce max_nodes to 200k, reduce depth to 3
   - If fast enough: consider depth 5 or 1M nodes
   - Log: mean time per game, mean nodes per game, mean depth achieved

4. Create run_phase2_study.py
   - Depends on: engine.py, locked_rules.py
   - Run studies in order: normal_chess_v2 first (validation),
     then matryoshka_no_ret, then matryoshka_ret_baseline
   - Gate: if normal chess validation fails (>85% draws),
     stop and improve engine before continuing

5. Run Q1 and Q2 studies
   - ~4-6 hours on M4 Max for ~2600 games at 10 games/min
   - Analyze: does degradation work? does retaliation work?

6. Conditional: Run Q3-Q5 studies
   - Only if Q2 shows retaliation adds measurable value
   - ~6-8 hours for remaining ~2000 games
```

---

# Performance Budget

Assuming M4 Max, 12 workers, depth-4 engine at ~10 games/min/worker:

| Study | Games | Workers | Est. Time |
|---|---:|---:|---|
| normal_chess_v2 | 200 | 12 | ~2 min |
| matryoshka_no_ret | 400 | 12 | ~4 min |
| matryoshka_ret_baseline | 400 | 12 | ~4 min |
| ret_close_fast | 400 | 12 | ~4 min |
| ret_wide_long | 400 | 12 | ~4 min |
| ret_aggressive_targeting | 400 | 12 | ~4 min |
| ret_king_dash | 400 | 12 | ~4 min |
| ret_plus_doom | 400 | 12 | ~4 min |
| **Total** | **3000** | | **~30 min** |

Note: these estimates assume 10 games/min PER WORKER. If the alpha-beta search is slower than expected (likely, given clone() cost), times could be 3-5x longer. The node cap is the primary time control -- tune `max_nodes` to hit the throughput target.

**Key risk**: `GameState.clone()` is called at every node in the search tree. At depth 4 with branching factor ~30, that's ~810,000 clones per game. If clone() takes 0.1ms, that's 81 seconds per game -- too slow. Optimization path: implement incremental make/unmake instead of full clone. This is a significant refactor but standard for chess engines.

If clone-based search is too slow, fallback plan:
- Depth 3 with 100k node cap (still ~27,000 clones per game, ~3 seconds)
- Or implement make/unmake on GameState (harder but correct long-term)

---

# Success Criteria

| Criterion | Target | How to Verify |
|---|---|---|
| Normal chess draw rate with v2 engine | 50-70% | normal_chess_v2 study |
| Engine completes 200 normal chess games | No crashes, no infinite loops | Basic run |
| Matryoshka reduces draws vs normal | >15 percentage point delta | Q1 comparison |
| Retaliation impact is measurable | >5 pp delta OR qualitative difference in game character | Q2 comparison |
| Retaliation events actually happen | >2 retaliation redeployments per game on average | Q2 stats |
| No new code errors | All .py files compile, existing tests pass | `python3 -m py_compile *.py` |
| Existing scripts unmodified | `simulate_variant_study.py`, `run_variant_optimization.py` unchanged | `git diff` |
| RULES.md unmodified | No changes | `git diff RULES.md` |

---

# Files Created/Modified

| File | Action | Purpose |
|---|---|---|
| `locked_rules.py` | **CREATE** | Locked structural rules from Phase 1 |
| `engine.py` | **CREATE** | Alpha-beta engine with evaluation, search, profiles |
| `run_phase2_study.py` | **CREATE** | Phase 2 optimization/study runner |
| `simulate_variant_study.py` | **DO NOT MODIFY** | Existing game logic stays as-is |
| `run_variant_optimization.py` | **DO NOT MODIFY** | Phase 1 optimizer stays for reference |
| `RULES.md` | **DO NOT MODIFY** | Canonical game definition |
