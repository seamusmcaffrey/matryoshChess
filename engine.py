"""Phase 2 search engine for Matryoshka Chess.

This module upgrades move selection from one-ply + randomness to
iterative-deepening alpha-beta with a richer evaluation function.

Game logic remains in simulate_variant_study.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from simulate_variant_study import BLACK, DRAW, WHITE, GameState, Move, Piece, sq_to_rc

INFINITY = 100_000.0


def _mirror_square_for_black(square: int) -> int:
    """Mirror rank for black when indexing white-perspective PSTs."""
    r, c = sq_to_rc(square)
    return (7 - r) * 8 + c


def _make_pst() -> Dict[str, List[float]]:
    """Generate simple 8x8 piece-square tables (white perspective)."""
    tables: Dict[str, List[float]] = {}

    pawn = [0.0] * 64
    for sq in range(64):
        r, c = sq_to_rc(sq)
        # White advances toward row 0.
        pawn[sq] = (6 - r) * 0.08
        if c in (3, 4):
            pawn[sq] += 0.09
        elif c in (2, 5):
            pawn[sq] += 0.04
    tables["P"] = pawn

    for kind in ("N", "W", "C", "D"):
        t = [0.0] * 64
        for sq in range(64):
            r, c = sq_to_rc(sq)
            dist = max(abs(r - 3.5), abs(c - 3.5))
            t[sq] = (3.5 - dist) * 0.09
        tables[kind] = t

    for kind in ("Q", "R", "B"):
        t = [0.0] * 64
        for sq in range(64):
            r, c = sq_to_rc(sq)
            dist = max(abs(r - 3.5), abs(c - 3.5))
            t[sq] = (3.5 - dist) * 0.05
        tables[kind] = t

    # King is handled dynamically by phase-sensitive terms.
    tables["K"] = [0.0] * 64

    return tables


PST = _make_pst()


@dataclass(frozen=True)
class EngineProfile:
    """Configurable engine profile for playstyle experiments."""

    name: str
    search_depth: int = 4
    max_nodes: int = 500_000
    noise: float = 0.03
    aggression: float = 0.0
    retaliation_weight: float = 1.0
    king_safety_weight: float = 1.0
    resign_threshold: float = -12.0


PROFILE_BALANCED = EngineProfile(name="balanced", search_depth=4)
PROFILE_AGGRESSIVE = EngineProfile(
    name="aggressive",
    search_depth=4,
    aggression=0.5,
    retaliation_weight=1.5,
    king_safety_weight=0.75,
)
PROFILE_DEFENSIVE = EngineProfile(
    name="defensive",
    search_depth=4,
    aggression=-0.25,
    retaliation_weight=0.85,
    king_safety_weight=1.45,
)
PROFILE_SHALLOW = EngineProfile(name="shallow", search_depth=2, max_nodes=50_000, noise=0.1)


@dataclass
class SearchInfo:
    nodes: int
    depth_reached: int
    best_score: float


@dataclass
class SearchResult:
    move: Move
    info: SearchInfo


@dataclass
class SearchContext:
    max_nodes: int
    profile: EngineProfile
    nodes: int = 0
    aborted: bool = False
    tt: Dict[Tuple[str, int, str], Tuple[int, float]] = field(default_factory=dict)


def _detect_phase(game: GameState, ply: int) -> Tuple[str, bool]:
    non_pawn_non_king = sum(1 for p in game.pieces.values() if p.kind not in ("P", "K"))
    if ply < 20:
        return "opening", False
    if ply >= 80 or non_pawn_non_king <= 6:
        return "endgame", True
    return "middlegame", False


def _health_points(piece: Piece) -> float:
    if piece.kind == "K":
        return 0.0
    if piece.kind in ("Q", "R", "B"):
        # tier1 > tier2 > tier3
        return max(0.0, 4.0 - float(piece.tier))
    if piece.kind == "N":
        return 2.2
    if piece.kind in ("W", "C", "D"):
        return 1.2
    # Pawns are durable blockers but weak finishers.
    return 0.5 if piece.crippled else 1.0


def _piece_square_score(piece: Piece) -> float:
    table = PST.get(piece.kind)
    if not table:
        return 0.0
    index = piece.square if piece.color == WHITE else _mirror_square_for_black(piece.square)
    value = table[index]

    # Degraded pieces are short-range and should stay near action.
    if piece.kind in ("Q", "R", "B"):
        if piece.tier == 2:
            value *= 1.20
        elif piece.tier >= 3:
            value *= 1.35
    if piece.kind in ("W", "C", "D"):
        value *= 1.10
    if piece.kind == "P" and piece.crippled:
        value *= 0.75

    return value


def _king_safety_penalty(game: GameState, color: str) -> float:
    king_sq = game._king_square(color)
    if king_sq is None:
        return 8.0

    enemy = game._opponent(color)
    kr, kc = sq_to_rc(king_sq)

    nearby_attackers = 0
    direct_attackers = 0
    for piece in game.pieces.values():
        if piece.color != enemy:
            continue
        pr, pc = sq_to_rc(piece.square)
        cheb = max(abs(pr - kr), abs(pc - kc))
        if cheb <= 3:
            nearby_attackers += 1
        if game._piece_can_attack_square(piece, piece.square, king_sq):
            direct_attackers += 1

    ring_exposed = 0
    for dr in (-1, 0, 1):
        for dc in (-1, 0, 1):
            if dr == 0 and dc == 0:
                continue
            rr, cc = kr + dr, kc + dc
            if 0 <= rr < 8 and 0 <= cc < 8:
                sq = rr * 8 + cc
                if game.board[sq] is None:
                    ring_exposed += 1

    return (nearby_attackers * 0.18) + (direct_attackers * 0.28) + (ring_exposed * 0.035)


def _retaliation_threat_score(
    game: GameState,
    perspective: str,
    profile: EngineProfile,
) -> float:
    total = 0.0
    for piece in game.pieces.values():
        if piece.retaliation_window <= 0 or piece.retaliation_target is None:
            continue
        target = game.pieces.get(piece.retaliation_target)
        if target is None:
            continue

        sign = 1.0 if piece.color == perspective else -1.0
        target_value = game._material_value(target)

        can_attack_now = game._piece_can_attack_square(piece, piece.square, target.square)
        tempo_bonus = 0.15 if game.side_to_move == piece.color else 0.0
        reach_bonus = 0.28 if can_attack_now else 0.0
        window_bonus = min(0.20, 0.07 * piece.retaliation_window)

        probability = min(0.92, 0.18 + tempo_bonus + reach_bonus + window_bonus)
        total += sign * target_value * probability

    return total * profile.retaliation_weight


def _king_execution_potential(game: GameState, perspective: str) -> float:
    our_king_sq = game._king_square(perspective)
    if our_king_sq is None:
        return 0.0
    kr, kc = sq_to_rc(our_king_sq)

    score = 0.0
    for piece in game.pieces.values():
        if piece.color == perspective or piece.kind == "K":
            continue

        is_degraded = (
            piece.kind in ("W", "C", "D")
            or (piece.kind in ("Q", "R", "B") and piece.tier >= 2)
            or (piece.kind == "P" and piece.crippled)
        )
        if not is_degraded:
            continue

        pr, pc = sq_to_rc(piece.square)
        dist = max(abs(pr - kr), abs(pc - kc))
        piece_weight = max(0.2, game._material_value(piece))
        score += piece_weight * (7.0 - float(dist)) * 0.04

    return score


def evaluate_position_v2(
    game: GameState,
    color: str,
    ply: int = 0,
    profile: Optional[EngineProfile] = None,
) -> float:
    """Tier-aware positional evaluation for the upgraded engine."""

    profile = profile or PROFILE_BALANCED
    phase, is_endgame = _detect_phase(game, ply)
    opp = game._opponent(color)

    if phase == "opening":
        pst_weight = 1.00
        health_weight = 0.24
        king_activity_weight = 0.10
        king_safety_weight = 1.18
    elif phase == "middlegame":
        pst_weight = 0.90
        health_weight = 0.28
        king_activity_weight = 0.18
        king_safety_weight = 1.05
    else:
        pst_weight = 0.70
        health_weight = 0.22
        king_activity_weight = 0.46
        king_safety_weight = 0.55

    score = 0.0
    own_health = 0.0
    opp_health = 0.0
    own_collapsed_pawns = 0
    opp_collapsed_pawns = 0

    for piece in game.pieces.values():
        sign = 1.0 if piece.color == color else -1.0

        # Material baseline.
        mat = game._material_value(piece)
        score += sign * mat

        # Piece-count pressure matters in Matryoshka because degraded pieces still fight.
        if piece.kind != "K":
            score += sign * 0.045

        # Positional terms.
        score += sign * (pst_weight * _piece_square_score(piece))

        # Development in opening.
        if phase == "opening" and piece.kind in ("Q", "R", "B", "N", "W", "C", "D"):
            if piece.square != piece.origin_square:
                score += sign * 0.05

        # Health pressure bookkeeping.
        hp = _health_points(piece)
        if piece.color == color:
            own_health += hp
        else:
            opp_health += hp

        if piece.kind == "P" and piece.crippled:
            if piece.color == color:
                own_collapsed_pawns += 1
            else:
                opp_collapsed_pawns += 1

    # Degradation/health pressure.
    score += (own_health - opp_health) * health_weight

    # Penalize collapsed pawn density (board clog + reduced conversion power).
    score += (opp_collapsed_pawns - own_collapsed_pawns) * 0.22

    # Retaliation threat valuation.
    if game.rules.ruleset == "matryoshka" and game.rules.retaliation_enabled:
        score += _retaliation_threat_score(game, color, profile) * 0.55

    # King safety (higher in opening/middlegame).
    own_king_pen = _king_safety_penalty(game, color)
    opp_king_pen = _king_safety_penalty(game, opp)
    score += (opp_king_pen - own_king_pen) * king_safety_weight * profile.king_safety_weight

    # Endgame king execution potential.
    if is_endgame:
        score += _king_execution_potential(game, color) * king_activity_weight
        score -= _king_execution_potential(game, opp) * king_activity_weight

    # Light tactical checks.
    if game.is_in_check(opp):
        score += 0.48
    if game.is_in_check(color):
        score -= 0.58

    return score


def _move_order_score(game: GameState, move: Move, aggression: float = 0.0) -> float:
    score = 0.0
    mover_id = game.board[move.from_sq]
    mover = game.pieces.get(mover_id) if mover_id is not None else None

    if move.capture_id is not None and move.capture_id in game.pieces:
        victim = game.pieces[move.capture_id]
        victim_value = game._material_value(victim)
        attacker_value = game._material_value(mover) if mover is not None else 1.0
        score += (victim_value * 2.0) - (attacker_value * 0.1)

    if mover is not None and mover.kind == "P":
        to_r, _ = sq_to_rc(move.to_sq)
        if (mover.color == WHITE and to_r == 0) or (mover.color == BLACK and to_r == 7):
            score += 2.0

    if mover is not None and mover.retaliation_window > 0 and mover.retaliation_target is not None:
        score += 1.2

    score += aggression * (0.35 if move.capture_id is not None else -0.08)
    return score


def _order_moves(game: GameState, moves: Sequence[Move], aggression: float = 0.0) -> List[Move]:
    return sorted(moves, key=lambda mv: _move_order_score(game, mv, aggression=aggression), reverse=True)


def alpha_beta(
    game: GameState,
    depth: int,
    alpha: float,
    beta: float,
    maximizing_color: str,
    ply: int,
    ctx: SearchContext,
) -> float:
    """Alpha-beta minimax with a simple transposition cache."""

    if ctx.nodes >= ctx.max_nodes:
        ctx.aborted = True
        return evaluate_position_v2(game, maximizing_color, ply, profile=ctx.profile)

    ctx.nodes += 1

    if game.terminated:
        if game.winner == maximizing_color:
            return INFINITY - float(ply)
        if game.winner == DRAW:
            return 0.0
        return -INFINITY + float(ply)

    if depth <= 0:
        return evaluate_position_v2(game, maximizing_color, ply, profile=ctx.profile)

    key = (game._position_hash(), depth, maximizing_color)
    cached = ctx.tt.get(key)
    if cached is not None and cached[0] >= depth:
        return cached[1]

    side = game.side_to_move
    legal = game.legal_moves(side)

    if not legal:
        if game.is_in_check(side):
            value = -INFINITY + float(ply) if side == maximizing_color else INFINITY - float(ply)
        elif game.rules.stalemate_is_loss:
            value = -INFINITY + float(ply) if side == maximizing_color else INFINITY - float(ply)
        else:
            value = 0.0
        ctx.tt[key] = (depth, value)
        return value

    ordered = _order_moves(game, legal, aggression=ctx.profile.aggression)

    if side == maximizing_color:
        value = -INFINITY
        for mv in ordered:
            sim = game.clone()
            sim.apply_move(mv)
            child = alpha_beta(sim, depth - 1, alpha, beta, maximizing_color, ply + 1, ctx)
            if child > value:
                value = child
            if value > alpha:
                alpha = value
            if alpha >= beta or ctx.aborted:
                break
    else:
        value = INFINITY
        for mv in ordered:
            sim = game.clone()
            sim.apply_move(mv)
            child = alpha_beta(sim, depth - 1, alpha, beta, maximizing_color, ply + 1, ctx)
            if child < value:
                value = child
            if value < beta:
                beta = value
            if alpha >= beta or ctx.aborted:
                break

    ctx.tt[key] = (depth, value)
    return value


def search_best_move_v2(
    game: GameState,
    legal_moves: Sequence[Move],
    target_depth: int = 4,
    max_nodes: int = 500_000,
    noise: float = 0.03,
    profile: Optional[EngineProfile] = None,
) -> SearchResult:
    """Iterative-deepening alpha-beta search with hard node budget."""

    if not legal_moves:
        raise ValueError("search_best_move_v2 requires at least one legal move")

    profile = profile or PROFILE_BALANCED
    side = game.side_to_move
    best_move = legal_moves[0]
    best_score = -INFINITY
    depth_reached = 0

    ctx = SearchContext(max_nodes=max(1, int(max_nodes)), profile=profile)

    for depth in range(1, max(1, int(target_depth)) + 1):
        if ctx.nodes >= ctx.max_nodes:
            break

        local_best_move = best_move
        local_best_score = -INFINITY
        found_any = False

        ordered = _order_moves(game, legal_moves, aggression=profile.aggression)

        for mv in ordered:
            if ctx.nodes >= ctx.max_nodes:
                ctx.aborted = True
                break

            sim = game.clone()
            sim.apply_move(mv)

            if sim.terminated:
                if sim.winner == side:
                    return SearchResult(
                        move=mv,
                        info=SearchInfo(nodes=ctx.nodes, depth_reached=depth, best_score=INFINITY),
                    )
                if sim.winner == DRAW:
                    score = 0.0
                else:
                    score = -INFINITY + 1.0
            else:
                score = alpha_beta(
                    sim,
                    depth - 1,
                    -INFINITY,
                    INFINITY,
                    side,
                    game.ply + 1,
                    ctx,
                )

            if noise > 0.0:
                score += game.rng.uniform(-noise, noise)

            if (not found_any) or (score > local_best_score):
                local_best_score = score
                local_best_move = mv
                found_any = True

            if ctx.aborted:
                break

        if found_any:
            best_move = local_best_move
            best_score = local_best_score
            depth_reached = depth

        if ctx.aborted:
            break

    return SearchResult(
        move=best_move,
        info=SearchInfo(nodes=ctx.nodes, depth_reached=depth_reached, best_score=best_score),
    )


def choose_move_v2(
    game: GameState,
    legal_moves: Sequence[Move],
    target_depth: int = 4,
    max_nodes: int = 500_000,
    noise: float = 0.03,
    profile: Optional[EngineProfile] = None,
) -> Move:
    """Drop-in move selector for the upgraded engine."""

    return search_best_move_v2(
        game,
        legal_moves,
        target_depth=target_depth,
        max_nodes=max_nodes,
        noise=noise,
        profile=profile,
    ).move


def choose_move_v2_with_info(
    game: GameState,
    legal_moves: Sequence[Move],
    target_depth: int = 4,
    max_nodes: int = 500_000,
    noise: float = 0.03,
    profile: Optional[EngineProfile] = None,
) -> SearchResult:
    """Move selector that also returns search diagnostics."""

    return search_best_move_v2(
        game,
        legal_moves,
        target_depth=target_depth,
        max_nodes=max_nodes,
        noise=noise,
        profile=profile,
    )


def should_resign(
    game: GameState,
    color: str,
    ply: int,
    profile: Optional[EngineProfile] = None,
    resign_threshold: float = -12.0,
) -> bool:
    """Return True when position is very likely lost with no tactical counterplay."""

    if game.terminated or ply < 40:
        return False

    profile = profile or PROFILE_BALANCED
    threshold = float(profile.resign_threshold if profile is not None else resign_threshold)

    score = evaluate_position_v2(game, color, ply=ply, profile=profile)
    if score >= threshold:
        return False

    # Keep playing if immediate retaliation threats exist.
    has_retaliation = any(
        (p.color == color)
        and (p.retaliation_window > 0)
        and (p.retaliation_target is not None)
        and (p.retaliation_target in game.pieces)
        for p in game.pieces.values()
    )
    if has_retaliation:
        return False

    own_material = 0.0
    opp_material = 0.0
    for piece in game.pieces.values():
        if piece.kind == "K":
            continue
        if piece.color == color:
            own_material += game._material_value(piece)
        else:
            opp_material += game._material_value(piece)

    return (opp_material - own_material) >= 8.0
