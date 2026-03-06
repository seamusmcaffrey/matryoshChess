#!/usr/bin/env python3
"""Variant study simulator for Matryoshka/Normal/Circe/Anticirce chess.

Capabilities:
- models multiple rulesets under a shared engine and light strategy policy
- supports an optional "king infinite kill" mode (queen-line capture range)
- runs large variant sweeps and exports combined data tables
- validates whether sample size (e.g., 1k games) appears statistically settled

Engine simplifications:
- No castling
- No en passant
- Promotion always to queen
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import hashlib
import json
import math
import multiprocessing as mp
import os
import random
import statistics
from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

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
    "C": 2,
    "D": 2,
    "P": 1,
}

MATERIAL_VALUES = {
    "K": 0.0,
    "Q": {1: 9.0, 2: 7.0, 3: 5.0},
    "R": {1: 5.0, 2: 4.0, 3: 3.0},
    "B": {1: 3.25, 2: 2.5, 3: 1.75},
    "N": 3.0,
    "W": 2.0,
    "C": 2.2,
    "D": 2.0,
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
CAMEL_OFFSETS = [
    (-3, -1),
    (-3, 1),
    (-1, -3),
    (-1, 3),
    (1, -3),
    (1, 3),
    (3, -1),
    (3, 1),
]
DIAGONAL_STEP_OFFSETS = [(-1, -1), (-1, 1), (1, -1), (1, 1)]

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
    permakill_vulnerable: int = 0
    crippled: bool = False

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
    ruleset: str = "matryoshka"  # matryoshka | normal | circe | anticirce
    tier2_slider_max_range: int = 4
    tier3_slider_max_range: int = 1
    retaliation_strike_window: int = 1
    fallback_policy: str = "random"  # random | king_proximity | nearest_circe
    king_infinite_kill: bool = False
    king_move_mode: str = "normal"  # normal | king_dash | king_k_range | king_capture_line
    king_dash_max: int = 2
    king_k_range: int = 2
    king_capture_line_range: int = 2
    king_capture_insta_kill: str = "on"  # on | off | adjacent_only
    retaliation_enabled: bool = True
    retaliation_targeting: str = "highest_safe"  # highest_safe | localized_safe | top2_pool_safe | highest_unsafe | any_unsafe
    retaliation_local_radius: int = 4
    retaliation_tiebreak: str = "random"  # random | max_threat | min_king_distance
    strike_effect: str = "perma_kill"  # perma_kill | double_demote
    retaliation_mode: str = "defender_strike"  # defender_strike | attacker_rekill
    stalemate_is_loss: bool = False
    ko_repetition_illegal: bool = False
    doom_clock_full_moves: int = 0
    doom_clock_effect: str = (
        "demote_random_non_king"  # demote_random_non_king | collapse_weakest | bonus_capture_damage
    )
    quiet_halfmove_limit: int = 0
    knight_decay_mode: str = "wazir"  # wazir | camel | diag_step
    collapse_target: str = "pawn"  # pawn | crippled_pawn
    crippled_pawn_can_promote: bool = False
    win_condition: str = "checkmate_or_king_capture"  # checkmate_only | checkmate_or_king_capture


class GameState:
    def __init__(self, seed: int, rules: Optional[RuleConfig] = None, start_side: str = WHITE):
        self.seed = seed
        self.rng = random.Random(seed)
        self.rules = rules if rules is not None else RuleConfig()
        self.board: List[Optional[int]] = [None] * NUM_SQUARES
        self.pieces: Dict[int, Piece] = {}
        self.next_piece_id = 1
        self.side_to_move = start_side
        self.ply = 0
        self.terminated = False
        self.winner: Optional[str] = None
        self.termination_reason: Optional[str] = None
        self.no_permanent_halfmoves = 0
        self.quiet_halfmoves = 0
        self.bonus_capture_damage = 0

        self.stats = {
            "captures_total": 0,
            "retaliation_redeployments": 0,
            "retaliation_safe_target_placements": 0,
            "retaliation_circe_placements": 0,
            "retaliation_random_placements": 0,
            "circe_captured_rebirths": 0,
            "anticirce_attacker_rebirths": 0,
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
            "doom_triggers": 0,
            "doom_forced_removals": 0,
        }

        self._setup_initial_position()
        self._last_material_sign = self._material_lead_sign()
        self.position_history: Counter[str] = Counter()
        self.position_history[self._position_hash()] = 1

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
        clone.no_permanent_halfmoves = self.no_permanent_halfmoves
        clone.quiet_halfmoves = self.quiet_halfmoves
        clone.bonus_capture_damage = self.bonus_capture_damage
        clone.stats = {
            "captures_total": self.stats["captures_total"],
            "retaliation_redeployments": self.stats["retaliation_redeployments"],
            "retaliation_safe_target_placements": self.stats[
                "retaliation_safe_target_placements"
            ],
            "retaliation_circe_placements": self.stats["retaliation_circe_placements"],
            "retaliation_random_placements": self.stats["retaliation_random_placements"],
            "circe_captured_rebirths": self.stats["circe_captured_rebirths"],
            "anticirce_attacker_rebirths": self.stats["anticirce_attacker_rebirths"],
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
            "doom_triggers": self.stats["doom_triggers"],
            "doom_forced_removals": self.stats["doom_forced_removals"],
        }
        clone._last_material_sign = self._last_material_sign
        clone.position_history = Counter(self.position_history)
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
        if 0 <= piece.square < NUM_SQUARES and self.board[piece.square] == pid:
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

    def _position_hash(self) -> str:
        items: List[str] = [self.side_to_move]
        for pid in sorted(self.pieces.keys()):
            piece = self.pieces[pid]
            items.append(
                f"{piece.id}:{piece.color}:{piece.kind}:{piece.square}:{piece.tier}:"
                f"{piece.retaliation_target}:{piece.retaliation_window}:{int(piece.crippled)}"
            )
        digest = hashlib.sha1("|".join(items).encode("utf-8")).hexdigest()
        return digest

    def board_ascii(self) -> str:
        lines: List[str] = []
        for r in range(BOARD_SIZE):
            row: List[str] = []
            for c in range(BOARD_SIZE):
                sq = rc_to_sq(r, c)
                pid = self.board[sq]
                if pid is None:
                    row.append("..")
                    continue
                piece = self.pieces[pid]
                glyph = piece.kind.lower() if piece.color == BLACK else piece.kind
                if piece.kind in ("Q", "R", "B"):
                    glyph = f"{glyph}{piece.tier}"
                else:
                    glyph = f"{glyph}."
                row.append(glyph)
            lines.append(" ".join(row))
        return "\n".join(lines)

    def piece_signature_counts(self) -> Dict[str, int]:
        counts: Counter[str] = Counter()
        for piece in self.pieces.values():
            tier = piece.tier if piece.kind in ("Q", "R", "B") else 1
            key = f"{piece.color}_{piece.kind}_t{tier}"
            if piece.crippled and piece.kind == "P":
                key += "_crippled"
            counts[key] += 1
        return dict(sorted(counts.items()))

    def piece_type_counts(self) -> Dict[str, int]:
        counts: Counter[str] = Counter()
        for piece in self.pieces.values():
            key = f"{piece.color}_{piece.kind}"
            counts[key] += 1
        return dict(sorted(counts.items()))

    def _king_capture_line_range(self) -> int:
        if self.rules.king_move_mode == "king_capture_line":
            return max(1, int(self.rules.king_capture_line_range))
        if self.rules.king_infinite_kill:
            return 7
        return 0

    def _king_step_range(self) -> int:
        if self.rules.king_move_mode == "king_k_range":
            return max(1, int(self.rules.king_k_range))
        return 1

    def _can_king_insta_kill(self, from_sq: int, to_sq: int) -> bool:
        mode = self.rules.king_capture_insta_kill
        if mode == "off":
            return False
        if mode == "adjacent_only":
            fr, fc = sq_to_rc(from_sq)
            tr, tc = sq_to_rc(to_sq)
            return max(abs(fr - tr), abs(fc - tc)) == 1
        return True

    def _sliding_max_distance(self, piece: Piece) -> int:
        if piece.kind not in ("Q", "R", "B"):
            return 0
        if self.rules.ruleset != "matryoshka":
            return 7
        if piece.tier == 1:
            return 7
        if piece.tier == 2:
            return self.rules.tier2_slider_max_range
        return self.rules.tier3_slider_max_range

    def _is_target_piece(self, piece: Piece, attacker_color: str) -> bool:
        return piece.color != attacker_color and piece.kind != "K"

    def _can_slide_attack(
        self,
        from_sq: int,
        to_sq: int,
        directions: Sequence[Tuple[int, int]],
        max_dist: int,
        board: Optional[List[Optional[int]]] = None,
    ) -> bool:
        occupancy = board if board is not None else self.board
        fr, fc = sq_to_rc(from_sq)
        for step_r, step_c in directions:
            r, c = fr + step_r, fc + step_c
            dist = 1
            while in_bounds(r, c) and dist <= max_dist:
                sq = rc_to_sq(r, c)
                if sq == to_sq:
                    return True
                if occupancy[sq] is not None:
                    break
                r += step_r
                c += step_c
                dist += 1
        return False

    def _anticirce_rebirth_square_available(
        self, mover: Piece, from_sq: int, to_sq: int
    ) -> bool:
        origin = mover.origin_square
        if origin == from_sq or origin == to_sq:
            return True
        return self.board[origin] is None

    def _capture_move_allowed(self, mover: Piece, from_sq: int, to_sq: int) -> bool:
        if self.rules.ruleset != "anticirce":
            return True
        return self._anticirce_rebirth_square_available(mover, from_sq, to_sq)

    def _target_is_legal_capture(
        self, mover: Piece, target: Piece, from_sq: int, to_sq: int
    ) -> bool:
        if target.color == mover.color:
            return False
        if self.rules.win_condition == "checkmate_only" and target.kind == "K":
            return False
        return self._capture_move_allowed(mover, from_sq, to_sq)

    def _is_square_attacked_on_board(
        self, square: int, by_color: str, board: List[Optional[int]]
    ) -> bool:
        for piece in self.pieces.values():
            if piece.color != by_color or piece.square < 0:
                continue
            if board[piece.square] != piece.id:
                continue
            if self._piece_can_attack_square(
                piece,
                piece.square,
                square,
                board_override=board,
            ):
                return True
        return False

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
            return self._can_slide_attack(
                from_sq,
                to_sq,
                directions=directions,
                max_dist=max_dist,
                board=board,
            )

        if piece.kind == "N":
            return (dr, dc) in KNIGHT_OFFSETS

        if piece.kind == "W":
            return (dr, dc) in WAZIR_OFFSETS

        if piece.kind == "C":
            return (dr, dc) in CAMEL_OFFSETS

        if piece.kind == "D":
            return (dr, dc) in DIAGONAL_STEP_OFFSETS

        if piece.kind == "K":
            line_k = self._king_capture_line_range()
            if line_k > 0 and self._can_slide_attack(
                from_sq,
                to_sq,
                directions=SLIDING_DIRECTIONS["Q"],
                max_dist=line_k,
                board=board,
            ):
                return True

            step_k = self._king_step_range()
            cheb = max(abs(dr), abs(dc))
            if cheb > 0 and cheb <= step_k:
                return True

            if self.rules.king_infinite_kill and line_k <= 0:
                return self._can_slide_attack(
                    from_sq,
                    to_sq,
                    directions=SLIDING_DIRECTIONS["Q"],
                    max_dist=7,
                    board=board,
                )
            return False

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
                        if self._target_is_legal_capture(piece, target, piece.square, sq):
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
                    if self._target_is_legal_capture(piece, target, piece.square, sq):
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
                    if self._target_is_legal_capture(piece, target, piece.square, sq):
                        moves.append(Move(piece.square, sq, occupant))
            return moves

        if piece.kind == "C":
            for dr, dc in CAMEL_OFFSETS:
                r, c = fr + dr, fc + dc
                if not in_bounds(r, c):
                    continue
                sq = rc_to_sq(r, c)
                occupant = self.board[sq]
                if occupant is None:
                    moves.append(Move(piece.square, sq, None))
                else:
                    target = self.pieces[occupant]
                    if self._target_is_legal_capture(piece, target, piece.square, sq):
                        moves.append(Move(piece.square, sq, occupant))
            return moves

        if piece.kind == "D":
            for dr, dc in DIAGONAL_STEP_OFFSETS:
                r, c = fr + dr, fc + dc
                if not in_bounds(r, c):
                    continue
                sq = rc_to_sq(r, c)
                occupant = self.board[sq]
                if occupant is None:
                    moves.append(Move(piece.square, sq, None))
                else:
                    target = self.pieces[occupant]
                    if self._target_is_legal_capture(piece, target, piece.square, sq):
                        moves.append(Move(piece.square, sq, occupant))
            return moves

        if piece.kind == "K":
            added: set[Tuple[int, Optional[int]]] = set()
            step_k = self._king_step_range()
            if self.rules.king_move_mode != "king_k_range":
                step_k = 1

            for dr in range(-step_k, step_k + 1):
                for dc in range(-step_k, step_k + 1):
                    if dr == 0 and dc == 0:
                        continue
                    r, c = fr + dr, fc + dc
                    if not in_bounds(r, c):
                        continue
                    sq = rc_to_sq(r, c)
                    occupant = self.board[sq]
                    if occupant is None:
                        key = (sq, None)
                        if key not in added:
                            moves.append(Move(piece.square, sq, None))
                            added.add(key)
                        continue
                    target = self.pieces[occupant]
                    if self._target_is_legal_capture(piece, target, piece.square, sq):
                        key = (sq, occupant)
                        if key not in added:
                            moves.append(Move(piece.square, sq, occupant))
                            added.add(key)

            line_k = self._king_capture_line_range()
            if line_k > 0:
                for dr, dc in SLIDING_DIRECTIONS["Q"]:
                    r, c = fr + dr, fc + dc
                    dist = 1
                    while in_bounds(r, c) and dist <= line_k:
                        sq = rc_to_sq(r, c)
                        occupant = self.board[sq]
                        if occupant is None:
                            r += dr
                            c += dc
                            dist += 1
                            continue
                        target = self.pieces[occupant]
                        if self._target_is_legal_capture(piece, target, piece.square, sq):
                            key = (sq, occupant)
                            if key not in added:
                                moves.append(Move(piece.square, sq, occupant))
                                added.add(key)
                        break

            if self.rules.king_move_mode == "king_dash":
                dash_k = max(2, int(self.rules.king_dash_max))
                for dr, dc in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
                    for dist in range(2, dash_k + 1):
                        blocked = False
                        temp_board = self.board.copy()
                        temp_board[piece.square] = None
                        for step in range(1, dist + 1):
                            r = fr + (dr * step)
                            c = fc + (dc * step)
                            if not in_bounds(r, c):
                                blocked = True
                                break
                            sq = rc_to_sq(r, c)
                            if self.board[sq] is not None:
                                blocked = True
                                break
                            temp_board[sq] = piece.id
                            if self._is_square_attacked_on_board(
                                sq,
                                self._opponent(piece.color),
                                temp_board,
                            ):
                                blocked = True
                            temp_board[sq] = None
                            if blocked:
                                break
                        if blocked:
                            break
                        dst_sq = rc_to_sq(fr + (dr * dist), fc + (dc * dist))
                        key = (dst_sq, None)
                        if key not in added:
                            moves.append(Move(piece.square, dst_sq, None))
                            added.add(key)
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
                    if (not piece.crippled) and fr == start_row and in_bounds(two_r, one_c):
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
                if self._target_is_legal_capture(piece, target, piece.square, target_sq):
                    moves.append(Move(piece.square, target_sq, occupant))
            return moves

        return moves

    def _all_pseudo_legal_moves(self, color: str) -> List[Move]:
        moves: List[Move] = []
        for piece in self.pieces.values():
            if piece.color != color or piece.square < 0:
                continue
            moves.extend(self._generate_piece_moves(piece))
        return moves

    def _king_square(self, color: str) -> Optional[int]:
        for piece in self.pieces.values():
            if piece.color == color and piece.kind == "K" and piece.square >= 0:
                return piece.square
        return None

    def is_square_attacked(self, square: int, by_color: str) -> bool:
        for piece in self.pieces.values():
            if piece.color != by_color or piece.square < 0:
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
            sim._apply_move_internal(move, resolve_terminal=False, record_history=False)
            if not sim.is_in_check(color):
                if self.rules.ko_repetition_illegal:
                    pos_hash = sim._position_hash()
                    if self.position_history.get(pos_hash, 0) > 0:
                        continue
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
                piece.crippled = False
                return True
            if piece.tier == 2:
                piece.tier = 3
                piece.crippled = False
                return True
            piece.kind = "P"
            piece.tier = 1
            piece.crippled = self.rules.collapse_target == "crippled_pawn"
            return True

        if piece.kind == "N":
            if self.rules.knight_decay_mode == "camel":
                piece.kind = "C"
            elif self.rules.knight_decay_mode == "diag_step":
                piece.kind = "D"
            else:
                piece.kind = "W"
            piece.tier = 1
            piece.crippled = False
            return True

        if piece.kind in ("W", "C", "D"):
            piece.kind = "P"
            piece.tier = 1
            piece.crippled = self.rules.collapse_target == "crippled_pawn"
            return True

        return False

    def _demote_piece_steps(self, piece: Piece, steps: int) -> bool:
        for _ in range(max(1, steps)):
            survives = self._demote_piece_for_capture(piece)
            if not survives:
                return False
        return True

    def _retaliation_candidates(
        self, demoted_piece: Piece
    ) -> List[Tuple[int, int]]:
        """Return (target_id, square) candidates based on targeting mode."""

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

        if self.rules.retaliation_targeting == "localized_safe":
            origin_r, origin_c = sq_to_rc(demoted_piece.origin_square)
            localized = []
            for target in targets:
                tr, tc = sq_to_rc(target.square)
                if max(abs(origin_r - tr), abs(origin_c - tc)) <= self.rules.retaliation_local_radius:
                    localized.append(target)
            if localized:
                targets = localized

        targets_sorted = sorted(
            targets,
            key=lambda p: (
                PIECE_VALUE_PRIORITY.get(p.kind, 0),
                self._material_value(p),
            ),
            reverse=True,
        )

        if self.rules.retaliation_targeting == "any_unsafe":
            # Target any enemy piece — widens the candidate pool dramatically.
            selected_targets = list(targets)
        elif self.rules.retaliation_targeting == "top2_pool_safe":
            allowed_target_ids = {p.id for p in targets_sorted[:2]}
            selected_targets = [p for p in targets if p.id in allowed_target_ids]
        else:
            top_pr = PIECE_VALUE_PRIORITY.get(targets_sorted[0].kind, 0)
            allowed_target_ids = {
                p.id for p in targets_sorted if PIECE_VALUE_PRIORITY.get(p.kind, 0) == top_pr
            }
            selected_targets = [p for p in targets if p.id in allowed_target_ids]

        targets_by_priority: Dict[int, List[Piece]] = defaultdict(list)
        for target in selected_targets:
            pr = PIECE_VALUE_PRIORITY.get(target.kind, 0)
            targets_by_priority[pr].append(target)

        skip_safety = self.rules.retaliation_targeting in ("highest_unsafe", "any_unsafe")

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

                    if not skip_safety:
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

    def _additional_threatened_value(
        self, demoted_piece: Piece, square: int, primary_target_id: int
    ) -> int:
        temp_board = self.board.copy()
        temp_board[square] = demoted_piece.id
        total = 0
        for piece in self.pieces.values():
            if piece.color == demoted_piece.color or piece.kind == "K":
                continue
            if piece.id == primary_target_id:
                continue
            if self._piece_can_attack_square(
                demoted_piece,
                square,
                piece.square,
                board_override=temp_board,
            ):
                total += PIECE_VALUE_PRIORITY.get(piece.kind, 0)
        return total

    def _distance_to_enemy_king(self, color: str, square: int) -> int:
        enemy_king_sq = self._king_square(self._opponent(color))
        if enemy_king_sq is None:
            return 99
        r1, c1 = sq_to_rc(square)
        r2, c2 = sq_to_rc(enemy_king_sq)
        return max(abs(r1 - r2), abs(c1 - c2))

    def _choose_retaliation_candidate(
        self, piece: Piece, candidates: Sequence[Tuple[int, int]]
    ) -> Tuple[int, int]:
        if not candidates:
            raise ValueError("No retaliation candidates to choose from.")

        if self.rules.retaliation_tiebreak == "max_threat":
            scored = [
                (
                    self._additional_threatened_value(piece, sq, target_id),
                    -self._distance_to_enemy_king(piece.color, sq),
                    target_id,
                    sq,
                )
                for target_id, sq in candidates
            ]
            best = max(score for score, _, _, _ in scored)
            finalists = [(t, s) for score, _, t, s in scored if score == best]
            return self.rng.choice(finalists)

        if self.rules.retaliation_tiebreak == "min_king_distance":
            scored = [
                (self._distance_to_enemy_king(piece.color, sq), target_id, sq)
                for target_id, sq in candidates
            ]
            best = min(score for score, _, _ in scored)
            finalists = [(t, s) for score, t, s in scored if score == best]
            return self.rng.choice(finalists)

        return self.rng.choice(list(candidates))

    def _redeploy_without_retaliation(self, piece: Piece, event: MoveEvent) -> None:
        circe_sq = piece.origin_square
        if self.board[circe_sq] is None:
            self.board[circe_sq] = piece.id
            piece.square = circe_sq
            piece.retaliation_target = None
            piece.retaliation_window = 0
            event.retaliation_placement_mode = "circe_no_retaliation"
            return

        empty_squares = [sq for sq, pid in enumerate(self.board) if pid is None]
        if not empty_squares:
            self._remove_piece(piece.id)
            return

        sq = self._pick_fallback_square(piece, empty_squares)
        self.board[sq] = piece.id
        piece.square = sq
        piece.retaliation_target = None
        piece.retaliation_window = 0
        event.retaliation_placement_mode = "fallback_no_retaliation"

    def _redeploy_with_retaliation(self, piece: Piece, event: MoveEvent) -> None:
        candidates = self._retaliation_candidates(piece)
        self.stats["retaliation_redeployments"] += 1

        if candidates:
            target_id, sq = self._choose_retaliation_candidate(piece, candidates)
            self.board[sq] = piece.id
            piece.square = sq
            if self.rules.retaliation_mode == "attacker_rekill":
                # Designer's intent: the respawned piece is vulnerable to permakill.
                # The capturing side must re-kill it within N moves or it stays.
                piece.permakill_vulnerable = self.rules.retaliation_strike_window
                piece.retaliation_target = None
                piece.retaliation_window = 0
            else:
                # Original implementation: respawned piece gets a revenge kill window.
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
            "king_proximity"
            if self.rules.fallback_policy == "king_proximity"
            else "nearest_circe"
            if self.rules.fallback_policy == "nearest_circe"
            else "random"
        )
        self.stats["retaliation_random_placements"] += 1

    def _pick_fallback_square(self, piece: Piece, empty_squares: Sequence[int]) -> int:
        if self.rules.fallback_policy == "nearest_circe":
            orr, orc = sq_to_rc(piece.origin_square)
            best: List[int] = []
            best_dist: Optional[int] = None
            for sq in empty_squares:
                r, c = sq_to_rc(sq)
                dist = max(abs(r - orr), abs(c - orc))
                if best_dist is None or dist < best_dist:
                    best_dist = dist
                    best = [sq]
                elif dist == best_dist:
                    best.append(sq)
            return self.rng.choice(best)

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

    def _mark_permanent_removal(
        self, event: MoveEvent, reason: str, count_king_rule: bool = False
    ) -> None:
        event.capture_was_permanent = True
        event.capture_permanent_reason = reason
        self.stats["permanent_removals_total"] += 1
        if count_king_rule:
            self.stats["permanent_removals_king_capture"] += 1
        if reason == "retaliation_strike":
            self.stats["permanent_removals_retaliation_strike"] += 1

    def _apply_capture(
        self,
        mover: Piece,
        captured_id: int,
        from_sq: int,
        to_sq: int,
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
            if self.rules.win_condition == "checkmate_only":
                return
            self._remove_piece(captured_id)
            self.terminated = True
            self.winner = mover.color
            self.termination_reason = "king_captured"
            event.game_over = True
            event.winner = mover.color
            event.termination = "king_captured"
            return

        ruleset = self.rules.ruleset

        if ruleset == "matryoshka":
            permanent = False
            reason = "demotion_cycle"
            demotion_steps = 1

            if mover.kind == "K" and self._can_king_insta_kill(from_sq, to_sq):
                permanent = True
                reason = "king_capture_rule"

            # Attacker-rekill mode: if the captured piece is vulnerable (just
            # respawned via retaliation), re-capturing it is a permanent kill.
            if (
                self.rules.retaliation_mode == "attacker_rekill"
                and captured.permakill_vulnerable > 0
            ):
                self.stats["retarget_captures_attempted"] += 1
                self.stats["retarget_captures_success"] += 1
                permanent = True
                reason = "retaliation_strike"

            # Defender-strike mode (original): mover executes revenge kill on target.
            if (
                self.rules.retaliation_mode == "defender_strike"
                and mover.retaliation_window > 0
            ):
                self.stats["retarget_captures_attempted"] += 1
                if mover.retaliation_target == captured_id:
                    self.stats["retarget_captures_success"] += 1
                    if self.rules.strike_effect == "double_demote":
                        demotion_steps += 1
                        reason = "retaliation_double_demote"
                    else:
                        permanent = True
                        reason = "retaliation_strike"

            if self.bonus_capture_damage > 0 and not permanent:
                demotion_steps += 1
                self.bonus_capture_damage -= 1

            if permanent:
                self._remove_piece(captured_id)
                self._mark_permanent_removal(
                    event, reason, count_king_rule=(reason == "king_capture_rule")
                )
                return

            # Normal Matryoshka capture: pawn removed, non-pawn demotes+redeploys.
            if self.board[captured.square] == captured_id:
                self.board[captured.square] = None
            survives = self._demote_piece_steps(captured, demotion_steps)
            if not survives:
                self._remove_piece(captured_id)
                self._mark_permanent_removal(event, "captured_pawn")
                return

            captured.retaliation_target = None
            captured.retaliation_window = 0
            if self.rules.retaliation_enabled:
                self._redeploy_with_retaliation(captured, event)
            else:
                self._redeploy_without_retaliation(captured, event)
            return

        if ruleset == "normal":
            self._remove_piece(captured_id)
            self._mark_permanent_removal(event, "normal_capture")
            return

        if ruleset == "circe":
            rebirth_sq = captured.origin_square

            # If the rebirth square is empty, captured piece returns there unchanged.
            if self.board[rebirth_sq] is None:
                captured.square = rebirth_sq
                captured.retaliation_target = None
                captured.retaliation_window = 0
                self.board[rebirth_sq] = captured.id
                self.stats["circe_captured_rebirths"] += 1
            else:
                self._remove_piece(captured_id)
                self._mark_permanent_removal(event, "circe_no_rebirth_square")
            return

        if ruleset == "anticirce":
            if not self._anticirce_rebirth_square_available(mover, from_sq, to_sq):
                # Should not occur because illegal captures are filtered in move generation.
                self._remove_piece(captured_id)
                self._mark_permanent_removal(event, "anticirce_rebirth_blocked_fallback")
                return

            self._remove_piece(captured_id)
            self._mark_permanent_removal(event, "anticirce_capture")

            rebirth_sq = mover.origin_square
            if rebirth_sq != to_sq:
                self.board[to_sq] = None
                self.board[rebirth_sq] = mover.id
                mover.square = rebirth_sq
            self.stats["anticirce_attacker_rebirths"] += 1
            return

        # Unknown ruleset: fall back to normal capture semantics.
        self._remove_piece(captured_id)
        self._mark_permanent_removal(event, "fallback_normal_capture")

    def _non_king_onboard(self, color: str) -> List[Piece]:
        return [
            p
            for p in self.pieces.values()
            if p.color == color and p.kind != "K" and p.square >= 0
        ]

    def _apply_doom_clock(self) -> None:
        self.stats["doom_triggers"] += 1

        if self.rules.doom_clock_effect == "bonus_capture_damage":
            self.bonus_capture_damage += 1
            return

        for color in (WHITE, BLACK):
            candidates = self._non_king_onboard(color)
            if not candidates:
                continue

            if self.rules.doom_clock_effect == "collapse_weakest":
                candidates = sorted(candidates, key=lambda p: self._material_value(p))
                target = candidates[0]
            else:
                target = self.rng.choice(candidates)

            if self.board[target.square] == target.id:
                self.board[target.square] = None
            survives = self._demote_piece_steps(target, 1)
            if not survives:
                self._remove_piece(target.id)
                self.stats["permanent_removals_total"] += 1
                self.stats["doom_forced_removals"] += 1
                continue

            target.retaliation_target = None
            target.retaliation_window = 0
            if self.board[target.origin_square] is None:
                target.square = target.origin_square
                self.board[target.square] = target.id
                continue

            empty_squares = [sq for sq, pid in enumerate(self.board) if pid is None]
            if not empty_squares:
                self._remove_piece(target.id)
                self.stats["permanent_removals_total"] += 1
                self.stats["doom_forced_removals"] += 1
                continue

            target.square = self._pick_fallback_square(target, empty_squares)
            self.board[target.square] = target.id

    def _apply_move_internal(
        self,
        move: Move,
        resolve_terminal: bool = False,
        record_history: bool = True,
    ) -> MoveEvent:
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

        from_sq = move.from_sq
        to_sq = move.to_sq

        self.board[from_sq] = None
        mover.square = move.to_sq

        captured_id = self.board[to_sq]
        self.board[to_sq] = mover.id
        if captured_id is not None:
            if self.pieces[captured_id].color == mover.color:
                raise ValueError("Illegal self-capture encountered.")
            self._apply_capture(mover, captured_id, from_sq=from_sq, to_sq=to_sq, event=event)
            if self.terminated:
                return event

        # Defender-strike mode: consume mover's retaliation window.
        if self.rules.ruleset == "matryoshka" and mover.retaliation_window > 0:
            mover.retaliation_window -= 1
            if mover.retaliation_window <= 0:
                mover.retaliation_window = 0
                mover.retaliation_target = None

        # Attacker-rekill mode: decrement vulnerability window for opponent pieces.
        # The moving side is the "capturer" — their opponent's pieces lose vulnerability.
        if (
            self.rules.ruleset == "matryoshka"
            and self.rules.retaliation_mode == "attacker_rekill"
        ):
            moving_side = mover.color
            for piece in self.pieces.values():
                if piece.color != moving_side and piece.permakill_vulnerable > 0:
                    piece.permakill_vulnerable -= 1

        # Promotion is always to full-tier queen in this simulation.
        if mover.kind == "P":
            row, _ = sq_to_rc(mover.square)
            can_promote = (mover.color == WHITE and row == 0) or (
                mover.color == BLACK and row == BOARD_SIZE - 1
            )
            if mover.crippled and not self.rules.crippled_pawn_can_promote:
                can_promote = False
            if can_promote:
                mover.kind = "Q"
                mover.tier = 1
                mover.crippled = False
                event.promotion_happened = True
                self.stats["promotions"] += 1

        if event.capture_happened:
            self.quiet_halfmoves = 0
        else:
            self.quiet_halfmoves += 1

        if event.capture_was_permanent:
            self.no_permanent_halfmoves = 0
        else:
            self.no_permanent_halfmoves += 1

        doom_limit = max(0, int(self.rules.doom_clock_full_moves))
        if doom_limit > 0 and self.no_permanent_halfmoves >= doom_limit * 2:
            self._apply_doom_clock()
            self.no_permanent_halfmoves = 0

        self._note_material_dynamics(previous_balance_white)

        self.side_to_move = self._opponent(self.side_to_move)
        self.ply += 1
        if record_history:
            self.position_history[self._position_hash()] += 1

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
                    if self.rules.stalemate_is_loss:
                        self.winner = self._opponent(self.side_to_move)
                        self.termination_reason = "stalemate_loss"
                    else:
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


def sq_to_coord(sq: int) -> str:
    r, c = sq_to_rc(sq)
    return f"{chr(ord('a') + c)}{BOARD_SIZE - r}"


def evaluate_position(game: GameState, color: str) -> float:
    score = game._material_balance(color)

    # Bonus for center occupancy and retaliation pressure.
    for piece in game.pieces.values():
        sign = 1.0 if piece.color == color else -1.0
        if piece.square in CENTER_SQUARES:
            score += sign * 0.2
        elif piece.square in NEAR_CENTER_SQUARES:
            score += sign * 0.08

        if (
            game.rules.ruleset == "matryoshka"
            and piece.retaliation_window > 0
            and piece.retaliation_target is not None
        ):
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


def run_single_game(
    seed: int,
    max_plies: int,
    rules: RuleConfig,
    include_move_log: bool = True,
    start_side: str = WHITE,
    snapshot_plies: Optional[Sequence[int]] = None,
) -> Dict[str, object]:
    game = GameState(seed=seed, rules=rules, start_side=start_side)

    move_log: Optional[List[Dict[str, object]]] = [] if include_move_log else None
    compact_log: deque[Dict[str, object]] = deque(maxlen=24)
    snapshot_targets = sorted({int(v) for v in (snapshot_plies or [40, 80, 120]) if int(v) > 0})
    snapshots: Dict[str, Dict[str, object]] = {}

    def maybe_take_snapshot() -> None:
        if game.ply in snapshot_targets and str(game.ply) not in snapshots:
            snapshots[str(game.ply)] = {
                "piece_count": len(game.pieces),
                "piece_type_counts": game.piece_type_counts(),
            }

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
                if rules.stalemate_is_loss:
                    game.winner = game._opponent(game.side_to_move)
                    game.termination_reason = "stalemate_loss"
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
        move_coord = f"{sq_to_coord(move.from_sq)}{sq_to_coord(move.to_sq)}"
        maybe_take_snapshot()

        if rules.quiet_halfmove_limit > 0 and game.quiet_halfmoves >= rules.quiet_halfmove_limit:
            game.terminated = True
            game.winner = DRAW
            game.termination_reason = "quiet_limit"

        if move_log is not None:
            move_log.append(
                {
                    "ply": game.ply,
                    "side": mover_color,
                    "mover_kind": mover_kind,
                    "from_sq": move.from_sq,
                    "to_sq": move.to_sq,
                    "coord": move_coord,
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
        compact_log.append(
            {
                "ply": game.ply,
                "side": mover_color,
                "coord": move_coord,
                "capture": event.capture_happened,
                "captured_kind": event.captured_piece_kind,
                "capture_permanent": event.capture_was_permanent,
                "check_target": game.side_to_move if event.check_given else None,
            }
        )

    if not game.terminated:
        game.terminated = True
        game.winner = DRAW
        game.termination_reason = "max_plies"

    white_legal = game.stats["mean_legal_moves_white"]
    black_legal = game.stats["mean_legal_moves_black"]
    for target in snapshot_targets:
        key = str(target)
        if key not in snapshots:
            snapshots[key] = {
                "piece_count": len(game.pieces),
                "piece_type_counts": game.piece_type_counts(),
            }

    draw_forensics: Optional[Dict[str, object]] = None
    if game.winner == DRAW:
        last_moves = list(compact_log)[-20:]
        last_ten = [row for row in compact_log if row["ply"] > max(0, game.ply - 10)]
        check_last_10 = {
            "white_in_check": any(row["check_target"] == WHITE for row in last_ten),
            "black_in_check": any(row["check_target"] == BLACK for row in last_ten),
            "check_events": sum(1 for row in last_ten if row["check_target"] is not None),
        }
        signature_payload = {
            "termination": game.termination_reason,
            "piece_signature": game.piece_signature_counts(),
            "check_last_10": check_last_10,
        }
        draw_sig_hash = hashlib.sha1(
            json.dumps(signature_payload, sort_keys=True).encode("utf-8")
        ).hexdigest()[:16]
        draw_forensics = {
            "signature_hash": draw_sig_hash,
            "termination_reason": game.termination_reason,
            "piece_signature": game.piece_signature_counts(),
            "piece_type_counts": game.piece_type_counts(),
            "checks_last_10_plies": check_last_10,
            "last_20_moves": [row["coord"] for row in last_moves],
            "final_board_hash": game._position_hash(),
            "final_board_ascii": game.board_ascii(),
        }

    return {
        "seed": seed,
        "start_side": start_side,
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
        "circe_captured_rebirths": game.stats["circe_captured_rebirths"],
        "anticirce_attacker_rebirths": game.stats["anticirce_attacker_rebirths"],
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
        "doom_triggers": game.stats["doom_triggers"],
        "doom_forced_removals": game.stats["doom_forced_removals"],
        "quiet_halfmoves": game.quiet_halfmoves,
        "snapshots": snapshots,
        "final_piece_signature": game.piece_signature_counts(),
        "final_piece_type_counts": game.piece_type_counts(),
        "final_board_hash": game._position_hash(),
        "final_board_ascii": game.board_ascii(),
        "draw_forensics": draw_forensics,
        "mean_legal_moves_white": (
            round(statistics.mean(white_legal), 3) if white_legal else 0.0
        ),
        "mean_legal_moves_black": (
            round(statistics.mean(black_legal), 3) if black_legal else 0.0
        ),
        "move_log": move_log if move_log is not None else [],
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
    circe_rebirths = [int(g["circe_captured_rebirths"]) for g in games]
    anticirce_rebirths = [int(g["anticirce_attacker_rebirths"]) for g in games]
    permanent = [int(g["permanent_removals_total"]) for g in games]
    strike_perm = [int(g["permanent_removals_retaliation_strike"]) for g in games]
    king_perm = [int(g["permanent_removals_king_capture"]) for g in games]
    ret_attempts = [int(g["retarget_captures_attempted"]) for g in games]
    ret_success = [int(g["retarget_captures_success"]) for g in games]
    promotions = [int(g["promotions"]) for g in games]
    volatility = [float(g["material_volatility"]) for g in games]
    lead_changes = [int(g["material_lead_sign_changes"]) for g in games]
    doom_triggers = [int(g.get("doom_triggers", 0)) for g in games]
    doom_forced = [int(g.get("doom_forced_removals", 0)) for g in games]

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

    draw_rate = (winners.get(DRAW, 0) / n) if n else 0.0
    decisive_rate = 1.0 - draw_rate if n else 0.0
    mean_plies = mean(plies)
    mean_captures = mean(captures)
    mean_permanent = mean(permanent)
    capture_rate_per_100_plies = (
        (mean_captures / mean_plies) * 100.0 if mean_plies else 0.0
    )

    return {
        "num_games": n,
        "winner_counts": dict(winners),
        "termination_counts": dict(terminations),
        "draw_rate": round(draw_rate, 4),
        "decisive_rate": round(decisive_rate, 4),
        "mean_plies": round(mean_plies, 3),
        "mean_full_moves": round(mean_plies / 2.0, 3),
        "median_plies": round(median(plies), 3),
        "mean_captures": round(mean_captures, 3),
        "captures_per_100_plies": round(capture_rate_per_100_plies, 3),
        "mean_redeployments": round(mean(redeploys), 3),
        "mean_permanent_removals": round(mean_permanent, 3),
        "permanent_capture_share": round(
            (mean_permanent / mean_captures) if mean_captures else 0.0,
            4,
        ),
        "mean_permanent_by_strike": round(mean(strike_perm), 3),
        "mean_permanent_by_king": round(mean(king_perm), 3),
        "mean_circe_captured_rebirths": round(mean(circe_rebirths), 3),
        "mean_anticirce_attacker_rebirths": round(mean(anticirce_rebirths), 3),
        "king_capture_termination_rate": round(
            (terminations.get("king_captured", 0) / n) if n else 0.0,
            4,
        ),
        "checkmate_rate": round(
            (terminations.get("checkmate", 0) / n) if n else 0.0,
            4,
        ),
        "stalemate_rate": round(
            (terminations.get("stalemate", 0) / n) if n else 0.0,
            4,
        ),
        "max_plies_rate": round(
            (terminations.get("max_plies", 0) / n) if n else 0.0,
            4,
        ),
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
        "mean_doom_triggers": round(mean(doom_triggers), 3),
        "mean_doom_forced_removals": round(mean(doom_forced), 3),
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
            f"- Rule parameters: ruleset={rules.ruleset}, tier2_range={rules.tier2_slider_max_range}, strike_window={rules.retaliation_strike_window}, fallback_policy={rules.fallback_policy}, king_infinite_kill={rules.king_infinite_kill}\n"
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


def _run_game_task(args: Tuple[int, int, RuleConfig, bool]) -> Dict[str, object]:
    gseed, max_plies, rules, include_move_log = args
    return run_single_game(
        seed=gseed,
        max_plies=max_plies,
        rules=rules,
        include_move_log=include_move_log,
    )


def run_batch(
    num_games: int,
    max_plies: int,
    seed: int,
    rules: RuleConfig,
    include_move_log: bool = True,
    workers: int = 1,
) -> Tuple[List[Dict[str, object]], Dict[str, object], List[Dict[str, str]]]:
    games: List[Dict[str, object]] = []
    tasks = [
        (seed + (i * 17), max_plies, rules, include_move_log) for i in range(num_games)
    ]
    if workers <= 1:
        for task in tasks:
            games.append(_run_game_task(task))
    else:
        with mp.Pool(processes=workers) as pool:
            for game in pool.imap_unordered(_run_game_task, tasks, chunksize=8):
                games.append(game)
        games.sort(key=lambda g: int(g["seed"]))
    summary = aggregate_results(games)
    recommendations = generate_recommendations(summary)
    return games, summary, recommendations


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Simulate chess variants.")
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
        "--ruleset",
        type=str,
        default="matryoshka",
        choices=["matryoshka", "normal", "circe", "anticirce"],
        help="Capture/respawn ruleset to simulate",
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
        choices=["random", "king_proximity", "nearest_circe"],
        help="Fallback placement policy when no safe target and Circe unavailable",
    )
    parser.add_argument(
        "--king-infinite-kill",
        action="store_true",
        help="Enable queen-line king capture range (captures only)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Number of worker processes (default: 1)",
    )
    parser.add_argument(
        "--no-move-log",
        action="store_true",
        help="Skip per-move logs to speed up large runs",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    rules = RuleConfig(
        ruleset=args.ruleset,
        tier2_slider_max_range=args.tier2_range,
        tier3_slider_max_range=1,
        retaliation_strike_window=args.strike_window,
        fallback_policy=args.fallback_policy,
        king_infinite_kill=args.king_infinite_kill,
    )
    games, summary, recommendations = run_batch(
        num_games=args.games,
        max_plies=args.max_plies,
        seed=args.seed,
        rules=rules,
        include_move_log=(not args.no_move_log),
        workers=max(1, args.workers),
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
        f"ruleset={rules.ruleset}, "
        f"tier2_range={rules.tier2_slider_max_range}, "
        f"strike_window={rules.retaliation_strike_window}, "
        f"fallback_policy={rules.fallback_policy}, "
        f"king_infinite_kill={rules.king_infinite_kill}"
    )
    print(f"Output directory: {args.output_dir}")


if __name__ == "__main__":
    main()
