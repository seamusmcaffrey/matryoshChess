#!/usr/bin/env python3
"""Two-stage Matryoshka variant optimizer with live checkpoints.

Key capabilities:
- broad stage-1 sweep (Latin-hypercube style + anchor variants)
- stage-2 local mutation search around elite variants
- stratified/randomized seed bundles per variant
- continuous checkpoint writes (progress.jsonl, run_state.json, per-variant partial CSV/JSON)
- uncertainty intervals + Pareto frontier + draw forensics aggregation
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import json
import math
import multiprocessing as mp
import os
import random
import signal
import statistics
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha1
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from simulate_variant_study import BLACK, DRAW, WHITE, RuleConfig, run_single_game


DEFAULT_CONFIG: Dict[str, object] = {
    "base_seed": 20260305,
    "max_plies": 220,
    "workers": max(1, min((os.cpu_count() or 8) - 2, 14)),
    "target_hours": 8.0,
    "utilization": 0.9,
    "calibration_games": 96,
    "snapshot_plies": [40, 80, 120],
    "progress_flush_every_games": 64,
    "variant_timebox_minutes": 18.0,
    "write_raw_games": True,
    "stage1": {
        "num_variants": 120,
        "seeds_per_variant": 10,
        "min_games_per_seed": 10,
        "max_games_per_seed": 70,
    },
    "stage2": {
        "elite_count": 15,
        "mutations_per_elite": 3,
        "seeds_per_variant": 14,
        "min_games_per_seed": 12,
        "max_games_per_seed": 80,
    },
    "search_space": {
        "ruleset": ["matryoshka", "matryoshka", "matryoshka", "normal", "circe", "anticirce"],
        "tier2_slider_max_range": [3, 4, 5],
        "tier3_slider_max_range": [1, 2],
        "retaliation_strike_window": [1, 2, 3],
        "fallback_policy": ["random", "king_proximity", "nearest_circe"],
        "king_infinite_kill": [False, False, True],
        "king_move_mode": ["normal", "king_k_range", "king_capture_line", "king_dash"],
        "king_dash_max": [2, 3],
        "king_k_range": [2, 3],
        "king_capture_line_range": [2, 3, 4],
        "king_capture_insta_kill": ["on", "adjacent_only", "off"],
        "retaliation_enabled": [True, False],
        "retaliation_targeting": ["highest_safe", "localized_safe", "top2_pool_safe"],
        "retaliation_local_radius": [3, 4, 5],
        "retaliation_tiebreak": ["random", "max_threat", "min_king_distance"],
        "strike_effect": ["perma_kill", "double_demote"],
        "stalemate_is_loss": [False, True],
        "ko_repetition_illegal": [False, True],
        "doom_clock_full_moves": [0, 16, 24, 32],
        "doom_clock_effect": [
            "demote_random_non_king",
            "collapse_weakest",
            "bonus_capture_damage",
        ],
        "quiet_halfmove_limit": [0, 60, 100],
        "knight_decay_mode": ["wazir", "camel", "diag_step"],
        "collapse_target": ["pawn", "crippled_pawn"],
        "crippled_pawn_can_promote": [False, True],
        "win_condition": ["checkmate_or_king_capture", "checkmate_only"],
    },
    "additional_ideas": [
        "localized retaliation within dynamic threat radius",
        "ghost retaliation markers (threat tokens) instead of instant teleport",
        "capture damage scaling by capturer/captured value ratio",
        "momentum vulnerability: capturer becomes targetable for 1 ply",
        "forced trade zones near kings after repeated checks",
        "piece fatigue: repeated movers lose range temporarily",
        "zone-of-control overlays that block redeploy squares",
        "retaliation escrow: delayed spawn unless tactical condition met",
        "sudden-death conversion after long no-removal streak",
        "temporary king immunity after successful retaliation strike",
    ],
}


@dataclass(frozen=True)
class VariantSpec:
    variant_id: str
    stage: str
    rules: RuleConfig
    levers: Dict[str, object]
    parent_id: Optional[str] = None
    notes: str = ""


@dataclass
class StageBudget:
    variants: int
    seeds_per_variant: int
    games_per_seed: int
    total_games: int


def deep_merge(base: Dict[str, object], override: Dict[str, object]) -> Dict[str, object]:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)  # type: ignore[arg-type]
        else:
            out[key] = value
    return out


def ci95_proportion(successes: int, n: int) -> float:
    if n <= 0:
        return 0.0
    p = successes / n
    return 1.96 * math.sqrt(max(0.0, p * (1.0 - p)) / n)


def ci95_mean(values: Sequence[float]) -> float:
    if len(values) <= 1:
        return 0.0
    sd = statistics.stdev(values)
    return 1.96 * (sd / math.sqrt(len(values)))


def percentile(values: Sequence[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = max(0, min(len(ordered) - 1, int(round((pct / 100.0) * (len(ordered) - 1)))))
    return float(ordered[idx])


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def progress_bar(completed: int, total: int, width: int = 24) -> str:
    if total <= 0:
        return "[" + ("-" * width) + "]"
    frac = max(0.0, min(1.0, completed / total))
    filled = int(round(frac * width))
    return "[" + ("#" * filled) + ("-" * (width - filled)) + "]"


def format_seconds(seconds: float) -> str:
    secs = max(0, int(seconds))
    h = secs // 3600
    m = (secs % 3600) // 60
    s = secs % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def append_jsonl(path: Path, payload: Dict[str, object]) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, sort_keys=True))
        f.write("\n")


def atomic_write_json(path: Path, payload: Dict[str, object]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, sort_keys=True)
    tmp.replace(path)


def rules_signature(rules: RuleConfig) -> str:
    blob = json.dumps(dataclasses.asdict(rules), sort_keys=True)
    return sha1(blob.encode("utf-8")).hexdigest()


def clamp_int(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, value))


def latin_hypercube_matrix(rng: random.Random, rows: int, dims: int) -> List[List[float]]:
    if rows <= 0:
        return []
    bins: List[List[int]] = []
    for _ in range(dims):
        perm = list(range(rows))
        rng.shuffle(perm)
        bins.append(perm)
    out: List[List[float]] = []
    for i in range(rows):
        row: List[float] = []
        for d in range(dims):
            row.append((bins[d][i] + rng.random()) / rows)
        out.append(row)
    return out


def sample_from_choices(unit: float, choices: Sequence[object]) -> object:
    if not choices:
        raise ValueError("choices cannot be empty")
    idx = min(len(choices) - 1, int(unit * len(choices)))
    return choices[idx]


def normalize_rule_values(raw: Dict[str, object]) -> Dict[str, object]:
    vals = dict(raw)
    ruleset = str(vals.get("ruleset", "matryoshka"))
    king_mode = str(vals.get("king_move_mode", "normal"))

    if king_mode != "king_dash":
        vals["king_dash_max"] = 2
    if king_mode != "king_k_range":
        vals["king_k_range"] = 2
    if king_mode != "king_capture_line":
        vals["king_capture_line_range"] = 2

    if king_mode == "king_capture_line":
        vals["king_infinite_kill"] = False

    if ruleset != "matryoshka":
        vals["retaliation_enabled"] = False
        vals["retaliation_targeting"] = "highest_safe"
        vals["retaliation_tiebreak"] = "random"
        vals["strike_effect"] = "perma_kill"
        vals["doom_clock_full_moves"] = 0
        vals["collapse_target"] = "pawn"
        vals["knight_decay_mode"] = "wazir"

    if not bool(vals.get("retaliation_enabled", True)):
        vals["retaliation_targeting"] = "highest_safe"
        vals["retaliation_tiebreak"] = "random"
        vals["retaliation_strike_window"] = 1
        vals["strike_effect"] = "perma_kill"

    if int(vals.get("doom_clock_full_moves", 0)) <= 0:
        vals["doom_clock_full_moves"] = 0
        vals["doom_clock_effect"] = "demote_random_non_king"

    if vals.get("collapse_target") != "crippled_pawn":
        vals["crippled_pawn_can_promote"] = False

    if vals.get("win_condition") == "checkmate_only":
        vals["king_capture_insta_kill"] = "off"

    return vals


def dict_to_rules(values: Dict[str, object]) -> RuleConfig:
    normalized = normalize_rule_values(values)
    return RuleConfig(**normalized)


def make_anchor_variants() -> List[VariantSpec]:
    anchors: List[Tuple[str, Dict[str, object], str]] = [
        (
            "anchor_normal_reference",
            {
                "ruleset": "normal",
                "tier2_slider_max_range": 4,
                "tier3_slider_max_range": 1,
                "retaliation_strike_window": 1,
                "fallback_policy": "random",
                "king_infinite_kill": False,
                "king_move_mode": "normal",
                "king_dash_max": 2,
                "king_k_range": 2,
                "king_capture_line_range": 2,
                "king_capture_insta_kill": "off",
                "retaliation_enabled": False,
                "retaliation_targeting": "highest_safe",
                "retaliation_local_radius": 4,
                "retaliation_tiebreak": "random",
                "strike_effect": "perma_kill",
                "stalemate_is_loss": False,
                "ko_repetition_illegal": False,
                "doom_clock_full_moves": 0,
                "doom_clock_effect": "demote_random_non_king",
                "quiet_halfmove_limit": 100,
                "knight_decay_mode": "wazir",
                "collapse_target": "pawn",
                "crippled_pawn_can_promote": False,
                "win_condition": "checkmate_only",
            },
            "baseline chess-like reference",
        ),
        (
            "anchor_mat_baseline",
            {
                "ruleset": "matryoshka",
                "tier2_slider_max_range": 4,
                "tier3_slider_max_range": 1,
                "retaliation_strike_window": 1,
                "fallback_policy": "random",
                "king_infinite_kill": False,
                "king_move_mode": "normal",
                "king_dash_max": 2,
                "king_k_range": 2,
                "king_capture_line_range": 2,
                "king_capture_insta_kill": "on",
                "retaliation_enabled": True,
                "retaliation_targeting": "highest_safe",
                "retaliation_local_radius": 4,
                "retaliation_tiebreak": "random",
                "strike_effect": "perma_kill",
                "stalemate_is_loss": False,
                "ko_repetition_illegal": False,
                "doom_clock_full_moves": 0,
                "doom_clock_effect": "demote_random_non_king",
                "quiet_halfmove_limit": 100,
                "knight_decay_mode": "wazir",
                "collapse_target": "pawn",
                "crippled_pawn_can_promote": False,
                "win_condition": "checkmate_or_king_capture",
            },
            "matryoshka current baseline",
        ),
        (
            "anchor_mat_ko_stalemate_loss",
            {
                "ruleset": "matryoshka",
                "tier2_slider_max_range": 3,
                "tier3_slider_max_range": 1,
                "retaliation_strike_window": 2,
                "fallback_policy": "king_proximity",
                "king_infinite_kill": False,
                "king_move_mode": "normal",
                "king_dash_max": 2,
                "king_k_range": 2,
                "king_capture_line_range": 2,
                "king_capture_insta_kill": "adjacent_only",
                "retaliation_enabled": True,
                "retaliation_targeting": "localized_safe",
                "retaliation_local_radius": 4,
                "retaliation_tiebreak": "min_king_distance",
                "strike_effect": "double_demote",
                "stalemate_is_loss": True,
                "ko_repetition_illegal": True,
                "doom_clock_full_moves": 24,
                "doom_clock_effect": "collapse_weakest",
                "quiet_halfmove_limit": 60,
                "knight_decay_mode": "camel",
                "collapse_target": "crippled_pawn",
                "crippled_pawn_can_promote": False,
                "win_condition": "checkmate_or_king_capture",
            },
            "anti-draw stress anchor",
        ),
        (
            "anchor_mat_no_retaliation",
            {
                "ruleset": "matryoshka",
                "tier2_slider_max_range": 3,
                "tier3_slider_max_range": 1,
                "retaliation_strike_window": 1,
                "fallback_policy": "nearest_circe",
                "king_infinite_kill": False,
                "king_move_mode": "king_k_range",
                "king_dash_max": 2,
                "king_k_range": 2,
                "king_capture_line_range": 2,
                "king_capture_insta_kill": "off",
                "retaliation_enabled": False,
                "retaliation_targeting": "highest_safe",
                "retaliation_local_radius": 4,
                "retaliation_tiebreak": "random",
                "strike_effect": "perma_kill",
                "stalemate_is_loss": False,
                "ko_repetition_illegal": False,
                "doom_clock_full_moves": 0,
                "doom_clock_effect": "demote_random_non_king",
                "quiet_halfmove_limit": 100,
                "knight_decay_mode": "diag_step",
                "collapse_target": "pawn",
                "crippled_pawn_can_promote": False,
                "win_condition": "checkmate_only",
            },
            "retaliation-off comparison",
        ),
    ]
    out: List[VariantSpec] = []
    for variant_id, lever_dict, note in anchors:
        out.append(
            VariantSpec(
                variant_id=variant_id,
                stage="stage1",
                rules=dict_to_rules(lever_dict),
                levers=normalize_rule_values(lever_dict),
                notes=note,
            )
        )
    return out


def sample_stage1_variants(
    cfg: Dict[str, object],
    rng: random.Random,
    anchors: Sequence[VariantSpec],
) -> List[VariantSpec]:
    stage1_cfg = cfg["stage1"]  # type: ignore[index]
    target_n = int(stage1_cfg["num_variants"])  # type: ignore[index]
    search_space = cfg["search_space"]  # type: ignore[index]

    keys = sorted(search_space.keys())  # deterministic order
    random_needed = max(0, target_n - len(anchors))
    matrix = latin_hypercube_matrix(rng, random_needed, len(keys))

    variants: List[VariantSpec] = list(anchors)
    seen = {rules_signature(v.rules) for v in variants}

    for idx, units in enumerate(matrix, start=1):
        raw: Dict[str, object] = {}
        for key, unit in zip(keys, units):
            raw[key] = sample_from_choices(unit, search_space[key])  # type: ignore[index]
        rules = dict_to_rules(raw)
        sig = rules_signature(rules)
        if sig in seen:
            continue
        seen.add(sig)
        variants.append(
            VariantSpec(
                variant_id=f"s1_{idx:04d}_{sig[:8]}",
                stage="stage1",
                rules=rules,
                levers=dataclasses.asdict(rules),
                notes="lhs_sample",
            )
        )
        if len(variants) >= target_n:
            break

    return variants[:target_n]


def mutate_levers(
    base_levers: Dict[str, object],
    search_space: Dict[str, Sequence[object]],
    rng: random.Random,
    edits: int,
) -> Dict[str, object]:
    out = dict(base_levers)
    mutable = [k for k in search_space.keys() if len(search_space[k]) > 1]
    rng.shuffle(mutable)
    for key in mutable[: max(1, edits)]:
        choices = list(search_space[key])
        current = out.get(key)
        choices = [x for x in choices if x != current]
        if not choices:
            continue
        out[key] = rng.choice(choices)
    return normalize_rule_values(out)


def non_dominated(rows: Sequence[Dict[str, object]]) -> List[Dict[str, object]]:
    def dominates(a: Dict[str, object], b: Dict[str, object]) -> bool:
        a_draw = float(a["draw_rate"])
        b_draw = float(b["draw_rate"])
        a_plies = float(a["mean_plies"])
        b_plies = float(b["mean_plies"])
        a_imb = float(a["imbalance_abs"])
        b_imb = float(b["imbalance_abs"])
        a_nov = float(a["novelty_kl"])
        b_nov = float(b["novelty_kl"])

        no_worse = (
            a_draw <= b_draw
            and a_plies <= b_plies
            and a_imb <= b_imb
            and a_nov >= b_nov
        )
        strictly = (
            a_draw < b_draw
            or a_plies < b_plies
            or a_imb < b_imb
            or a_nov > b_nov
        )
        return no_worse and strictly

    frontier: List[Dict[str, object]] = []
    for row in rows:
        dominated = False
        for other in rows:
            if other is row:
                continue
            if dominates(other, row):
                dominated = True
                break
        if not dominated:
            frontier.append(row)
    return frontier


def kl_divergence(p: Dict[str, float], q: Dict[str, float], eps: float = 1e-9) -> float:
    keys = set(p.keys()) | set(q.keys())
    total = 0.0
    for key in keys:
        pv = max(eps, p.get(key, 0.0))
        qv = max(eps, q.get(key, 0.0))
        total += pv * math.log(pv / qv)
    return total


def normalize_distribution(counts: Counter[str]) -> Dict[str, float]:
    total = float(sum(counts.values()))
    if total <= 0:
        return {}
    return {k: (v / total) for k, v in counts.items()}


def summarize_games(
    games: Sequence[Dict[str, object]],
    baseline_piece_distribution: Optional[Dict[str, float]],
) -> Dict[str, object]:
    n = len(games)
    if n == 0:
        return {
            "num_games": 0,
            "draw_rate": 0.0,
            "mean_plies": 0.0,
            "interestingness": 0.0,
            "novelty_kl": 0.0,
            "imbalance_abs": 0.0,
            "score_components": {},
            "constraints_failed": [],
            "piece_type_distribution": {},
        }

    winner_counts = Counter(str(g["winner"]) for g in games)
    termination_counts = Counter(str(g["termination"]) for g in games)
    plies = [float(g["plies"]) for g in games]
    captures = [float(g["captures_total"]) for g in games]
    permanent = [float(g["permanent_removals_total"]) for g in games]
    checks = [float(g["white_checks"]) + float(g["black_checks"]) for g in games]
    vol_per_ply = [float(g["material_volatility"]) / max(1.0, float(g["plies"])) for g in games]

    white_wins = winner_counts.get(WHITE, 0)
    black_wins = winner_counts.get(BLACK, 0)
    draws = winner_counts.get(DRAW, 0)

    white_rate = white_wins / n
    black_rate = black_wins / n
    draw_rate = draws / n

    mean_plies = statistics.mean(plies)
    median_plies = statistics.median(plies)
    p10_plies = percentile(plies, 10.0)
    p90_plies = percentile(plies, 90.0)

    total_plies = max(1.0, sum(plies))
    captures_per_100 = (sum(captures) / total_plies) * 100.0
    permanent_per_100 = (sum(permanent) / total_plies) * 100.0
    checks_per_100 = (sum(checks) / total_plies) * 100.0
    swinginess = statistics.pstdev(vol_per_ply) if len(vol_per_ply) > 1 else 0.0

    snapshots_piece_counts: Dict[str, List[float]] = {"40": [], "80": [], "120": []}
    piece_type_counter: Counter[str] = Counter()
    for game in games:
        snaps = game.get("snapshots", {})
        if isinstance(snaps, dict):
            for key in ("40", "80", "120"):
                if key in snaps:
                    piece_count = snaps[key].get("piece_count", 0)  # type: ignore[index]
                    snapshots_piece_counts[key].append(float(piece_count))
                    pt = snaps[key].get("piece_type_counts", {})  # type: ignore[index]
                    if isinstance(pt, dict):
                        for k, v in pt.items():
                            piece_type_counter[str(k)] += int(v)

    piece_distribution = normalize_distribution(piece_type_counter)
    novelty_kl = 0.0
    if baseline_piece_distribution:
        novelty_kl = kl_divergence(piece_distribution, baseline_piece_distribution)

    imbalance_abs = abs(white_rate - black_rate)
    white_advantage = white_rate - black_rate

    # Secondary scalar score (Pareto remains primary).
    decisiveness = max(0.0, 1.0 - draw_rate)
    pace = max(0.0, min(1.0, (175.0 - mean_plies) / 115.0))
    tactics = max(0.0, min(1.0, (captures_per_100 + checks_per_100) / 68.0))
    novelty = max(0.0, min(1.0, novelty_kl / 0.35))
    fairness_penalty = max(0.0, min(1.0, imbalance_abs / 0.10))

    constraints_failed: List[str] = []
    if draw_rate > 0.55 and novelty < 0.85:
        constraints_failed.append("draw_rate_gt_55")
    if mean_plies > 170.0:
        constraints_failed.append("mean_plies_gt_170")
    if imbalance_abs > 0.10:
        constraints_failed.append("imbalance_gt_10pt")

    interestingness = (
        (0.30 * decisiveness)
        + (0.25 * pace)
        + (0.20 * tactics)
        + (0.20 * novelty)
        + (0.05 * (1.0 - fairness_penalty))
    )
    if constraints_failed:
        interestingness *= 0.4

    board_clog = {
        f"pieces_ply_{k}": (statistics.mean(v) if v else 0.0)
        for k, v in snapshots_piece_counts.items()
    }

    draw_signatures = Counter()
    for game in games:
        forensics = game.get("draw_forensics")
        if isinstance(forensics, dict):
            sig = forensics.get("signature_hash")
            if sig:
                draw_signatures[str(sig)] += 1

    return {
        "num_games": n,
        "winner_counts": dict(winner_counts),
        "termination_counts": dict(termination_counts),
        "draw_rate": draw_rate,
        "white_win_rate": white_rate,
        "black_win_rate": black_rate,
        "draw_rate_ci95": ci95_proportion(draws, n),
        "white_win_ci95": ci95_proportion(white_wins, n),
        "black_win_ci95": ci95_proportion(black_wins, n),
        "mean_plies": mean_plies,
        "mean_plies_ci95": ci95_mean(plies),
        "median_plies": median_plies,
        "plies_p10": p10_plies,
        "plies_p90": p90_plies,
        "captures_per_100_plies": captures_per_100,
        "permanent_removals_per_100_plies": permanent_per_100,
        "checks_per_100_plies": checks_per_100,
        "swinginess": swinginess,
        "imbalance_abs": imbalance_abs,
        "white_advantage": white_advantage,
        "novelty_kl": novelty_kl,
        "interestingness": interestingness,
        "score_components": {
            "decisiveness": decisiveness,
            "pace": pace,
            "tactics": tactics,
            "novelty": novelty,
            "fairness_penalty": fairness_penalty,
        },
        "constraints_failed": constraints_failed,
        "piece_type_distribution": piece_distribution,
        "draw_signature_counts": dict(draw_signatures),
        **board_clog,
    }


def make_seed_bases(variant: VariantSpec, seeds_per_variant: int, base_seed: int) -> List[int]:
    sig = int(rules_signature(variant.rules)[:12], 16)
    rng = random.Random(base_seed ^ sig)
    seeds = set()
    while len(seeds) < seeds_per_variant:
        seeds.add(rng.randint(1, 2_147_000_000))
    return list(seeds)


def expand_game_seed(seed_base: int, game_idx: int) -> int:
    # Fast deterministic mixer to decorrelate sequential game ids.
    mixed = (seed_base * 1_000_003 + (game_idx + 17) * 97_457) % 2_147_000_000
    return int(mixed) + 1


def flatten_game_row(variant: VariantSpec, game: Dict[str, object]) -> Dict[str, object]:
    draw_sig = ""
    forensics = game.get("draw_forensics")
    if isinstance(forensics, dict):
        draw_sig = str(forensics.get("signature_hash", ""))
    return {
        "variant_id": variant.variant_id,
        "stage": variant.stage,
        "parent_id": variant.parent_id or "",
        "seed": int(game["seed"]),
        "start_side": str(game.get("start_side", WHITE)),
        "winner": str(game["winner"]),
        "termination": str(game["termination"]),
        "plies": int(game["plies"]),
        "captures_total": int(game["captures_total"]),
        "permanent_removals_total": int(game["permanent_removals_total"]),
        "checks_total": int(game["white_checks"]) + int(game["black_checks"]),
        "material_volatility": float(game["material_volatility"]),
        "draw_signature_hash": draw_sig,
    }


def _worker(task: Tuple[int, int, str, int, RuleConfig, Sequence[int]]) -> Dict[str, object]:
    seed_group, game_seed, start_side, max_plies, rules, snapshot_plies = task
    game = run_single_game(
        seed=game_seed,
        max_plies=max_plies,
        rules=rules,
        include_move_log=False,
        start_side=start_side,
        snapshot_plies=snapshot_plies,
    )
    game["seed_group"] = seed_group
    return game


def run_variant(
    variant: VariantSpec,
    out_dir: Path,
    seeds_per_variant: int,
    games_per_seed: int,
    max_plies: int,
    workers: int,
    snapshot_plies: Sequence[int],
    base_seed: int,
    progress_every_games: int,
    variant_timebox_minutes: float,
    baseline_piece_distribution: Optional[Dict[str, float]],
    progress_log_path: Path,
    run_state_path: Path,
    stage_name: str,
    stage_variant_index: int,
    stage_variant_total: int,
    stage_games_completed_before: int,
    stage_games_total: int,
) -> Tuple[List[Dict[str, object]], Dict[str, object], Dict[str, Dict[str, object]], bool]:
    variant_dir = out_dir / "variants" / variant.variant_id
    variant_dir.mkdir(parents=True, exist_ok=True)

    seed_bases = make_seed_bases(variant, seeds_per_variant=seeds_per_variant, base_seed=base_seed)
    with (variant_dir / "seed_manifest.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["seed_group", "seed_base"])
        for idx, seed_base in enumerate(seed_bases):
            w.writerow([idx, seed_base])

    tasks: List[Tuple[int, int, str, int, RuleConfig, Sequence[int]]] = []
    for group_idx, seed_base in enumerate(seed_bases):
        for game_idx in range(games_per_seed):
            game_seed = expand_game_seed(seed_base, game_idx)
            start_side = WHITE if (game_idx % 2 == 0) else BLACK
            tasks.append(
                (
                    group_idx,
                    game_seed,
                    start_side,
                    max_plies,
                    variant.rules,
                    tuple(snapshot_plies),
                )
            )

    random.Random(base_seed ^ int(rules_signature(variant.rules)[:8], 16)).shuffle(tasks)
    effective_progress_every = max(
        1,
        min(
            max(1, progress_every_games),
            max(1, len(tasks) // 10),
        ),
    )

    games: List[Dict[str, object]] = []
    draw_signatures: Dict[str, Dict[str, object]] = {}
    timed_out = False
    start_ts = time.time()

    games_csv_path = variant_dir / "games_partial.csv"
    with games_csv_path.open("w", newline="", encoding="utf-8") as f_csv:
        game_writer = csv.DictWriter(
            f_csv,
            fieldnames=[
                "variant_id",
                "stage",
                "parent_id",
                "seed",
                "start_side",
                "winner",
                "termination",
                "plies",
                "captures_total",
                "permanent_removals_total",
                "checks_total",
                "material_volatility",
                "draw_signature_hash",
            ],
        )
        game_writer.writeheader()

        pool = mp.Pool(processes=max(1, workers))
        try:
            for i, game in enumerate(pool.imap_unordered(_worker, tasks, chunksize=8), start=1):
                games.append(game)
                game_writer.writerow(flatten_game_row(variant, game))

                forensics = game.get("draw_forensics")
                if isinstance(forensics, dict):
                    sig = str(forensics.get("signature_hash", ""))
                    if sig:
                        if sig not in draw_signatures:
                            draw_signatures[sig] = {
                                "count": 0,
                                "sample": forensics,
                            }
                        draw_signatures[sig]["count"] = int(draw_signatures[sig]["count"]) + 1

                if (i % effective_progress_every) == 0 or i == len(tasks):
                    partial = summarize_games(games, baseline_piece_distribution)
                    elapsed_sec = time.time() - start_ts
                    stage_games_now = stage_games_completed_before + i
                    atomic_write_json(
                        variant_dir / "partial_summary.json",
                        {
                            "variant_id": variant.variant_id,
                            "stage": variant.stage,
                            "completed_games": i,
                            "total_games": len(tasks),
                            "elapsed_sec": round(elapsed_sec, 2),
                            "partial_metrics": partial,
                            "rules": dataclasses.asdict(variant.rules),
                            "updated_at": now_iso(),
                        },
                    )
                    append_jsonl(
                        progress_log_path,
                        {
                            "ts": now_iso(),
                            "event": "variant_progress",
                            "variant_id": variant.variant_id,
                            "stage": variant.stage,
                            "completed_games": i,
                            "total_games": len(tasks),
                            "elapsed_sec": round(elapsed_sec, 2),
                            "partial_draw_rate": round(float(partial["draw_rate"]), 4),
                            "partial_mean_plies": round(float(partial["mean_plies"]), 2),
                        },
                    )
                    atomic_write_json(
                        run_state_path,
                        {
                            "updated_at": now_iso(),
                            "active_variant": variant.variant_id,
                            "active_stage": variant.stage,
                            "variant_completed_games": i,
                            "variant_total_games": len(tasks),
                            "variant_elapsed_sec": round(elapsed_sec, 2),
                            "stage_completed_games": stage_games_now,
                            "stage_total_games": stage_games_total,
                        },
                    )
                    f_csv.flush()

                    variant_bar = progress_bar(i, len(tasks))
                    stage_bar = progress_bar(stage_games_now, stage_games_total)
                    variant_pct = (100.0 * i / len(tasks)) if len(tasks) else 0.0
                    stage_pct = (
                        (100.0 * stage_games_now / stage_games_total)
                        if stage_games_total
                        else 0.0
                    )
                    print(
                        f"[progress] {stage_name} v{stage_variant_index}/{stage_variant_total} "
                        f"{variant.variant_id} "
                        f"variant {variant_bar} {variant_pct:5.1f}% ({i}/{len(tasks)}) "
                        f"stage {stage_bar} {stage_pct:5.1f}% ({stage_games_now}/{stage_games_total}) "
                        f"elapsed={format_seconds(elapsed_sec)}"
                    )

                if variant_timebox_minutes > 0:
                    elapsed_minutes = (time.time() - start_ts) / 60.0
                    if elapsed_minutes >= variant_timebox_minutes:
                        timed_out = True
                        append_jsonl(
                            progress_log_path,
                            {
                                "ts": now_iso(),
                                "event": "variant_timebox_hit",
                                "variant_id": variant.variant_id,
                                "elapsed_minutes": round(elapsed_minutes, 2),
                            },
                        )
                        pool.terminate()
                        break
            else:
                pool.close()
            pool.join()
        except KeyboardInterrupt:
            pool.terminate()
            pool.join()
            raise

    metrics = summarize_games(games, baseline_piece_distribution)
    metrics["timed_out"] = timed_out
    metrics["games_requested"] = len(tasks)
    metrics["games_completed"] = len(games)
    metrics["elapsed_sec"] = round(time.time() - start_ts, 2)

    with (variant_dir / "draw_forensics.jsonl").open("w", encoding="utf-8") as f:
        for game in games:
            forensics = game.get("draw_forensics")
            if isinstance(forensics, dict):
                payload = {"variant_id": variant.variant_id, "seed": int(game["seed"]), **forensics}
                f.write(json.dumps(payload, sort_keys=True))
                f.write("\n")

    top_draw = sorted(
        draw_signatures.items(),
        key=lambda kv: int(kv[1]["count"]),
        reverse=True,
    )[:10]
    with (variant_dir / "draw_forensics_top.md").open("w", encoding="utf-8") as f:
        f.write(f"# Draw Forensics - {variant.variant_id}\n\n")
        f.write("| Signature | Count | Termination | Last20 |\n")
        f.write("|---|---:|---|---|\n")
        for sig, blob in top_draw:
            sample = blob["sample"]
            term = sample.get("termination_reason", "")
            last20 = " ".join(sample.get("last_20_moves", [])[:8])
            f.write(f"| `{sig}` | {blob['count']} | {term} | {last20} |\n")

    atomic_write_json(
        variant_dir / "summary.json",
        {
            "variant_id": variant.variant_id,
            "stage": variant.stage,
            "parent_id": variant.parent_id,
            "notes": variant.notes,
            "rules": dataclasses.asdict(variant.rules),
            "metrics": metrics,
            "draw_signature_counts": {
                sig: int(blob["count"]) for sig, blob in draw_signatures.items()
            },
            "updated_at": now_iso(),
        },
    )

    return games, metrics, draw_signatures, timed_out


def write_variant_summary_csv(path: Path, rows: Sequence[Dict[str, object]]) -> None:
    fieldnames = [
        "variant_id",
        "stage",
        "parent_id",
        "games_completed",
        "draw_rate",
        "draw_rate_ci95",
        "white_win_rate",
        "white_win_ci95",
        "black_win_rate",
        "black_win_ci95",
        "imbalance_abs",
        "mean_plies",
        "mean_plies_ci95",
        "plies_p10",
        "plies_p90",
        "captures_per_100_plies",
        "permanent_removals_per_100_plies",
        "checks_per_100_plies",
        "swinginess",
        "novelty_kl",
        "interestingness",
        "timed_out",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in fieldnames})


def write_variant_table_md(path: Path, rows: Sequence[Dict[str, object]]) -> None:
    ranked = sorted(rows, key=lambda r: float(r.get("interestingness", 0.0)), reverse=True)
    with path.open("w", encoding="utf-8") as f:
        f.write("# Variant Summary\n\n")
        f.write(
            "| Variant | Stage | Draw% | White% | Black% | Mean plies | Captures/100 | Checks/100 | Imbalance | Novelty KL | Interestingness |\n"
        )
        f.write("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|\n")
        for row in ranked:
            f.write(
                f"| {row['variant_id']} | {row['stage']} | "
                f"{100.0*float(row['draw_rate']):.2f} | "
                f"{100.0*float(row['white_win_rate']):.2f} | "
                f"{100.0*float(row['black_win_rate']):.2f} | "
                f"{float(row['mean_plies']):.1f} | "
                f"{float(row['captures_per_100_plies']):.2f} | "
                f"{float(row['checks_per_100_plies']):.2f} | "
                f"{100.0*float(row['imbalance_abs']):.2f} | "
                f"{float(row['novelty_kl']):.3f} | "
                f"{float(row['interestingness']):.3f} |\n"
            )


def write_pareto(path: Path, rows: Sequence[Dict[str, object]]) -> List[Dict[str, object]]:
    frontier = non_dominated(rows)
    frontier_sorted = sorted(frontier, key=lambda r: float(r["interestingness"]), reverse=True)
    with path.open("w", encoding="utf-8") as f:
        f.write("# Pareto Frontier\n\n")
        f.write(
            "Objectives: minimize draw rate, minimize mean plies, minimize imbalance, maximize novelty.\n\n"
        )
        f.write("| Variant | Draw% | Mean plies | Imbalance | Novelty KL | Interestingness |\n")
        f.write("|---|---:|---:|---:|---:|---:|\n")
        for row in frontier_sorted:
            f.write(
                f"| {row['variant_id']} | "
                f"{100.0*float(row['draw_rate']):.2f} | "
                f"{float(row['mean_plies']):.1f} | "
                f"{100.0*float(row['imbalance_abs']):.2f} | "
                f"{float(row['novelty_kl']):.3f} | "
                f"{float(row['interestingness']):.3f} |\n"
            )
    return frontier_sorted


def write_metric_glossary(path: Path) -> None:
    with path.open("w", encoding="utf-8") as f:
        f.write("# Metric Glossary\n\n")
        f.write("- `draw_rate`: draw outcomes / total games.\n")
        f.write("- `white_win_rate`, `black_win_rate`: per-color win shares.\n")
        f.write("- `imbalance_abs`: `|white_win_rate - black_win_rate|`.\n")
        f.write("- `mean_plies`, `plies_p10`, `plies_p90`: pacing distribution.\n")
        f.write("- `captures_per_100_plies`: total captures normalized by plies.\n")
        f.write("- `permanent_removals_per_100_plies`: perma removals normalized by plies.\n")
        f.write("- `checks_per_100_plies`: checks normalized by plies.\n")
        f.write("- `swinginess`: stddev of per-game volatility-per-ply proxy.\n")
        f.write("- `novelty_kl`: KL divergence of piece-type snapshot distribution vs normal reference.\n")
        f.write("- `interestingness`: weighted scalar helper (secondary to Pareto).\n")


def build_raw_games_csv(out_dir: Path) -> None:
    variant_root = out_dir / "variants"
    rows_written = 0
    header: Optional[List[str]] = None
    with (out_dir / "raw_games.csv").open("w", newline="", encoding="utf-8") as f_out:
        writer: Optional[csv.DictWriter] = None
        for csv_path in sorted(variant_root.glob("*/games_partial.csv")):
            with csv_path.open("r", newline="", encoding="utf-8") as f_in:
                reader = csv.DictReader(f_in)
                if reader.fieldnames is None:
                    continue
                if header is None:
                    header = list(reader.fieldnames)
                    writer = csv.DictWriter(f_out, fieldnames=header)
                    writer.writeheader()
                if writer is None:
                    continue
                for row in reader:
                    writer.writerow(row)
                    rows_written += 1
    print(f"[raw_games] rows={rows_written} -> {out_dir / 'raw_games.csv'}")


def write_draw_forensics_global(
    out_dir: Path,
    draw_db: Dict[str, Dict[str, object]],
) -> None:
    df_dir = out_dir / "draw_forensics"
    examples_dir = df_dir / "examples"
    examples_dir.mkdir(parents=True, exist_ok=True)

    top = sorted(draw_db.items(), key=lambda kv: int(kv[1]["count"]), reverse=True)[:10]
    with (df_dir / "top_signatures.md").open("w", encoding="utf-8") as f:
        f.write("# Global Draw Signatures\n\n")
        f.write("| Signature | Count | Termination | Variants |\n")
        f.write("|---|---:|---|---|\n")
        for sig, blob in top:
            sample = blob.get("sample", {})
            term = sample.get("termination_reason", "")
            variants = ", ".join(sorted(blob.get("variants", []))[:5])
            f.write(f"| `{sig}` | {blob['count']} | {term} | {variants} |\n")
            with (examples_dir / f"{sig}.txt").open("w", encoding="utf-8") as ef:
                ef.write(f"signature: {sig}\n")
                ef.write(f"count: {blob['count']}\n")
                ef.write(f"termination: {term}\n")
                ef.write("last_20_moves:\n")
                for mv in sample.get("last_20_moves", []):
                    ef.write(f"  {mv}\n")
                ef.write("\nboard:\n")
                ef.write(sample.get("final_board_ascii", ""))
                ef.write("\n")


def write_optimizer_report(
    out_path: Path,
    ranked_rows: Sequence[Dict[str, object]],
    frontier_rows: Sequence[Dict[str, object]],
    timed_out_variants: Sequence[str],
    additional_ideas: Sequence[str],
) -> None:
    top10 = list(ranked_rows[:10])
    frontier_top = list(frontier_rows[:10])
    with out_path.open("w", encoding="utf-8") as f:
        f.write("# Variant Optimization Report\n\n")
        f.write("## Top 10 Variants\n\n")
        for idx, row in enumerate(top10, start=1):
            rationale = (
                f"draw {100.0*float(row['draw_rate']):.1f}%, "
                f"plies {float(row['mean_plies']):.1f}, "
                f"imbalance {100.0*float(row['imbalance_abs']):.1f}%, "
                f"novelty {float(row['novelty_kl']):.3f}, "
                f"levers [{row.get('rules_short', '')}]"
            )
            f.write(f"{idx}. **{row['variant_id']}** ({row['stage']}) - {rationale}\n")

        f.write("\n## Pareto Frontier (Top 10 by Interestingness)\n\n")
        for idx, row in enumerate(frontier_top, start=1):
            f.write(
                f"{idx}. {row['variant_id']} - draw {100.0*float(row['draw_rate']):.1f}%, "
                f"plies {float(row['mean_plies']):.1f}, "
                f"imbalance {100.0*float(row['imbalance_abs']):.1f}%, "
                f"novelty {float(row['novelty_kl']):.3f}\n"
            )

        f.write("\n## Recommendations (Next Iteration)\n\n")
        for idx, row in enumerate(top10[:3], start=1):
            f.write(
                f"{idx}. Iterate `{row['variant_id']}` with +30% more games and two nearest mutations.\n"
            )

        if timed_out_variants:
            f.write("\n## Timebox Notes\n\n")
            f.write(
                "The following variants hit per-variant timebox and were summarized with partial data:\n"
            )
            for vid in timed_out_variants:
                f.write(f"- {vid}\n")

        f.write("\n## Additional Lever Ideas\n\n")
        for idea in additional_ideas:
            f.write(f"- {idea}\n")


def derive_stage_budget(
    stage_cfg: Dict[str, object],
    variants: int,
    budget_games: float,
) -> StageBudget:
    seeds_per_variant = int(stage_cfg["seeds_per_variant"])
    min_gps = int(stage_cfg["min_games_per_seed"])
    max_gps = int(stage_cfg["max_games_per_seed"])

    raw = int(budget_games / max(1, variants * seeds_per_variant))
    games_per_seed = clamp_int(raw, min_gps, max_gps)
    total_games = variants * seeds_per_variant * games_per_seed
    return StageBudget(
        variants=variants,
        seeds_per_variant=seeds_per_variant,
        games_per_seed=games_per_seed,
        total_games=total_games,
    )


def calibrate_games_per_second(
    cfg: Dict[str, object],
    out_dir: Path,
    progress_log_path: Path,
) -> float:
    calibration_games = int(cfg["calibration_games"])
    workers = int(cfg["workers"])
    max_plies = int(cfg["max_plies"])
    snapshot_plies = tuple(int(x) for x in cfg["snapshot_plies"])  # type: ignore[index]

    base_variant = make_anchor_variants()[1]  # mat baseline
    seeds = make_seed_bases(base_variant, seeds_per_variant=1, base_seed=int(cfg["base_seed"]))
    seed_base = seeds[0]
    tasks = []
    for i in range(calibration_games):
        tasks.append(
            (
                0,
                expand_game_seed(seed_base, i),
                WHITE if i % 2 == 0 else BLACK,
                max_plies,
                base_variant.rules,
                snapshot_plies,
            )
        )

    start = time.time()
    pool = mp.Pool(processes=max(1, workers))
    pool_closed = False
    try:
        completed = 0
        for _ in pool.imap_unordered(_worker, tasks, chunksize=8):
            completed += 1
        pool.close()
        pool_closed = True
        pool.join()
    finally:
        if not pool_closed:
            pool.terminate()
            pool.join()
    elapsed = max(0.001, time.time() - start)
    gps = completed / elapsed

    append_jsonl(
        progress_log_path,
        {
            "ts": now_iso(),
            "event": "calibration_complete",
            "games": completed,
            "elapsed_sec": round(elapsed, 3),
            "games_per_sec": round(gps, 4),
        },
    )
    atomic_write_json(
        out_dir / "calibration.json",
        {
            "games": completed,
            "elapsed_sec": round(elapsed, 3),
            "games_per_sec": round(gps, 4),
            "workers": workers,
            "max_plies": max_plies,
        },
    )
    return gps


def select_elites_for_stage2(
    rows: Sequence[Dict[str, object]],
    elite_count: int,
) -> List[Dict[str, object]]:
    frontier = non_dominated(rows)
    frontier_sorted = sorted(frontier, key=lambda r: float(r["interestingness"]), reverse=True)
    ranked = sorted(rows, key=lambda r: float(r["interestingness"]), reverse=True)

    selected: List[Dict[str, object]] = []
    seen = set()
    for row in frontier_sorted + ranked:
        vid = str(row["variant_id"])
        if vid in seen:
            continue
        selected.append(row)
        seen.add(vid)
        if len(selected) >= elite_count:
            break
    return selected


def rules_short_label(rules: RuleConfig) -> str:
    r = dataclasses.asdict(rules)
    return (
        f"ruleset={r['ruleset']};"
        f"king={r['king_move_mode']};"
        f"t2={r['tier2_slider_max_range']};"
        f"sw={r['retaliation_strike_window']};"
        f"ret={r['retaliation_enabled']};"
        f"ko={r['ko_repetition_illegal']};"
        f"doom={r['doom_clock_full_moves']}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run two-stage variant optimizer.")
    parser.add_argument("--config", required=True, help="Path to JSON config")
    parser.add_argument("--out", required=True, help="Output directory")
    parser.add_argument("--smoke", action="store_true", help="Fast smoke mode")
    parser.add_argument("--workers", type=int, default=0, help="Override worker count")
    parser.add_argument("--target-hours", type=float, default=0.0, help="Override target hours")
    args = parser.parse_args()

    with open(args.config, "r", encoding="utf-8") as f:
        user_cfg = json.load(f)
    cfg = deep_merge(DEFAULT_CONFIG, user_cfg)
    if args.workers > 0:
        cfg["workers"] = args.workers
    if args.target_hours > 0:
        cfg["target_hours"] = args.target_hours

    if args.smoke:
        cfg["target_hours"] = 0.15
        cfg["calibration_games"] = 20
        cfg["stage1"]["num_variants"] = 10  # type: ignore[index]
        cfg["stage2"]["elite_count"] = 4  # type: ignore[index]
        cfg["stage2"]["mutations_per_elite"] = 1  # type: ignore[index]
        cfg["stage1"]["min_games_per_seed"] = 3  # type: ignore[index]
        cfg["stage1"]["max_games_per_seed"] = 5  # type: ignore[index]
        cfg["stage2"]["min_games_per_seed"] = 3  # type: ignore[index]
        cfg["stage2"]["max_games_per_seed"] = 6  # type: ignore[index]
        cfg["variant_timebox_minutes"] = 2.0

    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "variants").mkdir(exist_ok=True)

    progress_log_path = out_dir / "progress.jsonl"
    run_state_path = out_dir / "run_state.json"
    append_jsonl(progress_log_path, {"ts": now_iso(), "event": "run_start", "config": cfg})
    atomic_write_json(run_state_path, {"updated_at": now_iso(), "status": "running"})

    rng = random.Random(int(cfg["base_seed"]))
    anchors = make_anchor_variants()
    stage1_variants = sample_stage1_variants(cfg, rng, anchors=anchors)

    stage2_cfg = cfg["stage2"]  # type: ignore[index]
    stage2_estimated_variants = int(stage2_cfg["elite_count"]) * (
        1 + int(stage2_cfg["mutations_per_elite"])
    )

    games_per_sec = calibrate_games_per_second(cfg, out_dir, progress_log_path)
    total_budget_games = games_per_sec * float(cfg["target_hours"]) * 3600.0 * float(cfg["utilization"])

    stage1_budget_games = total_budget_games * 0.65
    stage2_budget_games = total_budget_games * 0.35

    stage1_budget = derive_stage_budget(
        stage_cfg=cfg["stage1"],  # type: ignore[arg-type]
        variants=len(stage1_variants),
        budget_games=stage1_budget_games,
    )
    stage2_budget = derive_stage_budget(
        stage_cfg=cfg["stage2"],  # type: ignore[arg-type]
        variants=max(1, stage2_estimated_variants),
        budget_games=stage2_budget_games,
    )

    atomic_write_json(
        out_dir / "run_plan.json",
        {
            "target_hours": cfg["target_hours"],
            "workers": cfg["workers"],
            "games_per_sec_estimate": round(games_per_sec, 4),
            "total_budget_games": int(total_budget_games),
            "stage1": dataclasses.asdict(stage1_budget),
            "stage2": dataclasses.asdict(stage2_budget),
            "stage1_variants": len(stage1_variants),
            "stage2_estimated_variants": stage2_estimated_variants,
        },
    )

    print(
        f"[plan] stage1 variants={len(stage1_variants)} seeds={stage1_budget.seeds_per_variant} "
        f"games/seed={stage1_budget.games_per_seed} total_games={stage1_budget.total_games}"
    )
    print(
        f"[plan] stage2 est variants={stage2_estimated_variants} seeds={stage2_budget.seeds_per_variant} "
        f"games/seed={stage2_budget.games_per_seed} total_games={stage2_budget.total_games}"
    )

    all_rows: List[Dict[str, object]] = []
    all_specs: Dict[str, VariantSpec] = {}
    global_draw_db: Dict[str, Dict[str, object]] = {}
    timed_out_variants: List[str] = []

    baseline_piece_distribution: Optional[Dict[str, float]] = None
    variant_timebox = float(cfg["variant_timebox_minutes"])
    snapshot_plies = [int(x) for x in cfg["snapshot_plies"]]  # type: ignore[index]

    def integrate_variant_result(
        variant: VariantSpec,
        metrics: Dict[str, object],
        draw_signatures: Dict[str, Dict[str, object]],
    ) -> None:
        row = {
            "variant_id": variant.variant_id,
            "stage": variant.stage,
            "parent_id": variant.parent_id or "",
            "rules_short": rules_short_label(variant.rules),
            "games_completed": int(metrics["games_completed"]),
            "draw_rate": float(metrics["draw_rate"]),
            "draw_rate_ci95": float(metrics["draw_rate_ci95"]),
            "white_win_rate": float(metrics["white_win_rate"]),
            "white_win_ci95": float(metrics["white_win_ci95"]),
            "black_win_rate": float(metrics["black_win_rate"]),
            "black_win_ci95": float(metrics["black_win_ci95"]),
            "imbalance_abs": float(metrics["imbalance_abs"]),
            "mean_plies": float(metrics["mean_plies"]),
            "mean_plies_ci95": float(metrics["mean_plies_ci95"]),
            "plies_p10": float(metrics["plies_p10"]),
            "plies_p90": float(metrics["plies_p90"]),
            "captures_per_100_plies": float(metrics["captures_per_100_plies"]),
            "permanent_removals_per_100_plies": float(
                metrics["permanent_removals_per_100_plies"]
            ),
            "checks_per_100_plies": float(metrics["checks_per_100_plies"]),
            "swinginess": float(metrics["swinginess"]),
            "novelty_kl": float(metrics["novelty_kl"]),
            "interestingness": float(metrics["interestingness"]),
            "timed_out": bool(metrics.get("timed_out", False)),
        }
        all_rows.append(row)
        all_specs[variant.variant_id] = variant
        for sig, blob in draw_signatures.items():
            if sig not in global_draw_db:
                global_draw_db[sig] = {
                    "count": 0,
                    "sample": blob.get("sample", {}),
                    "variants": set(),
                }
            global_draw_db[sig]["count"] = int(global_draw_db[sig]["count"]) + int(blob["count"])
            global_draw_db[sig]["variants"].add(variant.variant_id)

        write_variant_summary_csv(out_dir / "variant_summary.csv", all_rows)
        write_variant_table_md(out_dir / "variant_table.md", all_rows)
        frontier_rows = write_pareto(out_dir / "pareto_frontier.md", all_rows)
        write_draw_forensics_global(out_dir, global_draw_db)
        ranked = sorted(all_rows, key=lambda r: float(r["interestingness"]), reverse=True)
        write_optimizer_report(
            out_dir / "optimizer_report.md",
            ranked_rows=ranked,
            frontier_rows=frontier_rows,
            timed_out_variants=timed_out_variants,
            additional_ideas=cfg["additional_ideas"],  # type: ignore[arg-type]
        )
        write_metric_glossary(out_dir / "metric_glossary.md")

    interrupted = False
    try:
        stage1_games_total = (
            len(stage1_variants)
            * stage1_budget.seeds_per_variant
            * stage1_budget.games_per_seed
        )
        stage1_games_done = 0
        append_jsonl(progress_log_path, {"ts": now_iso(), "event": "stage_start", "stage": "stage1"})
        for idx, variant in enumerate(stage1_variants, start=1):
            print(f"[stage1 {idx}/{len(stage1_variants)}] {variant.variant_id}")
            games, metrics, draw_signatures, timed_out = run_variant(
                variant=variant,
                out_dir=out_dir,
                seeds_per_variant=stage1_budget.seeds_per_variant,
                games_per_seed=stage1_budget.games_per_seed,
                max_plies=int(cfg["max_plies"]),
                workers=int(cfg["workers"]),
                snapshot_plies=snapshot_plies,
                base_seed=int(cfg["base_seed"]),
                progress_every_games=int(cfg["progress_flush_every_games"]),
                variant_timebox_minutes=variant_timebox,
                baseline_piece_distribution=baseline_piece_distribution,
                progress_log_path=progress_log_path,
                run_state_path=run_state_path,
                stage_name="stage1",
                stage_variant_index=idx,
                stage_variant_total=len(stage1_variants),
                stage_games_completed_before=stage1_games_done,
                stage_games_total=stage1_games_total,
            )
            stage1_games_done += int(metrics["games_completed"])
            if timed_out:
                timed_out_variants.append(variant.variant_id)

            if variant.variant_id == "anchor_normal_reference":
                baseline_piece_distribution = metrics.get("piece_type_distribution", {})  # type: ignore[assignment]

            if baseline_piece_distribution:
                metrics = summarize_games(games, baseline_piece_distribution)
                metrics["timed_out"] = timed_out
                metrics["games_completed"] = len(games)
            integrate_variant_result(variant, metrics, draw_signatures)
            append_jsonl(
                progress_log_path,
                {
                    "ts": now_iso(),
                    "event": "variant_complete",
                    "variant_id": variant.variant_id,
                    "stage": "stage1",
                    "draw_rate": round(float(metrics["draw_rate"]), 4),
                    "mean_plies": round(float(metrics["mean_plies"]), 2),
                    "interestingness": round(float(metrics["interestingness"]), 4),
                },
            )

        elites = select_elites_for_stage2(all_rows, elite_count=int(stage2_cfg["elite_count"]))
        stage2_variants: List[VariantSpec] = []
        seen_stage2 = set()
        search_space = cfg["search_space"]  # type: ignore[index]
        for elite_idx, elite in enumerate(elites, start=1):
            parent_id = str(elite["variant_id"])
            parent_spec = all_specs[parent_id]

            ref_id = f"s2_ref_{elite_idx:02d}_{rules_signature(parent_spec.rules)[:8]}"
            if ref_id not in seen_stage2:
                stage2_variants.append(
                    VariantSpec(
                        variant_id=ref_id,
                        stage="stage2",
                        rules=parent_spec.rules,
                        levers=dataclasses.asdict(parent_spec.rules),
                        parent_id=parent_id,
                        notes="elite_ref",
                    )
                )
                seen_stage2.add(ref_id)

            for mut_idx in range(int(stage2_cfg["mutations_per_elite"])):
                mut_seed = int(cfg["base_seed"]) ^ (elite_idx * 97_531) ^ (mut_idx * 3_131)
                mut_rng = random.Random(mut_seed)
                mutated = mutate_levers(
                    base_levers=dataclasses.asdict(parent_spec.rules),
                    search_space=search_space,  # type: ignore[arg-type]
                    rng=mut_rng,
                    edits=mut_rng.randint(1, 3),
                )
                mut_rules = dict_to_rules(mutated)
                mut_sig = rules_signature(mut_rules)[:8]
                vid = f"s2_mut_{elite_idx:02d}_{mut_idx+1:02d}_{mut_sig}"
                if vid in seen_stage2:
                    continue
                stage2_variants.append(
                    VariantSpec(
                        variant_id=vid,
                        stage="stage2",
                        rules=mut_rules,
                        levers=mutated,
                        parent_id=parent_id,
                        notes="local_mutation",
                    )
                )
                seen_stage2.add(vid)

        stage2_games_total = (
            len(stage2_variants)
            * stage2_budget.seeds_per_variant
            * stage2_budget.games_per_seed
        )
        stage2_games_done = 0
        append_jsonl(progress_log_path, {"ts": now_iso(), "event": "stage_start", "stage": "stage2"})
        for idx, variant in enumerate(stage2_variants, start=1):
            print(f"[stage2 {idx}/{len(stage2_variants)}] {variant.variant_id} parent={variant.parent_id}")
            games, metrics, draw_signatures, timed_out = run_variant(
                variant=variant,
                out_dir=out_dir,
                seeds_per_variant=stage2_budget.seeds_per_variant,
                games_per_seed=stage2_budget.games_per_seed,
                max_plies=int(cfg["max_plies"]),
                workers=int(cfg["workers"]),
                snapshot_plies=snapshot_plies,
                base_seed=int(cfg["base_seed"]),
                progress_every_games=int(cfg["progress_flush_every_games"]),
                variant_timebox_minutes=variant_timebox,
                baseline_piece_distribution=baseline_piece_distribution,
                progress_log_path=progress_log_path,
                run_state_path=run_state_path,
                stage_name="stage2",
                stage_variant_index=idx,
                stage_variant_total=len(stage2_variants),
                stage_games_completed_before=stage2_games_done,
                stage_games_total=stage2_games_total,
            )
            stage2_games_done += int(metrics["games_completed"])
            if timed_out:
                timed_out_variants.append(variant.variant_id)
            integrate_variant_result(variant, metrics, draw_signatures)
            append_jsonl(
                progress_log_path,
                {
                    "ts": now_iso(),
                    "event": "variant_complete",
                    "variant_id": variant.variant_id,
                    "stage": "stage2",
                    "draw_rate": round(float(metrics["draw_rate"]), 4),
                    "mean_plies": round(float(metrics["mean_plies"]), 2),
                    "interestingness": round(float(metrics["interestingness"]), 4),
                },
            )

    except KeyboardInterrupt:
        interrupted = True
        append_jsonl(progress_log_path, {"ts": now_iso(), "event": "keyboard_interrupt"})
        print("Interrupted; preserving partial outputs.")
    finally:
        ranked = sorted(all_rows, key=lambda r: float(r["interestingness"]), reverse=True)
        frontier_rows = write_pareto(out_dir / "pareto_frontier.md", all_rows) if all_rows else []
        write_optimizer_report(
            out_dir / "optimizer_report.md",
            ranked_rows=ranked,
            frontier_rows=frontier_rows,
            timed_out_variants=timed_out_variants,
            additional_ideas=cfg["additional_ideas"],  # type: ignore[arg-type]
        )
        write_metric_glossary(out_dir / "metric_glossary.md")
        write_draw_forensics_global(out_dir, global_draw_db)
        if bool(cfg.get("write_raw_games", True)):
            build_raw_games_csv(out_dir)
        atomic_write_json(
            run_state_path,
            {
                "updated_at": now_iso(),
                "status": "interrupted" if interrupted else "completed",
                "variants_completed": len(all_rows),
                "timed_out_variants": timed_out_variants,
            },
        )
        append_jsonl(
            progress_log_path,
            {
                "ts": now_iso(),
                "event": "run_end",
                "status": "interrupted" if interrupted else "completed",
                "variants_completed": len(all_rows),
            },
        )

    print(f"[done] status={'interrupted' if interrupted else 'completed'} variants={len(all_rows)}")
    print(f"[done] outputs: {out_dir}")


if __name__ == "__main__":
    # Keep CTRL+C responsive inside multiprocessing workloads.
    signal.signal(signal.SIGINT, signal.default_int_handler)
    main()
