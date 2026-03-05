#!/usr/bin/env python3
"""Matryoshka Chess simulator (Range Decay + Retaliation variant).

This script models the rules in rules.md, runs self-play games with light
heuristic strategy, exports game-level data, and emits a trend report with
rule-adjustment suggestions.

Intentional simplifications (documented for transparency):
- No castling.
- No en passant.
- Promotion always becomes a full-tier queen.
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import json
import math
import os
import random
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

BOARD_SIZE = 8
NUM_SQUARES = BOARD_SIZE * BOARD_SIZE
WHITE = "W"
BLACK = "B"
DRAW = "D"

PIECE_VALUE_PRIORITY = {
    "Q": 5,
    "R": 4,
    "B": 3,
    "N": 2,
    "W": 2,
    "P": 1,
}

MATERIAL_VALUES = {
    "K": 0.0,
    "Q": {1: 9.0, 2: 7.0, 3: 5.0},
    "R": {1: 5.0, 2: 4.0, 3: 3.0},
    "B": {1: 3.25, 2: 2.5, 3: 1.75},
    "N": 3.0,
    "W": 2.0,
    "P": 1.0,
}

SLIDING_DIRECTIONS = {
    "Q": [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)],
    "R": [(-1, 0), (1, 0), (0, -1), (0, 1)],
    "B": [(-1, -1), (-1, 1), (1, -1), (1, 1)],
}

KNIGHT_OFFSETS = [
    (-2, -1),
    (-2, 1),
    (-1, -2),
    (-1, 2),
    (1, -2),
    (1, 2),
    (2, -1),
    (2, 1),
]

KING_OFFSETS = [
    (-1, -1),
    (-1, 0),
    (-1, 1),
    (0, -1),
    (0, 1),
    (1, -1),
    (1, 0),
    (1, 1),
]

WAZIR_OFFSETS = [(-1, 0), (1, 0), (0, -1), (0, 1)]

CENTER_SQUARES = {27, 28, 35, 36}
NEAR_CENTER_SQUARES = {
    18,
    19,
    20,
    21,
    26,
    29,
    34,
    37,
    42,
    43,
    44,
    45,
}


@dataclass
class Piece:
    id: int
    color: str
    kind: str
    square: int
    origin_square: int
    tier: int = 1
    retaliation_target: Optional[int] = None
    retaliation_window: int = 0

    def clone(self) -> "Piece":
        return dataclasses.replace(self)


@dataclass
class Move:
    from_sq: int
    to_sq: int
    capture_id: Optional[int] = None


@dataclass
class MoveEvent:
    capture_happened: bool = False
    captured_piece_kind: Optional[str] = None
    captured_piece_color: Optional[str] = None
    capture_was_permanent: bool = False
    capture_permanent_reason: Optional[str] = None
    retaliation_redeployed_piece_id: Optional[int] = None
    retaliation_target_id: Optional[int] = None
    retaliation_target_kind: Optional[str] = None
    retaliation_placement_mode: Optional[str] = None
    promotion_happened: bool = False
    check_given: bool = False
    game_over: bool = False
    winner: Optional[str] = None
    termination: Optional[str] = None


@dataclass(frozen=True)
class RuleConfig:
    tier2_slider_max_range: int = 4
    tier3_slider_max_range: int = 1
    retaliation_strike_window: int = 1
    fallback_policy: str = "random"  # random | king_proximity


class GameState:
    def __init__(self, seed: int, rules: Optional[RuleConfig] = None):
        self.seed = seed
        self.rng = random.Random(seed)
        self.rules = rules if rules is not None else RuleConfig()
        self.board: List[Optional[int]] = [None] * NUM_SQUARES
        self.pieces: Dict[int, Piece] = {}
        self.next_piece_id = 1
        self.side_to_move = WHITE
        self.ply = 0
        self.terminated = False
        self.winner: Optional[str] = None
        self.termination_reason: Optional[str] = None

        self.stats = {
            "captures_total": 0,
            "retaliation_redeployments": 0,
            "retaliation_safe_target_placements": 0,
            "retaliation_circe_placements": 0,
            "retaliation_random_placements": 0,
            "permanent_removals_total": 0,
            "permanent_removals_king_capture": 0,
            "permanent_removals_retaliation_strike": 0,
            "promotions": 0,
            "checks": {WHITE: 0, BLACK: 0},
            "retarget_captures_attempted": 0,
            "retarget_captures_success": 0,
            "material_volatility": 0.0,
            "material_lead_sign_changes": 0,
            "capture_repeats_over_one": 0,
            "capture_events_by_piece_id": Counter(),
            "mean_legal_moves_white": [],
            "mean_legal_moves_black": [],
        }

        self._setup_initial_position()
        self._last_material_sign = self._material_lead_sign()

    def clone(self) -> "GameState":
        clone = GameState.__new__(GameState)
        clone.seed = self.seed
        clone.rng = random.Random()
        clone.rng.setstate(self.rng.getstate())
        clone.rules = self.rules
        clone.board = self.board.copy()
        clone.pieces = {pid: p.clone() for pid, p in self.pieces.items()}
        clone.next_piece_id = self.next_piece_id
        clone.side_to_move = self.side_to_move
        clone.ply = self.ply
        clone.terminated = self.terminated
        clone.winner = self.winner
        clone.termination_reason = self.termination_reason
        clone.stats = {
            "captures_total": self.stats["captures_total"],
            "retaliation_redeployments": self.stats["retaliation_redeployments"],
            "retaliation_safe_target_placements": self.stats[
                "retaliation_safe_target_placements"
            ],
            "retaliation_circe_placements": self.stats["retaliation_circe_placements"],
            "retaliation_random_placements": self.stats["retaliation_random_placements"],
            "permanent_removals_total": self.stats["permanent_removals_total"],
            "permanent_removals_king_capture": self.stats[
                "permanent_removals_king_capture"
            ],
            "permanent_removals_retaliation_strike": self.stats[
                "permanent_removals_retaliation_strike"
            ],
            "promotions": self.stats["promotions"],
            "checks": {
                WHITE: self.stats["checks"][WHITE],
                BLACK: self.stats["checks"][BLACK],
            },
            "retarget_captures_attempted": self.stats["retarget_captures_attempted"],
            "retarget_captures_success": self.stats["retarget_captures_success"],
            "material_volatility": self.stats["material_volatility"],
            "material_lead_sign_changes": self.stats["material_lead_sign_changes"],
            "capture_repeats_over_one": self.stats["capture_repeats_over_one"],
            "capture_events_by_piece_id": Counter(
                self.stats["capture_events_by_piece_id"]
            ),
            "mean_legal_moves_white": list(self.stats["mean_legal_moves_white"]),
            "mean_legal_moves_black": list(self.stats["mean_legal_moves_black"]),
        }
        clone._last_material_sign = self._last_material_sign
        return clone

    def _setup_initial_position(self) -> None:
        back_rank = ["R", "N", "B", "Q", "K", "B", "N", "R"]

        # Black pieces.
        for col, kind in enumerate(back_rank):
            self._add_piece(BLACK, kind, rc_to_sq(0, col), tier=1)
        for col in range(BOARD_SIZE):
            self._add_piece(BLACK, "P", rc_to_sq(1, col), tier=1)

        # White pieces.
        for col in range(BOARD_SIZE):
            self._add_piece(WHITE, "P", rc_to_sq(6, col), tier=1)
        for col, kind in enumerate(back_rank):
            self._add_piece(WHITE, kind, rc_to_sq(7, col), tier=1)

    def _add_piece(self, color: str, kind: str, square: int, tier: int = 1) -> int:
        pid = self.next_piece_id
        self.next_piece_id += 1
        piece = Piece(
            id=pid,
            color=color,
            kind=kind,
            square=square,
            origin_square=square,
            tier=tier,
        )
        self.pieces[pid] = piece
        self.board[square] = pid
        return pid

    def _remove_piece(self, pid: int) -> None:
        piece = self.pieces.get(pid)
        if piece is None:
            return
        if self.board[piece.square] == pid:
            self.board[piece.square] = None
        del self.pieces[pid]

    def _material_value(self, piece: Piece) -> float:
        value = MATERIAL_VALUES[piece.kind]
        if isinstance(value, dict):
            return value[piece.tier]
        return value

    def _material_balance(self, perspective: str) -> float:
        total = 0.0
        for piece in self.pieces.values():
            value = self._material_value(piece)
            if piece.color == perspective:
                total += value
            else:
                total -= value
        return total

    def _material_lead_sign(self) -> int:
        score = self._material_balance(WHITE)
        if score > 0.15:
            return 1
        if score < -0.15:
            return -1
        return 0

    def _note_material_dynamics(self, previous_balance_white: float) -> None:
        current_balance_white = self._material_balance(WHITE)
        self.stats["material_volatility"] += abs(current_balance_white - previous_balance_white)
        current_sign = self._material_lead_sign()
        if current_sign != self._last_material_sign and current_sign != 0:
            self.stats["material_lead_sign_changes"] += 1
        if current_sign != 0:
            self._last_material_sign = current_sign

    def _opponent(self, color: str) -> str:
        return BLACK if color == WHITE else WHITE

    def _sliding_max_distance(self, piece: Piece) -> int:
        if piece.kind not in ("Q", "R", "B"):
            return 0
        if piece.tier == 1:
            return 7
        if piece.tier == 2:
            return self.rules.tier2_slider_max_range
        return self.rules.tier3_slider_max_range

    def _is_target_piece(self, piece: Piece, attacker_color: str) -> bool:
        return piece.color != attacker_color and piece.kind != "K"

    def _piece_can_attack_square(
        self,
        piece: Piece,
        from_sq: int,
        to_sq: int,
        board_override: Optional[List[Optional[int]]] = None,
    ) -> bool:
        board = board_override if board_override is not None else self.board
        fr, fc = sq_to_rc(from_sq)
        tr, tc = sq_to_rc(to_sq)
        dr = tr - fr
        dc = tc - fc

        if piece.kind in ("Q", "R", "B"):
            max_dist = self._sliding_max_distance(piece)
            if max_dist <= 0:
                return False
            directions = SLIDING_DIRECTIONS[piece.kind]
            for step_r, step_c in directions:
                r, c = fr + step_r, fc + step_c
                dist = 1
                while in_bounds(r, c) and dist <= max_dist:
                    sq = rc_to_sq(r, c)
                    if sq == to_sq:
                        return True
                    if board[sq] is not None:
                        break
                    r += step_r
                    c += step_c
                    dist += 1
            return False

        if piece.kind == "N":
            return (dr, dc) in KNIGHT_OFFSETS

        if piece.kind == "W":
            return (dr, dc) in WAZIR_OFFSETS

        if piece.kind == "K":
            return (dr, dc) in KING_OFFSETS

        if piece.kind == "P":
            forward = -1 if piece.color == WHITE else 1
            return dr == forward and abs(dc) == 1

        return False

    def _generate_piece_moves(self, piece: Piece) -> List[Move]:
        moves: List[Move] = []
        fr, fc = sq_to_rc(piece.square)

        if piece.kind in ("Q", "R", "B"):
            max_dist = self._sliding_max_distance(piece)
            for dr, dc in SLIDING_DIRECTIONS[piece.kind]:
                r, c = fr + dr, fc + dc
                dist = 1
                while in_bounds(r, c) and dist <= max_dist:
                    sq = rc_to_sq(r, c)
                    occupant = self.board[sq]
                    if occupant is None:
                        moves.append(Move(piece.square, sq, None))
                    else:
                        target = self.pieces[occupant]
                        if target.color != piece.color:
                            moves.append(Move(piece.square, sq, occupant))
                        break
                    r += dr
                    c += dc
                    dist += 1
            return moves

        if piece.kind == "N":
            for dr, dc in KNIGHT_OFFSETS:
                r, c = fr + dr, fc + dc
                if not in_bounds(r, c):
                    continue
                sq = rc_to_sq(r, c)
                occupant = self.board[sq]
                if occupant is None:
                    moves.append(Move(piece.square, sq, None))
                else:
                    target = self.pieces[occupant]
                    if target.color != piece.color:
                        moves.append(Move(piece.square, sq, occupant))
            return moves

        if piece.kind == "W":
            for dr, dc in WAZIR_OFFSETS:
                r, c = fr + dr, fc + dc
                if not in_bounds(r, c):
                    continue
                sq = rc_to_sq(r, c)
                occupant = self.board[sq]
                if occupant is None:
                    moves.append(Move(piece.square, sq, None))
                else:
                    target = self.pieces[occupant]
                    if target.color != piece.color:
                        moves.append(Move(piece.square, sq, occupant))
            return moves

        if piece.kind == "K":
            for dr, dc in KING_OFFSETS:
                r, c = fr + dr, fc + dc
                if not in_bounds(r, c):
                    continue
                sq = rc_to_sq(r, c)
                occupant = self.board[sq]
                if occupant is None:
                    moves.append(Move(piece.square, sq, None))
                else:
                    target = self.pieces[occupant]
                    if target.color != piece.color:
                        moves.append(Move(piece.square, sq, occupant))
            return moves

        # Pawn movement (simplified: no en passant).
        if piece.kind == "P":
            forward = -1 if piece.color == WHITE else 1
            one_r, one_c = fr + forward, fc
            if in_bounds(one_r, one_c):
                one_sq = rc_to_sq(one_r, one_c)
                if self.board[one_sq] is None:
                    moves.append(Move(piece.square, one_sq, None))

                    start_row = 6 if piece.color == WHITE else 1
                    two_r = fr + (2 * forward)
                    if fr == start_row and in_bounds(two_r, one_c):
                        two_sq = rc_to_sq(two_r, one_c)
                        if self.board[two_sq] is None:
                            moves.append(Move(piece.square, two_sq, None))

            for dc in (-1, 1):
                cr, cc = fr + forward, fc + dc
                if not in_bounds(cr, cc):
                    continue
                target_sq = rc_to_sq(cr, cc)
                occupant = self.board[target_sq]
                if occupant is None:
                    continue
                target = self.pieces[occupant]
                if target.color != piece.color:
                    moves.append(Move(piece.square, target_sq, occupant))
            return moves

        return moves

    def _all_pseudo_legal_moves(self, color: str) -> List[Move]:
        moves: List[Move] = []
        for piece in self.pieces.values():
            if piece.color != color:
                continue
            moves.extend(self._generate_piece_moves(piece))
        return moves

    def _king_square(self, color: str) -> Optional[int]:
        for piece in self.pieces.values():
            if piece.color == color and piece.kind == "K":
                return piece.square
        return None

    def is_square_attacked(self, square: int, by_color: str) -> bool:
        for piece in self.pieces.values():
            if piece.color != by_color:
                continue
            if self._piece_can_attack_square(piece, piece.square, square):
                return True
        return False

    def is_in_check(self, color: str) -> bool:
        king_sq = self._king_square(color)
        if king_sq is None:
            return True
        return self.is_square_attacked(king_sq, self._opponent(color))

    def legal_moves(self, color: str) -> List[Move]:
        legal: List[Move] = []
        for move in self._all_pseudo_legal_moves(color):
            sim = self.clone()
            sim._apply_move_internal(move, resolve_terminal=False)
            if not sim.is_in_check(color):
                legal.append(move)
        return legal

    def _demote_piece_for_capture(self, piece: Piece) -> bool:
        """Demote captured piece by one tier/stage.

        Returns True if piece survives and should be redeployed,
        False if piece is permanently removed.
        """

        if piece.kind == "P":
            return False

        if piece.kind in ("Q", "R", "B"):
            if piece.tier == 1:
                piece.tier = 2
                return True
            if piece.tier == 2:
                piece.tier = 3
                return True
            piece.kind = "P"
            piece.tier = 1
            return True

        if piece.kind == "N":
            piece.kind = "W"
            piece.tier = 1
            return True

        if piece.kind == "W":
            piece.kind = "P"
            piece.tier = 1
            return True

        return False

    def _retaliation_candidates(
        self, demoted_piece: Piece
    ) -> List[Tuple[int, int]]:
        """Return (target_id, square) candidates by highest target value class only."""

        empty_squares = [sq for sq, pid in enumerate(self.board) if pid is None]
        if not empty_squares:
            return []

        targets = [
            piece
            for piece in self.pieces.values()
            if self._is_target_piece(piece, demoted_piece.color)
        ]

        if not targets:
            return []

        targets_by_priority: Dict[int, List[Piece]] = defaultdict(list)
        for target in targets:
            pr = PIECE_VALUE_PRIORITY.get(target.kind, 0)
            targets_by_priority[pr].append(target)

        for pr in sorted(targets_by_priority.keys(), reverse=True):
            candidates: List[Tuple[int, int]] = []
            for target in targets_by_priority[pr]:
                for sq in empty_squares:
                    if not self._piece_can_attack_square(
                        demoted_piece,
                        sq,
                        target.square,
                    ):
                        continue

                    # Build temporary board occupancy for the safety test.
                    temp_board = self.board.copy()
                    temp_board[sq] = demoted_piece.id
                    if self._piece_can_attack_square(
                        target,
                        target.square,
                        sq,
                        board_override=temp_board,
                    ):
                        continue
                    candidates.append((target.id, sq))

            if candidates:
                return candidates

        return []

    def _redeploy_with_retaliation(self, piece: Piece, event: MoveEvent) -> None:
        candidates = self._retaliation_candidates(piece)
        self.stats["retaliation_redeployments"] += 1

        if candidates:
            target_id, sq = self.rng.choice(candidates)
            self.board[sq] = piece.id
            piece.square = sq
            piece.retaliation_target = target_id
            piece.retaliation_window = self.rules.retaliation_strike_window
            event.retaliation_target_id = target_id
            if target_id in self.pieces:
                event.retaliation_target_kind = self.pieces[target_id].kind
            event.retaliation_placement_mode = "safe_target"
            self.stats["retaliation_safe_target_placements"] += 1
            return

        circe_sq = piece.origin_square
        if self.board[circe_sq] is None:
            self.board[circe_sq] = piece.id
            piece.square = circe_sq
            piece.retaliation_target = None
            piece.retaliation_window = 0
            event.retaliation_placement_mode = "circe"
            self.stats["retaliation_circe_placements"] += 1
            return

        empty_squares = [sq for sq, pid in enumerate(self.board) if pid is None]
        if not empty_squares:
            # Should not happen in normal play, but keep piece removed if no space.
            self._remove_piece(piece.id)
            return

        sq = self._pick_fallback_square(piece, empty_squares)
        self.board[sq] = piece.id
        piece.square = sq
        piece.retaliation_target = None
        piece.retaliation_window = 0
        event.retaliation_placement_mode = (
            "king_proximity" if self.rules.fallback_policy == "king_proximity" else "random"
        )
        self.stats["retaliation_random_placements"] += 1

    def _pick_fallback_square(self, piece: Piece, empty_squares: Sequence[int]) -> int:
        if self.rules.fallback_policy != "king_proximity":
            return self.rng.choice(list(empty_squares))

        enemy_king_sq = self._king_square(self._opponent(piece.color))
        if enemy_king_sq is None:
            return self.rng.choice(list(empty_squares))

        ekr, ekc = sq_to_rc(enemy_king_sq)
        best: List[int] = []
        best_dist: Optional[int] = None
        for sq in empty_squares:
            r, c = sq_to_rc(sq)
            dist = max(abs(r - ekr), abs(c - ekc))
            if best_dist is None or dist < best_dist:
                best_dist = dist
                best = [sq]
            elif dist == best_dist:
                best.append(sq)
        return self.rng.choice(best)

    def _apply_capture(
        self,
        mover: Piece,
        captured_id: int,
        event: MoveEvent,
    ) -> None:
        if captured_id not in self.pieces:
            return

        captured = self.pieces[captured_id]

        event.capture_happened = True
        event.captured_piece_kind = captured.kind
        event.captured_piece_color = captured.color
        self.stats["captures_total"] += 1

        self.stats["capture_events_by_piece_id"][captured_id] += 1
        if self.stats["capture_events_by_piece_id"][captured_id] == 2:
            self.stats["capture_repeats_over_one"] += 1

        if captured.kind == "K":
            self._remove_piece(captured_id)
            self.terminated = True
            self.winner = mover.color
            self.termination_reason = "king_captured"
            event.game_over = True
            event.winner = mover.color
            event.termination = "king_captured"
            return

        permanent = False
        reason = None

        if mover.kind == "K":
            permanent = True
            reason = "king_capture_rule"

        if mover.retaliation_window > 0:
            self.stats["retarget_captures_attempted"] += 1
            if mover.retaliation_target == captured_id:
                permanent = True
                reason = "retaliation_strike"
                self.stats["retarget_captures_success"] += 1

        if permanent:
            self._remove_piece(captured_id)
            event.capture_was_permanent = True
            event.capture_permanent_reason = reason
            self.stats["permanent_removals_total"] += 1
            if reason == "king_capture_rule":
                self.stats["permanent_removals_king_capture"] += 1
            elif reason == "retaliation_strike":
                self.stats["permanent_removals_retaliation_strike"] += 1
            return

        # Normal capture: pawn removed, non-pawn demotes and redeploys.
        # Only clear square if captured piece still occupies it (mover may already be there).
        if self.board[captured.square] == captured_id:
            self.board[captured.square] = None
        survives = self._demote_piece_for_capture(captured)
        if not survives:
            self._remove_piece(captured_id)
            return

        # Survives as demoted piece and redeploys.
        captured.retaliation_target = None
        captured.retaliation_window = 0
        self._redeploy_with_retaliation(captured, event)

    def _apply_move_internal(self, move: Move, resolve_terminal: bool = False) -> MoveEvent:
        event = MoveEvent()
        if self.terminated:
            event.game_over = True
            event.winner = self.winner
            event.termination = self.termination_reason
            return event

        pid = self.board[move.from_sq]
        if pid is None:
            raise ValueError(f"No piece at square {move.from_sq}.")

        mover = self.pieces[pid]
        previous_balance_white = self._material_balance(WHITE)

        self.board[move.from_sq] = None
        mover.square = move.to_sq

        captured_id = self.board[move.to_sq]
        self.board[move.to_sq] = mover.id
        if captured_id is not None:
            if self.pieces[captured_id].color == mover.color:
                raise ValueError("Illegal self-capture encountered.")
            self._apply_capture(mover, captured_id, event)
            if self.terminated:
                return event

        # If this piece had a one-move retaliation strike window, consume it.
        if mover.retaliation_window > 0:
            mover.retaliation_window -= 1
            if mover.retaliation_window <= 0:
                mover.retaliation_window = 0
                mover.retaliation_target = None

        # Promotion is always to full-tier queen in this simulation.
        if mover.kind == "P":
            row, _ = sq_to_rc(mover.square)
            if (mover.color == WHITE and row == 0) or (
                mover.color == BLACK and row == BOARD_SIZE - 1
            ):
                mover.kind = "Q"
                mover.tier = 1
                event.promotion_happened = True
                self.stats["promotions"] += 1

        self._note_material_dynamics(previous_balance_white)

        self.side_to_move = self._opponent(self.side_to_move)
        self.ply += 1

        # Check state after move.
        if self.is_in_check(self.side_to_move):
            self.stats["checks"][self.side_to_move] += 1
            event.check_given = True

        if resolve_terminal:
            legal_after = self.legal_moves(self.side_to_move)
            if not legal_after:
                if self.is_in_check(self.side_to_move):
                    self.terminated = True
                    self.winner = self._opponent(self.side_to_move)
                    self.termination_reason = "checkmate"
                else:
                    self.terminated = True
                    self.winner = DRAW
                    self.termination_reason = "stalemate"
                event.game_over = True
                event.winner = self.winner
                event.termination = self.termination_reason

        return event

    def apply_move(self, move: Move) -> MoveEvent:
        return self._apply_move_internal(move, resolve_terminal=False)


def in_bounds(r: int, c: int) -> bool:
    return 0 <= r < BOARD_SIZE and 0 <= c < BOARD_SIZE


def sq_to_rc(sq: int) -> Tuple[int, int]:
    return divmod(sq, BOARD_SIZE)


def rc_to_sq(r: int, c: int) -> int:
    return (r * BOARD_SIZE) + c


def evaluate_position(game: GameState, color: str) -> float:
    score = game._material_balance(color)

    # Bonus for center occupancy and retaliation pressure.
    for piece in game.pieces.values():
        sign = 1.0 if piece.color == color else -1.0
        if piece.square in CENTER_SQUARES:
            score += sign * 0.2
        elif piece.square in NEAR_CENTER_SQUARES:
            score += sign * 0.08

        if piece.retaliation_window == 1 and piece.retaliation_target is not None:
            score += sign * 0.35

    # Light king safety: discourage being in check.
    if game.is_in_check(color):
        score -= 0.65
    if game.is_in_check(game._opponent(color)):
        score += 0.65

    return score


def choose_move(game: GameState, legal_moves: Sequence[Move], explore: float = 0.22) -> Move:
    side = game.side_to_move

    # Exploration branch.
    if game.rng.random() < explore:
        return game.rng.choice(list(legal_moves))

    # Evaluate one-ply outcomes.
    scored: List[Tuple[float, Move]] = []
    for mv in legal_moves:
        sim = game.clone()
        event = sim.apply_move(mv)
        if event.game_over:
            if event.winner == side:
                scored.append((10_000.0, mv))
                continue
            if event.winner == DRAW:
                scored.append((0.0, mv))
                continue
            scored.append((-10_000.0, mv))
            continue

        base = evaluate_position(sim, side)

        # Immediate tactical preference for captures and promotion.
        tactical = 0.0
        if mv.capture_id is not None and mv.capture_id in game.pieces:
            tactical += game._material_value(game.pieces[mv.capture_id])
        if event.capture_was_permanent:
            tactical += 1.5
        if event.promotion_happened:
            tactical += 1.0
        if event.check_given:
            tactical += 0.4

        noise = game.rng.uniform(-0.12, 0.12)
        scored.append((base + tactical + noise, mv))

    scored.sort(key=lambda x: x[0], reverse=True)

    # Pick from top-k with weighted preference to avoid deterministic play.
    top_k = scored[: min(4, len(scored))]
    max_score = top_k[0][0]
    weights = [math.exp((score - max_score) / 2.5) for score, _ in top_k]
    total = sum(weights)
    pick = game.rng.random() * total
    running = 0.0
    for (score, mv), w in zip(top_k, weights):
        _ = score
        running += w
        if running >= pick:
            return mv

    return top_k[0][1]


def run_single_game(seed: int, max_plies: int, rules: RuleConfig) -> Dict[str, object]:
    game = GameState(seed=seed, rules=rules)

    move_log: List[Dict[str, object]] = []

    while not game.terminated and game.ply < max_plies:
        legal = game.legal_moves(game.side_to_move)

        if game.side_to_move == WHITE:
            game.stats["mean_legal_moves_white"].append(len(legal))
        else:
            game.stats["mean_legal_moves_black"].append(len(legal))

        if not legal:
            if game.is_in_check(game.side_to_move):
                game.winner = game._opponent(game.side_to_move)
                game.termination_reason = "checkmate"
            else:
                game.winner = DRAW
                game.termination_reason = "stalemate"
            game.terminated = True
            break

        move = choose_move(game, legal)
        mover_id = game.board[move.from_sq]
        mover_kind = game.pieces[mover_id].kind if mover_id is not None else None
        mover_color = game.side_to_move

        event = game.apply_move(move)

        move_log.append(
            {
                "ply": game.ply,
                "side": mover_color,
                "mover_kind": mover_kind,
                "from_sq": move.from_sq,
                "to_sq": move.to_sq,
                "capture": event.capture_happened,
                "captured_kind": event.captured_piece_kind,
                "capture_permanent": event.capture_was_permanent,
                "permanent_reason": event.capture_permanent_reason,
                "retaliation_mode": event.retaliation_placement_mode,
                "retaliation_target_kind": event.retaliation_target_kind,
                "promotion": event.promotion_happened,
                "check": event.check_given,
            }
        )

    if not game.terminated:
        game.terminated = True
        game.winner = DRAW
        game.termination_reason = "max_plies"

    white_legal = game.stats["mean_legal_moves_white"]
    black_legal = game.stats["mean_legal_moves_black"]

    return {
        "seed": seed,
        "winner": game.winner,
        "termination": game.termination_reason,
        "plies": game.ply,
        "captures_total": game.stats["captures_total"],
        "retaliation_redeployments": game.stats["retaliation_redeployments"],
        "retaliation_safe_target_placements": game.stats[
            "retaliation_safe_target_placements"
        ],
        "retaliation_circe_placements": game.stats["retaliation_circe_placements"],
        "retaliation_random_placements": game.stats["retaliation_random_placements"],
        "permanent_removals_total": game.stats["permanent_removals_total"],
        "permanent_removals_king_capture": game.stats["permanent_removals_king_capture"],
        "permanent_removals_retaliation_strike": game.stats[
            "permanent_removals_retaliation_strike"
        ],
        "retarget_captures_attempted": game.stats["retarget_captures_attempted"],
        "retarget_captures_success": game.stats["retarget_captures_success"],
        "promotions": game.stats["promotions"],
        "white_checks": game.stats["checks"][WHITE],
        "black_checks": game.stats["checks"][BLACK],
        "material_volatility": round(game.stats["material_volatility"], 3),
        "material_lead_sign_changes": game.stats["material_lead_sign_changes"],
        "capture_repeats_over_one": game.stats["capture_repeats_over_one"],
        "mean_legal_moves_white": (
            round(statistics.mean(white_legal), 3) if white_legal else 0.0
        ),
        "mean_legal_moves_black": (
            round(statistics.mean(black_legal), 3) if black_legal else 0.0
        ),
        "move_log": move_log,
    }


def aggregate_results(games: Sequence[Dict[str, object]]) -> Dict[str, object]:
    n = len(games)
    winners = Counter(g["winner"] for g in games)
    terminations = Counter(g["termination"] for g in games)

    plies = [int(g["plies"]) for g in games]
    captures = [int(g["captures_total"]) for g in games]
    redeploys = [int(g["retaliation_redeployments"]) for g in games]
    safe_redeploys = [int(g["retaliation_safe_target_placements"]) for g in games]
    circe_redeploys = [int(g["retaliation_circe_placements"]) for g in games]
    random_redeploys = [int(g["retaliation_random_placements"]) for g in games]
    permanent = [int(g["permanent_removals_total"]) for g in games]
    strike_perm = [int(g["permanent_removals_retaliation_strike"]) for g in games]
    king_perm = [int(g["permanent_removals_king_capture"]) for g in games]
    ret_attempts = [int(g["retarget_captures_attempted"]) for g in games]
    ret_success = [int(g["retarget_captures_success"]) for g in games]
    promotions = [int(g["promotions"]) for g in games]
    volatility = [float(g["material_volatility"]) for g in games]
    lead_changes = [int(g["material_lead_sign_changes"]) for g in games]

    def mean(values: Sequence[float]) -> float:
        return float(statistics.mean(values)) if values else 0.0

    def median(values: Sequence[float]) -> float:
        return float(statistics.median(values)) if values else 0.0

    total_attempts = sum(ret_attempts)
    total_success = sum(ret_success)

    total_redeploys = sum(redeploys)
    safe_share = (sum(safe_redeploys) / total_redeploys) if total_redeploys else 0.0
    circe_share = (sum(circe_redeploys) / total_redeploys) if total_redeploys else 0.0
    random_share = (sum(random_redeploys) / total_redeploys) if total_redeploys else 0.0

    return {
        "num_games": n,
        "winner_counts": dict(winners),
        "termination_counts": dict(terminations),
        "mean_plies": round(mean(plies), 3),
        "median_plies": round(median(plies), 3),
        "mean_captures": round(mean(captures), 3),
        "mean_redeployments": round(mean(redeploys), 3),
        "mean_permanent_removals": round(mean(permanent), 3),
        "mean_permanent_by_strike": round(mean(strike_perm), 3),
        "mean_permanent_by_king": round(mean(king_perm), 3),
        "retaliation_target_capture_attempt_rate": round(
            (total_attempts / sum(captures)) if sum(captures) else 0.0,
            4,
        ),
        "retaliation_target_capture_success_rate": round(
            (total_success / total_attempts) if total_attempts else 0.0,
            4,
        ),
        "safe_redeploy_share": round(safe_share, 4),
        "circe_redeploy_share": round(circe_share, 4),
        "random_redeploy_share": round(random_share, 4),
        "mean_promotions": round(mean(promotions), 3),
        "mean_material_volatility": round(mean(volatility), 3),
        "mean_lead_sign_changes": round(mean(lead_changes), 3),
    }


def generate_recommendations(summary: Dict[str, object]) -> List[Dict[str, str]]:
    recs: List[Dict[str, str]] = []

    mean_plies = float(summary["mean_plies"])
    draw_count = int(summary["winner_counts"].get(DRAW, 0))
    num_games = int(summary["num_games"])
    draw_rate = (draw_count / num_games) if num_games else 0.0

    safe_share = float(summary["safe_redeploy_share"])
    random_share = float(summary["random_redeploy_share"])
    strike_success = float(summary["retaliation_target_capture_success_rate"])
    mean_permanent = float(summary["mean_permanent_removals"])
    volatility = float(summary["mean_material_volatility"])

    # Baseline rule tuning logic from empirical indicators.
    if mean_plies > 130 or draw_rate > 0.35:
        recs.append(
            {
                "rule_adjustment": "Increase decay pressure: Tier-2 slider max range from 4 -> 3",
                "why": "Games are trending long / drawish; reducing Tier-2 range should accelerate conversion and reduce repeated resets.",
            }
        )

    if safe_share < 0.45 and random_share > 0.2:
        recs.append(
            {
                "rule_adjustment": "When no safe threat exists, prefer Circe; if blocked, use nearest empty square to the opposing king instead of random",
                "why": "High random fallback suggests retaliation pressure is often unfocused; directed fallback should create clearer counterplay and tactical clusters.",
            }
        )

    if strike_success < 0.22:
        recs.append(
            {
                "rule_adjustment": "Extend retaliation strike window from exactly next move to next 2 moves of that piece",
                "why": "Targeted permanent removals are rare; a slightly wider window increases payoff for retaliation planning.",
            }
        )

    if mean_permanent < 4.5:
        recs.append(
            {
                "rule_adjustment": "Allow Tier-3 sliders to permanently remove only when capturing a marked retaliation target",
                "why": "Low permanent-removal rate can make material too sticky; adding a conditional conversion path increases game resolution without deleting the core loop.",
            }
        )

    if volatility < 30:
        recs.append(
            {
                "rule_adjustment": "Add a soft repetition breaker: after a piece is captured 3 times, its next capture removes it permanently",
                "why": "Low volatility implies repeated recycling without strategic payoff; this limits grindy loops while preserving Matryoshka identity.",
            }
        )

    if not recs:
        recs.append(
            {
                "rule_adjustment": "Keep core rules, but test a minor targeting tweak: ties in safe-threat squares choose the square closest to enemy king",
                "why": "Current metrics already show strong interaction; king-proximity tie-break can raise tactical sharpness without major complexity.",
            }
        )

    return recs


def write_outputs(
    output_dir: str,
    games: Sequence[Dict[str, object]],
    summary: Dict[str, object],
    recommendations: Sequence[Dict[str, str]],
    rules: RuleConfig,
) -> None:
    os.makedirs(output_dir, exist_ok=True)

    game_rows = []
    for idx, game in enumerate(games, start=1):
        row = dict(game)
        row["game_id"] = idx
        row.pop("move_log", None)
        game_rows.append(row)

    csv_path = os.path.join(output_dir, "games.csv")
    if game_rows:
        fieldnames = ["game_id"] + [k for k in game_rows[0].keys() if k != "game_id"]
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(game_rows)

    move_csv_path = os.path.join(output_dir, "moves.csv")
    move_fieldnames = [
        "game_id",
        "ply",
        "side",
        "mover_kind",
        "from_sq",
        "to_sq",
        "capture",
        "captured_kind",
        "capture_permanent",
        "permanent_reason",
        "retaliation_mode",
        "retaliation_target_kind",
        "promotion",
        "check",
    ]
    with open(move_csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=move_fieldnames)
        writer.writeheader()
        for game_id, game in enumerate(games, start=1):
            for row in game["move_log"]:
                out = dict(row)
                out["game_id"] = game_id
                writer.writerow(out)

    summary_path = os.path.join(output_dir, "summary.json")
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "summary": summary,
                "recommendations": recommendations,
                "rules": dataclasses.asdict(rules),
                "notes": {
                    "model_simplifications": [
                        "No castling",
                        "No en passant",
                        "Promotion fixed to queen",
                    ]
                },
            },
            f,
            indent=2,
        )

    report_path = os.path.join(output_dir, "analysis.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("# Matryoshka Chess Simulation Analysis\n\n")
        f.write("## Run Parameters\n")
        f.write(f"- Number of games: {summary['num_games']}\n")
        f.write("- Strategy: one-ply heuristic with controlled randomness\n")
        f.write(
            f"- Rule parameters: tier2_range={rules.tier2_slider_max_range}, strike_window={rules.retaliation_strike_window}, fallback_policy={rules.fallback_policy}\n"
        )
        f.write(
            "- Engine simplifications: no castling, no en passant, queen-only promotion\n\n"
        )

        f.write("## Core Metrics\n")
        f.write(f"- Mean plies: {summary['mean_plies']}\n")
        f.write(f"- Median plies: {summary['median_plies']}\n")
        f.write(f"- Winner counts: {summary['winner_counts']}\n")
        f.write(f"- Termination counts: {summary['termination_counts']}\n")
        f.write(f"- Mean captures per game: {summary['mean_captures']}\n")
        f.write(f"- Mean redeployments per game: {summary['mean_redeployments']}\n")
        f.write(f"- Mean permanent removals: {summary['mean_permanent_removals']}\n")
        f.write(
            f"- Retaliation target capture success rate: {summary['retaliation_target_capture_success_rate']}\n"
        )
        f.write(f"- Safe redeploy share: {summary['safe_redeploy_share']}\n")
        f.write(f"- Circe redeploy share: {summary['circe_redeploy_share']}\n")
        f.write(f"- Random redeploy share: {summary['random_redeploy_share']}\n")
        f.write(f"- Mean material volatility: {summary['mean_material_volatility']}\n")
        f.write(f"- Mean lead sign changes: {summary['mean_lead_sign_changes']}\n\n")

        f.write("## Trend Interpretation\n")
        if summary["mean_plies"] > 130:
            f.write(
                "- Games are relatively long; capture recycling appears to delay decisive conversion.\n"
            )
        else:
            f.write("- Game length is moderate; the recycle loop is active but not dominant.\n")

        if summary["safe_redeploy_share"] < 0.5:
            f.write(
                "- Less than half of redeployments create immediate safe threats, so many retaliations are positional rather than forcing.\n"
            )
        else:
            f.write(
                "- Safe-threat redeployments are common, which keeps tactical pressure high after captures.\n"
            )

        if summary["retaliation_target_capture_success_rate"] < 0.25:
            f.write(
                "- Marked-target conversions are low, suggesting the one-move strike window may be too tight in practice.\n"
            )
        else:
            f.write(
                "- Marked-target conversions occur often enough to materially affect piece permanence.\n"
            )

        if summary["mean_permanent_removals"] < 5.0:
            f.write(
                "- Permanent removal is relatively sparse, which can increase cyclic recapture loops.\n"
            )
        else:
            f.write(
                "- Permanent removals are frequent enough to steadily simplify positions.\n"
            )

        f.write("\n## Recommended Rule Adjustments for More Interesting Play\n")
        for i, rec in enumerate(recommendations, start=1):
            f.write(f"{i}. **{rec['rule_adjustment']}**\n")
            f.write(f"   - Rationale: {rec['why']}\n")


def run_batch(
    num_games: int,
    max_plies: int,
    seed: int,
    rules: RuleConfig,
) -> Tuple[List[Dict[str, object]], Dict[str, object], List[Dict[str, str]]]:
    games: List[Dict[str, object]] = []
    for i in range(num_games):
        gseed = seed + (i * 17)
        games.append(run_single_game(seed=gseed, max_plies=max_plies, rules=rules))
    summary = aggregate_results(games)
    recommendations = generate_recommendations(summary)
    return games, summary, recommendations


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Simulate Matryoshka Chess games.")
    parser.add_argument(
        "--games",
        type=int,
        default=200,
        help="Number of self-play games to simulate (default: 200)",
    )
    parser.add_argument(
        "--max-plies",
        type=int,
        default=260,
        help="Max half-moves per game before declaring draw (default: 260)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Base RNG seed (default: 42)",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="outputs",
        help="Directory for CSV/JSON/Markdown outputs (default: outputs)",
    )
    parser.add_argument(
        "--tier2-range",
        type=int,
        default=4,
        help="Tier-2 max range for sliders (default: 4)",
    )
    parser.add_argument(
        "--strike-window",
        type=int,
        default=1,
        help="Retaliation strike window in moves of that piece (default: 1)",
    )
    parser.add_argument(
        "--fallback-policy",
        type=str,
        default="random",
        choices=["random", "king_proximity"],
        help="Fallback placement policy when no safe target and Circe unavailable",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    rules = RuleConfig(
        tier2_slider_max_range=args.tier2_range,
        tier3_slider_max_range=1,
        retaliation_strike_window=args.strike_window,
        fallback_policy=args.fallback_policy,
    )
    games, summary, recommendations = run_batch(
        num_games=args.games,
        max_plies=args.max_plies,
        seed=args.seed,
        rules=rules,
    )
    write_outputs(args.output_dir, games, summary, recommendations, rules=rules)

    print("Simulation complete.")
    print(f"Games: {summary['num_games']}")
    print(f"Winner counts: {summary['winner_counts']}")
    print(f"Termination counts: {summary['termination_counts']}")
    print(f"Mean plies: {summary['mean_plies']}")
    print(f"Mean captures: {summary['mean_captures']}")
    print(f"Mean redeployments: {summary['mean_redeployments']}")
    print(f"Mean permanent removals: {summary['mean_permanent_removals']}")
    print(
        "Retaliation target capture success rate: "
        f"{summary['retaliation_target_capture_success_rate']}"
    )
    print(f"Safe redeploy share: {summary['safe_redeploy_share']}")
    print(
        "Rules: "
        f"tier2_range={rules.tier2_slider_max_range}, "
        f"strike_window={rules.retaliation_strike_window}, "
        f"fallback_policy={rules.fallback_policy}"
    )
    print(f"Output directory: {args.output_dir}")


if __name__ == "__main__":
    main()
