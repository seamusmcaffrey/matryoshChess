#!/usr/bin/env python3
"""Phase 2 study runner: upgraded engine + locked structural rules.

Questions addressed:
1) Does degradation stay valuable with a thinking engine?
2) Does retaliation matter once tactical foresight exists?
3) Which retaliation settings work best under deeper search?
4) Is doom clock still needed?
5) Do playstyles produce different outcomes?
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import json
import multiprocessing as mp
import os
import statistics
import time
from collections import Counter, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from engine import (
    EngineProfile,
    PROFILE_AGGRESSIVE,
    PROFILE_BALANCED,
    PROFILE_DEFENSIVE,
    choose_move_v2_with_info,
    should_resign,
)
from locked_rules import build_locked_config
from simulate_variant_study import (
    BLACK,
    DRAW,
    WHITE,
    GameState,
    RuleConfig,
    aggregate_results,
    sq_to_coord,
)


@dataclass(frozen=True)
class StudyConfig:
    name: str
    description: str
    rules: RuleConfig
    games: int
    white_profile: EngineProfile = PROFILE_BALANCED
    black_profile: EngineProfile = PROFILE_BALANCED


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def append_jsonl(path: Path, payload: Dict[str, object]) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, sort_keys=True))
        f.write("\n")


def atomic_write_json(path: Path, payload: Dict[str, object]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, sort_keys=True)
    tmp.replace(path)


def progress_bar(completed: int, total: int, width: int = 24) -> str:
    if total <= 0:
        return "[" + ("-" * width) + "]"
    frac = max(0.0, min(1.0, completed / total))
    fill = int(round(frac * width))
    return "[" + ("#" * fill) + ("-" * (width - fill)) + "]"


def mean(values: Sequence[float]) -> float:
    return float(statistics.mean(values)) if values else 0.0


def _normal_reference_rules() -> RuleConfig:
    """Chess-like baseline for engine sanity checks."""
    return RuleConfig(
        ruleset="normal",
        retaliation_enabled=False,
        king_move_mode="normal",
        king_capture_insta_kill="off",
        win_condition="checkmate_only",
        quiet_halfmove_limit=100,
        stalemate_is_loss=False,
        ko_repetition_illegal=False,
        doom_clock_full_moves=0,
    )


def _scaled_games(base_games: int, scale: float, min_games: int = 12) -> int:
    return max(min_games, int(round(base_games * scale)))


def build_catalog(sample_scale: float) -> Dict[str, StudyConfig]:
    return {
        "normal_chess_v2": StudyConfig(
            name="normal_chess_v2",
            description="Normal chess with v2 engine (validation baseline)",
            rules=_normal_reference_rules(),
            games=_scaled_games(200, sample_scale),
        ),
        "matryoshka_no_ret": StudyConfig(
            name="matryoshka_no_ret",
            description="Locked matryoshka structure, retaliation disabled",
            rules=build_locked_config(retaliation_enabled=False),
            games=_scaled_games(400, sample_scale),
        ),
        "matryoshka_ret_baseline": StudyConfig(
            name="matryoshka_ret_baseline",
            description="Locked matryoshka structure, baseline retaliation",
            rules=build_locked_config(retaliation_enabled=True),
            games=_scaled_games(400, sample_scale),
        ),
        "ret_close_fast": StudyConfig(
            name="ret_close_fast",
            description="Retaliation radius=2, strike_window=1, perma_kill",
            rules=build_locked_config(
                retaliation_enabled=True,
                retaliation_targeting="localized_safe",
                retaliation_local_radius=2,
                retaliation_strike_window=1,
                strike_effect="perma_kill",
            ),
            games=_scaled_games(400, sample_scale),
        ),
        "ret_wide_long": StudyConfig(
            name="ret_wide_long",
            description="Retaliation radius=5, strike_window=3, perma_kill",
            rules=build_locked_config(
                retaliation_enabled=True,
                retaliation_local_radius=5,
                retaliation_strike_window=3,
                strike_effect="perma_kill",
            ),
            games=_scaled_games(400, sample_scale),
        ),
        "ret_aggressive_targeting": StudyConfig(
            name="ret_aggressive_targeting",
            description="Retaliation top2_pool_safe + max_threat tiebreak",
            rules=build_locked_config(
                retaliation_enabled=True,
                retaliation_targeting="top2_pool_safe",
                retaliation_tiebreak="max_threat",
                retaliation_strike_window=2,
                strike_effect="perma_kill",
            ),
            games=_scaled_games(400, sample_scale),
        ),
        "ret_king_dash": StudyConfig(
            name="ret_king_dash",
            description="Retaliation baseline with king_dash mode",
            rules=build_locked_config(
                retaliation_enabled=True,
                king_move_mode="king_dash",
                king_dash_max=2,
            ),
            games=_scaled_games(400, sample_scale),
        ),
        "ret_plus_doom": StudyConfig(
            name="ret_plus_doom",
            description="Retaliation baseline with doom clock full-moves=32",
            rules=build_locked_config(
                retaliation_enabled=True,
                doom_clock_full_moves=32,
                doom_clock_effect="bonus_capture_damage",
            ),
            games=_scaled_games(400, sample_scale),
        ),
    }


def _retaliation_threat_available(game: GameState, color: str) -> bool:
    for piece in game.pieces.values():
        if piece.color != color:
            continue
        if piece.retaliation_window <= 0 or piece.retaliation_target is None:
            continue
        if piece.retaliation_target in game.pieces:
            return True
    return False


def _tier_distribution_final(game: GameState) -> Dict[str, int]:
    out = {"tier1": 0, "tier2": 0, "tier3": 0}
    for piece in game.pieces.values():
        if piece.kind in ("Q", "R", "B"):
            key = f"tier{piece.tier}"
            if key in out:
                out[key] += 1
            else:
                out["tier3"] += 1
        else:
            out["tier1"] += 1
    return out


def _build_draw_forensics(
    game: GameState,
    compact_log: deque[Dict[str, object]],
) -> Dict[str, object]:
    last_moves = list(compact_log)[-20:]
    last_ten = [row for row in compact_log if int(row["ply"]) > max(0, game.ply - 10)]
    check_last_10 = {
        "white_in_check": any(row["check_target"] == WHITE for row in last_ten),
        "black_in_check": any(row["check_target"] == BLACK for row in last_ten),
        "check_events": sum(1 for row in last_ten if row["check_target"] is not None),
    }
    return {
        "termination_reason": game.termination_reason,
        "piece_signature": game.piece_signature_counts(),
        "piece_type_counts": game.piece_type_counts(),
        "checks_last_10_plies": check_last_10,
        "last_20_moves": [str(row["coord"]) for row in last_moves],
        "final_board_hash": game._position_hash(),
        "final_board_ascii": game.board_ascii(),
    }


def run_game_v2(
    seed: int,
    rules: RuleConfig,
    max_plies: int = 300,
    white_profile: EngineProfile = PROFILE_BALANCED,
    black_profile: EngineProfile = PROFILE_BALANCED,
    start_side: str = WHITE,
    snapshot_plies: Sequence[int] = (40, 80, 120, 160),
) -> Dict[str, object]:
    game = GameState(seed=seed, rules=rules, start_side=start_side)

    compact_log: deque[Dict[str, object]] = deque(maxlen=32)
    snapshot_targets = sorted({int(v) for v in snapshot_plies if int(v) > 0})
    snapshots: Dict[str, Dict[str, object]] = {}

    def maybe_take_snapshot() -> None:
        if game.ply in snapshot_targets and str(game.ply) not in snapshots:
            snapshots[str(game.ply)] = {
                "piece_count": len(game.pieces),
                "piece_type_counts": game.piece_type_counts(),
            }

    collapsed_pawns_created = 0
    retaliation_threat_moves_available = 0
    resignation_count = 0
    search_depths: List[float] = []
    search_nodes: List[float] = []

    while not game.terminated and game.ply < max_plies:
        side = game.side_to_move
        profile = white_profile if side == WHITE else black_profile

        if _retaliation_threat_available(game, side):
            retaliation_threat_moves_available += 1

        if should_resign(game, side, game.ply, profile=profile):
            game.terminated = True
            game.winner = game._opponent(side)
            game.termination_reason = "resignation"
            resignation_count = 1
            break

        legal = game.legal_moves(side)

        if side == WHITE:
            game.stats["mean_legal_moves_white"].append(len(legal))
        else:
            game.stats["mean_legal_moves_black"].append(len(legal))

        if not legal:
            if game.is_in_check(side):
                game.winner = game._opponent(side)
                game.termination_reason = "checkmate"
            else:
                if rules.stalemate_is_loss:
                    game.winner = game._opponent(side)
                    game.termination_reason = "stalemate_loss"
                else:
                    game.winner = DRAW
                    game.termination_reason = "stalemate"
            game.terminated = True
            break

        before_kinds = {pid: (piece.kind, piece.crippled) for pid, piece in game.pieces.items()}

        result = choose_move_v2_with_info(
            game,
            legal,
            target_depth=profile.search_depth,
            max_nodes=profile.max_nodes,
            noise=profile.noise,
            profile=profile,
        )
        move = result.move
        search_depths.append(float(result.info.depth_reached))
        search_nodes.append(float(result.info.nodes))

        mover_id = game.board[move.from_sq]
        mover_side = game.side_to_move

        event = game.apply_move(move)

        for pid, (old_kind, _old_crippled) in before_kinds.items():
            piece = game.pieces.get(pid)
            if piece is None:
                continue
            if old_kind != "P" and piece.kind == "P":
                collapsed_pawns_created += 1

        move_coord = f"{sq_to_coord(move.from_sq)}{sq_to_coord(move.to_sq)}"
        compact_log.append(
            {
                "ply": game.ply,
                "side": mover_side,
                "coord": move_coord,
                "capture": event.capture_happened,
                "captured_kind": event.captured_piece_kind,
                "capture_permanent": event.capture_was_permanent,
                "check_target": game.side_to_move if event.check_given else None,
            }
        )

        maybe_take_snapshot()

        if rules.quiet_halfmove_limit > 0 and game.quiet_halfmoves >= rules.quiet_halfmove_limit:
            game.terminated = True
            game.winner = DRAW
            game.termination_reason = "quiet_limit"

    if not game.terminated:
        game.terminated = True
        game.winner = DRAW
        game.termination_reason = "max_plies"

    for target in snapshot_targets:
        key = str(target)
        if key not in snapshots:
            snapshots[key] = {
                "piece_count": len(game.pieces),
                "piece_type_counts": game.piece_type_counts(),
            }

    draw_forensics: Optional[Dict[str, object]] = None
    if game.winner == DRAW:
        draw_forensics = _build_draw_forensics(game, compact_log)

    white_legal = game.stats["mean_legal_moves_white"]
    black_legal = game.stats["mean_legal_moves_black"]

    return {
        "seed": seed,
        "start_side": start_side,
        "winner": game.winner,
        "termination": game.termination_reason,
        "plies": game.ply,
        "captures_total": game.stats["captures_total"],
        "retaliation_redeployments": game.stats["retaliation_redeployments"],
        "retaliation_safe_target_placements": game.stats["retaliation_safe_target_placements"],
        "retaliation_circe_placements": game.stats["retaliation_circe_placements"],
        "retaliation_random_placements": game.stats["retaliation_random_placements"],
        "circe_captured_rebirths": game.stats["circe_captured_rebirths"],
        "anticirce_attacker_rebirths": game.stats["anticirce_attacker_rebirths"],
        "permanent_removals_total": game.stats["permanent_removals_total"],
        "permanent_removals_king_capture": game.stats["permanent_removals_king_capture"],
        "permanent_removals_retaliation_strike": game.stats["permanent_removals_retaliation_strike"],
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
        "mean_legal_moves_white": round(mean([float(v) for v in white_legal]), 3),
        "mean_legal_moves_black": round(mean([float(v) for v in black_legal]), 3),
        "tier_distribution_final": _tier_distribution_final(game),
        "collapsed_pawns_created": collapsed_pawns_created,
        "retaliation_threat_moves_available": retaliation_threat_moves_available,
        "resignation_count": resignation_count,
        "mean_search_depth_achieved": round(mean(search_depths), 3),
        "mean_search_nodes": round(mean(search_nodes), 3),
        "white_profile": white_profile.name,
        "black_profile": black_profile.name,
    }


def _interestingness_v2(summary: Dict[str, object]) -> float:
    draw_rate = float(summary.get("draw_rate", 1.0))
    mean_plies = float(summary.get("mean_plies", 999.0))
    volatility = float(summary.get("mean_material_volatility", 0.0))
    redeploy = float(summary.get("mean_redeployments", 0.0))
    strike_success = float(summary.get("retaliation_target_capture_success_rate", 0.0))

    winner_counts = summary.get("winner_counts", {})
    if isinstance(winner_counts, dict):
        total = max(1.0, float(summary.get("num_games", 0)))
        white_rate = float(winner_counts.get(WHITE, 0)) / total
        black_rate = float(winner_counts.get(BLACK, 0)) / total
    else:
        white_rate = 0.5
        black_rate = 0.5
    imbalance = abs(white_rate - black_rate)

    decisiveness = max(0.0, 1.0 - draw_rate)
    pace = max(0.0, min(1.0, (220.0 - mean_plies) / 140.0))
    volatility_score = max(0.0, min(1.0, volatility / 50.0))
    retaliation_activity = max(0.0, min(1.0, (redeploy * max(0.1, strike_success)) / 1.4))
    fairness = 1.0 - max(0.0, min(1.0, imbalance / 0.12))

    return (
        (0.28 * decisiveness)
        + (0.24 * pace)
        + (0.19 * volatility_score)
        + (0.19 * retaliation_activity)
        + (0.10 * fairness)
    )


def summarize_phase2_games(games: Sequence[Dict[str, object]], elapsed_sec: float) -> Dict[str, object]:
    base = aggregate_results(games)

    collapsed = [float(g.get("collapsed_pawns_created", 0)) for g in games]
    threats = [float(g.get("retaliation_threat_moves_available", 0)) for g in games]
    resignations = [float(g.get("resignation_count", 0)) for g in games]
    search_depth = [float(g.get("mean_search_depth_achieved", 0.0)) for g in games]
    search_nodes = [float(g.get("mean_search_nodes", 0.0)) for g in games]

    tier1 = [float(g.get("tier_distribution_final", {}).get("tier1", 0)) for g in games]
    tier2 = [float(g.get("tier_distribution_final", {}).get("tier2", 0)) for g in games]
    tier3 = [float(g.get("tier_distribution_final", {}).get("tier3", 0)) for g in games]

    base["mean_collapsed_pawns_created"] = round(mean(collapsed), 3)
    base["mean_retaliation_threat_moves_available"] = round(mean(threats), 3)
    base["resignation_rate"] = round(sum(resignations) / max(1, len(games)), 4)
    base["mean_search_depth_achieved"] = round(mean(search_depth), 3)
    base["mean_search_nodes"] = round(mean(search_nodes), 1)
    base["mean_tier1_pieces_final"] = round(mean(tier1), 3)
    base["mean_tier2_pieces_final"] = round(mean(tier2), 3)
    base["mean_tier3_pieces_final"] = round(mean(tier3), 3)

    base["elapsed_sec"] = round(elapsed_sec, 2)
    base["games_per_minute"] = round((len(games) / elapsed_sec) * 60.0, 3) if elapsed_sec > 0 else 0.0
    base["interestingness_v2"] = round(_interestingness_v2(base), 4)

    return base


def write_config_outputs(
    out_dir: Path,
    config: StudyConfig,
    games: Sequence[Dict[str, object]],
    summary: Dict[str, object],
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    with (out_dir / "games.jsonl").open("w", encoding="utf-8") as f:
        for row in games:
            f.write(json.dumps(row, sort_keys=True))
            f.write("\n")

    with (out_dir / "summary.json").open("w", encoding="utf-8") as f:
        json.dump(
            {
                "config": {
                    "name": config.name,
                    "description": config.description,
                    "games": config.games,
                    "rules": dataclasses.asdict(config.rules),
                    "white_profile": dataclasses.asdict(config.white_profile),
                    "black_profile": dataclasses.asdict(config.black_profile),
                },
                "summary": summary,
                "updated_at": now_iso(),
            },
            f,
            indent=2,
            sort_keys=True,
        )

    if config.rules.retaliation_enabled:
        lines = [
            f"# Retaliation Analysis - {config.name}",
            "",
            f"- Mean redeployments/game: {summary['mean_redeployments']}",
            f"- Safe redeploy share: {summary['safe_redeploy_share']}",
            f"- Circe redeploy share: {summary['circe_redeploy_share']}",
            f"- Random redeploy share: {summary['random_redeploy_share']}",
            f"- Strike attempt rate (per capture): {summary['retaliation_target_capture_attempt_rate']}",
            f"- Strike success rate: {summary['retaliation_target_capture_success_rate']}",
            f"- Mean retaliation-threat moves available: {summary['mean_retaliation_threat_moves_available']}",
            f"- Mean permanent removals via strike: {summary['mean_permanent_by_strike']}",
            "",
        ]
        (out_dir / "retaliation_analysis.md").write_text("\n".join(lines), encoding="utf-8")


def _run_game_task(
    args: Tuple[
        int,
        RuleConfig,
        int,
        EngineProfile,
        EngineProfile,
        str,
        Tuple[int, ...],
    ]
) -> Dict[str, object]:
    seed, rules, max_plies, white_profile, black_profile, start_side, snapshot_plies = args
    return run_game_v2(
        seed=seed,
        rules=rules,
        max_plies=max_plies,
        white_profile=white_profile,
        black_profile=black_profile,
        start_side=start_side,
        snapshot_plies=snapshot_plies,
    )


def run_study(
    config: StudyConfig,
    out_root: Path,
    workers: int,
    base_seed: int,
    max_plies: int,
    snapshot_plies: Sequence[int],
    progress_log_path: Path,
    run_state_path: Path,
) -> Tuple[List[Dict[str, object]], Dict[str, object], float]:
    cfg_dir = out_root / f"config_{config.name}"
    cfg_dir.mkdir(parents=True, exist_ok=True)

    tasks = []
    for i in range(config.games):
        seed = base_seed + (i * 7919)
        start_side = WHITE if (i % 2 == 0) else BLACK
        tasks.append(
            (
                seed,
                config.rules,
                max_plies,
                config.white_profile,
                config.black_profile,
                start_side,
                tuple(int(v) for v in snapshot_plies),
            )
        )

    start_ts = time.time()
    games: List[Dict[str, object]] = []
    progress_every = max(1, min(20, max(1, len(tasks) // 10)))

    if workers <= 1:
        for i, task in enumerate(tasks, start=1):
            games.append(_run_game_task(task))
            if (i % progress_every) == 0 or i == len(tasks):
                elapsed = time.time() - start_ts
                print(
                    f"[progress] {config.name} {progress_bar(i, len(tasks))} "
                    f"{(100.0 * i / max(1, len(tasks))):5.1f}% ({i}/{len(tasks)}) "
                    f"elapsed={elapsed:0.1f}s"
                )
                append_jsonl(
                    progress_log_path,
                    {
                        "ts": now_iso(),
                        "event": "config_progress",
                        "config": config.name,
                        "completed_games": i,
                        "total_games": len(tasks),
                        "elapsed_sec": round(elapsed, 2),
                    },
                )
                atomic_write_json(
                    run_state_path,
                    {
                        "updated_at": now_iso(),
                        "active_config": config.name,
                        "completed_games": i,
                        "total_games": len(tasks),
                        "elapsed_sec": round(elapsed, 2),
                    },
                )
    else:
        with mp.Pool(processes=max(1, workers)) as pool:
            for i, game in enumerate(pool.imap_unordered(_run_game_task, tasks, chunksize=4), start=1):
                games.append(game)
                if (i % progress_every) == 0 or i == len(tasks):
                    elapsed = time.time() - start_ts
                    print(
                        f"[progress] {config.name} {progress_bar(i, len(tasks))} "
                        f"{(100.0 * i / max(1, len(tasks))):5.1f}% ({i}/{len(tasks)}) "
                        f"elapsed={elapsed:0.1f}s"
                    )
                    append_jsonl(
                        progress_log_path,
                        {
                            "ts": now_iso(),
                            "event": "config_progress",
                            "config": config.name,
                            "completed_games": i,
                            "total_games": len(tasks),
                            "elapsed_sec": round(elapsed, 2),
                        },
                    )
                    atomic_write_json(
                        run_state_path,
                        {
                            "updated_at": now_iso(),
                            "active_config": config.name,
                            "completed_games": i,
                            "total_games": len(tasks),
                            "elapsed_sec": round(elapsed, 2),
                        },
                    )

    games.sort(key=lambda g: int(g["seed"]))
    elapsed_sec = time.time() - start_ts
    summary = summarize_phase2_games(games, elapsed_sec)
    write_config_outputs(cfg_dir, config, games, summary)
    return games, summary, elapsed_sec


def _delta(a: Dict[str, object], b: Dict[str, object], key: str) -> float:
    return float(a.get(key, 0.0)) - float(b.get(key, 0.0))


def _retaliation_is_positive(results: Dict[str, Dict[str, object]]) -> bool:
    no_ret = results.get("matryoshka_no_ret", {}).get("summary")
    ret = results.get("matryoshka_ret_baseline", {}).get("summary")
    if not isinstance(no_ret, dict) or not isinstance(ret, dict):
        return False

    draw_delta = float(no_ret.get("draw_rate", 0.0)) - float(ret.get("draw_rate", 0.0))
    swing_delta = float(ret.get("mean_material_volatility", 0.0)) - float(
        no_ret.get("mean_material_volatility", 0.0)
    )
    threat_delta = float(ret.get("mean_retaliation_threat_moves_available", 0.0)) - float(
        no_ret.get("mean_retaliation_threat_moves_available", 0.0)
    )
    strike_success = float(ret.get("retaliation_target_capture_success_rate", 0.0))

    return (draw_delta >= 0.05) or (swing_delta >= 4.0) or (
        threat_delta >= 2.0 and strike_success >= 0.20
    )


def _best_retaliation_config(
    configs: Dict[str, StudyConfig],
    results: Dict[str, Dict[str, object]],
) -> Optional[StudyConfig]:
    candidates: List[Tuple[float, str]] = []
    for name, blob in results.items():
        if not (name.startswith("ret_") or name == "matryoshka_ret_baseline"):
            continue
        summary = blob.get("summary", {})
        if not isinstance(summary, dict):
            continue
        candidates.append((float(summary.get("interestingness_v2", 0.0)), name))

    if not candidates:
        return None

    candidates.sort(reverse=True)
    best_name = candidates[0][1]
    return configs.get(best_name)


def write_study_report(
    out_path: Path,
    ordered_configs: Sequence[str],
    configs: Dict[str, StudyConfig],
    results: Dict[str, Dict[str, object]],
    baseline_ok: bool,
    baseline_gate_threshold: float,
    retaliation_positive: bool,
) -> None:
    ranked = sorted(
        (
            (
                name,
                float(results[name]["summary"].get("interestingness_v2", 0.0)),
            )
            for name in ordered_configs
            if name in results and isinstance(results[name].get("summary"), dict)
        ),
        key=lambda x: x[1],
        reverse=True,
    )

    lines: List[str] = []
    lines.append("# Phase 2 Study Report")
    lines.append("")
    lines.append(f"Generated: {now_iso()}")
    lines.append("")

    lines.append("## Baseline Validation")
    if "normal_chess_v2" in results:
        normal = results["normal_chess_v2"]["summary"]
        draw_rate = float(normal["draw_rate"])
        lines.append(
            f"- Normal chess draw rate: {100.0 * draw_rate:.2f}% "
            f"(gate <= {100.0 * baseline_gate_threshold:.1f}%)"
        )
        lines.append(
            f"- Gate status: {'PASS' if baseline_ok else 'FAIL'}"
        )
    else:
        lines.append("- Normal baseline not executed.")
    lines.append("")

    lines.append("## Configuration Table")
    lines.append("")
    lines.append(
        "| Config | Games | Draw% | Mean plies | Captures/100 | Volatility | Mean depth | GPM | Interestingness |"
    )
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for name, _score in ranked:
        summary = results[name]["summary"]
        lines.append(
            f"| {name} | {int(summary['num_games'])} | {100.0 * float(summary['draw_rate']):.2f} | "
            f"{float(summary['mean_plies']):.2f} | {float(summary['captures_per_100_plies']):.2f} | "
            f"{float(summary['mean_material_volatility']):.2f} | {float(summary['mean_search_depth_achieved']):.2f} | "
            f"{float(summary['games_per_minute']):.2f} | {float(summary['interestingness_v2']):.3f} |"
        )
    lines.append("")

    lines.append("## Q1: Degradation Impact")
    if "normal_chess_v2" in results and "matryoshka_no_ret" in results:
        normal = results["normal_chess_v2"]["summary"]
        no_ret = results["matryoshka_no_ret"]["summary"]
        lines.append(
            f"- Draw delta (mat_no_ret - normal): {100.0 * _delta(no_ret, normal, 'draw_rate'):+.2f} pp"
        )
        lines.append(
            f"- Mean plies delta: {_delta(no_ret, normal, 'mean_plies'):+.2f}"
        )
        lines.append(
            f"- Material volatility delta: {_delta(no_ret, normal, 'mean_material_volatility'):+.2f}"
        )
    else:
        lines.append("- Insufficient data.")
    lines.append("")

    lines.append("## Q2: Retaliation Impact")
    if "matryoshka_no_ret" in results and "matryoshka_ret_baseline" in results:
        no_ret = results["matryoshka_no_ret"]["summary"]
        ret = results["matryoshka_ret_baseline"]["summary"]
        lines.append(
            f"- Draw delta (ret - no_ret): {100.0 * _delta(ret, no_ret, 'draw_rate'):+.2f} pp"
        )
        lines.append(
            f"- Mean redeployments delta: {_delta(ret, no_ret, 'mean_redeployments'):+.3f}"
        )
        lines.append(
            f"- Strike success delta: {_delta(ret, no_ret, 'retaliation_target_capture_success_rate'):+.4f}"
        )
        lines.append(
            f"- Retaliation-positive gate: {'PASS' if retaliation_positive else 'FAIL'}"
        )
    else:
        lines.append("- Insufficient data.")
    lines.append("")

    ret_rows = [name for name, _ in ranked if name.startswith("ret_") or name == "matryoshka_ret_baseline"]
    lines.append("## Q3/Q4 Retaliation and Doom Ranking")
    if ret_rows:
        for idx, name in enumerate(ret_rows, start=1):
            summary = results[name]["summary"]
            lines.append(
                f"{idx}. `{name}` score={float(summary['interestingness_v2']):.3f}, "
                f"draw={100.0 * float(summary['draw_rate']):.2f}%, "
                f"depth={float(summary['mean_search_depth_achieved']):.2f}"
            )
    else:
        lines.append("- No retaliation variants executed.")
    lines.append("")

    style_rows = [name for name, _ in ranked if name.startswith("style_")]
    lines.append("## Q5 Playstyle Diversity")
    if style_rows:
        for name in style_rows:
            summary = results[name]["summary"]
            cfg = configs[name]
            lines.append(
                f"- {name}: {cfg.white_profile.name} vs {cfg.black_profile.name}, "
                f"draw={100.0 * float(summary['draw_rate']):.2f}%, "
                f"white_win={100.0 * (float(summary['winner_counts'].get(WHITE, 0)) / max(1, float(summary['num_games']))):.2f}%"
            )
    else:
        lines.append("- Not executed.")

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Phase 2 matryoshka study with v2 engine")
    parser.add_argument("--out", type=str, default="", help="Output directory (default: outputs_phase2_<timestamp>)")
    parser.add_argument("--workers", type=int, default=int(os.environ.get("WORKERS", "8")))
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--max-plies", type=int, default=300)
    parser.add_argument("--snapshot-plies", type=int, nargs="*", default=[40, 80, 120, 160])
    parser.add_argument("--sample-scale", type=float, default=float(os.environ.get("SAMPLE_SCALE", "0.25")))
    parser.add_argument("--baseline-draw-gate", type=float, default=0.85)
    parser.add_argument("--allow-high-draw", action="store_true")

    parser.add_argument("--target-depth", type=int, default=int(os.environ.get("TARGET_DEPTH", "3")))
    parser.add_argument("--max-nodes", type=int, default=int(os.environ.get("MAX_NODES", "80000")))
    parser.add_argument("--noise", type=float, default=float(os.environ.get("SEARCH_NOISE", "0.02")))

    parser.add_argument(
        "--configs",
        type=str,
        default="",
        help="Comma-separated config names. If set, only these run in this order.",
    )
    parser.add_argument("--skip-extended", action="store_true")
    parser.add_argument("--force-extended", action="store_true")
    parser.add_argument("--smoke", action="store_true", help="Fast sanity run")
    return parser.parse_args()


def apply_profile_overrides(profile: EngineProfile, args: argparse.Namespace) -> EngineProfile:
    return dataclasses.replace(
        profile,
        search_depth=max(1, int(args.target_depth)),
        max_nodes=max(10_000, int(args.max_nodes)),
        noise=max(0.0, float(args.noise)),
    )


def main() -> None:
    args = parse_args()

    if args.smoke:
        args.sample_scale = min(args.sample_scale, 0.10)
        args.target_depth = min(args.target_depth, 2)
        args.max_nodes = min(args.max_nodes, 30_000)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_root = Path(args.out) if args.out else Path(f"outputs_phase2_{stamp}")
    out_root.mkdir(parents=True, exist_ok=True)

    progress_log_path = out_root / "progress.jsonl"
    run_state_path = out_root / "run_state.json"
    append_jsonl(
        progress_log_path,
        {
            "ts": now_iso(),
            "event": "run_start",
            "workers": args.workers,
            "seed": args.seed,
            "max_plies": args.max_plies,
            "sample_scale": args.sample_scale,
            "target_depth": args.target_depth,
            "max_nodes": args.max_nodes,
            "noise": args.noise,
            "smoke": bool(args.smoke),
        },
    )

    catalog = build_catalog(sample_scale=args.sample_scale)

    # Apply engine profile overrides globally.
    for name, cfg in list(catalog.items()):
        catalog[name] = StudyConfig(
            name=cfg.name,
            description=cfg.description,
            rules=cfg.rules,
            games=cfg.games,
            white_profile=apply_profile_overrides(cfg.white_profile, args),
            black_profile=apply_profile_overrides(cfg.black_profile, args),
        )

    core_order = ["normal_chess_v2", "matryoshka_no_ret", "matryoshka_ret_baseline"]
    extended_order = [
        "ret_close_fast",
        "ret_wide_long",
        "ret_aggressive_targeting",
        "ret_king_dash",
        "ret_plus_doom",
    ]

    if args.configs:
        run_order = [name.strip() for name in args.configs.split(",") if name.strip()]
    else:
        run_order = list(core_order)

    results: Dict[str, Dict[str, object]] = {}
    executed_configs: List[str] = []

    # Run explicit order or core first.
    for idx, name in enumerate(run_order, start=1):
        if name not in catalog:
            raise ValueError(f"Unknown config name: {name}")
        cfg = catalog[name]
        print(
            f"[run] ({idx}/{len(run_order)}) {name} games={cfg.games} workers={args.workers} "
            f"depth={cfg.white_profile.search_depth} max_nodes={cfg.white_profile.max_nodes}"
        )
        games, summary, elapsed = run_study(
            config=cfg,
            out_root=out_root,
            workers=args.workers,
            base_seed=args.seed + (idx * 100_000),
            max_plies=args.max_plies,
            snapshot_plies=args.snapshot_plies,
            progress_log_path=progress_log_path,
            run_state_path=run_state_path,
        )
        results[name] = {
            "summary": summary,
            "elapsed_sec": elapsed,
            "games_completed": len(games),
        }
        executed_configs.append(name)

    # If configs were explicitly requested, skip orchestration logic.
    explicit_only = bool(args.configs)

    baseline_ok = True
    if "normal_chess_v2" in results:
        normal_draw = float(results["normal_chess_v2"]["summary"]["draw_rate"])
        baseline_ok = normal_draw <= float(args.baseline_draw_gate)
        if (not baseline_ok) and (not args.allow_high_draw):
            append_jsonl(
                progress_log_path,
                {
                    "ts": now_iso(),
                    "event": "baseline_gate_failed",
                    "draw_rate": normal_draw,
                    "gate": args.baseline_draw_gate,
                },
            )
            write_study_report(
                out_path=out_root / "study_report.md",
                ordered_configs=executed_configs,
                configs=catalog,
                results=results,
                baseline_ok=False,
                baseline_gate_threshold=args.baseline_draw_gate,
                retaliation_positive=False,
            )
            raise SystemExit(
                f"Baseline gate failed: normal draw rate {normal_draw:.4f} > {args.baseline_draw_gate:.4f}. "
                "Use --allow-high-draw to continue."
            )

    retaliation_positive = _retaliation_is_positive(results)

    should_run_extended = (not explicit_only) and (not args.skip_extended) and (
        args.force_extended or retaliation_positive
    )

    if should_run_extended:
        start_idx = len(executed_configs)
        for i, name in enumerate(extended_order, start=1):
            cfg = catalog[name]
            idx = start_idx + i
            total = start_idx + len(extended_order)
            print(
                f"[run] ({idx}/{total}) {name} games={cfg.games} workers={args.workers} "
                f"depth={cfg.white_profile.search_depth} max_nodes={cfg.white_profile.max_nodes}"
            )
            games, summary, elapsed = run_study(
                config=cfg,
                out_root=out_root,
                workers=args.workers,
                base_seed=args.seed + (idx * 100_000),
                max_plies=args.max_plies,
                snapshot_plies=args.snapshot_plies,
                progress_log_path=progress_log_path,
                run_state_path=run_state_path,
            )
            results[name] = {
                "summary": summary,
                "elapsed_sec": elapsed,
                "games_completed": len(games),
            }
            executed_configs.append(name)

        best_ret = _best_retaliation_config(catalog, results)
        if best_ret is not None:
            style_games = _scaled_games(240, args.sample_scale, min_games=20)
            style_rules = best_ret.rules
            style_configs = {
                "style_balanced_vs_balanced": StudyConfig(
                    name="style_balanced_vs_balanced",
                    description="Balanced vs balanced on best retaliation rules",
                    rules=style_rules,
                    games=style_games,
                    white_profile=apply_profile_overrides(PROFILE_BALANCED, args),
                    black_profile=apply_profile_overrides(PROFILE_BALANCED, args),
                ),
                "style_aggressive_vs_balanced": StudyConfig(
                    name="style_aggressive_vs_balanced",
                    description="Aggressive (white) vs balanced (black)",
                    rules=style_rules,
                    games=style_games,
                    white_profile=apply_profile_overrides(PROFILE_AGGRESSIVE, args),
                    black_profile=apply_profile_overrides(PROFILE_BALANCED, args),
                ),
                "style_aggressive_vs_defensive": StudyConfig(
                    name="style_aggressive_vs_defensive",
                    description="Aggressive (white) vs defensive (black)",
                    rules=style_rules,
                    games=style_games,
                    white_profile=apply_profile_overrides(PROFILE_AGGRESSIVE, args),
                    black_profile=apply_profile_overrides(PROFILE_DEFENSIVE, args),
                ),
            }
            catalog.update(style_configs)

            start_idx = len(executed_configs)
            style_order = list(style_configs.keys())
            for i, name in enumerate(style_order, start=1):
                cfg = style_configs[name]
                idx = start_idx + i
                total = start_idx + len(style_order)
                print(
                    f"[run] ({idx}/{total}) {name} games={cfg.games} workers={args.workers} "
                    f"profiles={cfg.white_profile.name}/{cfg.black_profile.name}"
                )
                games, summary, elapsed = run_study(
                    config=cfg,
                    out_root=out_root,
                    workers=args.workers,
                    base_seed=args.seed + (idx * 100_000),
                    max_plies=args.max_plies,
                    snapshot_plies=args.snapshot_plies,
                    progress_log_path=progress_log_path,
                    run_state_path=run_state_path,
                )
                results[name] = {
                    "summary": summary,
                    "elapsed_sec": elapsed,
                    "games_completed": len(games),
                }
                executed_configs.append(name)

    # Aggregate top-level CSV.
    csv_rows = []
    for name in executed_configs:
        summary = results[name]["summary"]
        csv_rows.append(
            {
                "config": name,
                "games": int(summary["num_games"]),
                "draw_rate": float(summary["draw_rate"]),
                "mean_plies": float(summary["mean_plies"]),
                "captures_per_100_plies": float(summary["captures_per_100_plies"]),
                "mean_material_volatility": float(summary["mean_material_volatility"]),
                "mean_redeployments": float(summary["mean_redeployments"]),
                "retaliation_target_capture_success_rate": float(
                    summary["retaliation_target_capture_success_rate"]
                ),
                "mean_search_depth_achieved": float(summary["mean_search_depth_achieved"]),
                "mean_search_nodes": float(summary["mean_search_nodes"]),
                "games_per_minute": float(summary["games_per_minute"]),
                "interestingness_v2": float(summary["interestingness_v2"]),
                "description": catalog[name].description,
            }
        )

    with (out_root / "study_summary.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "config",
                "games",
                "draw_rate",
                "mean_plies",
                "captures_per_100_plies",
                "mean_material_volatility",
                "mean_redeployments",
                "retaliation_target_capture_success_rate",
                "mean_search_depth_achieved",
                "mean_search_nodes",
                "games_per_minute",
                "interestingness_v2",
                "description",
            ],
        )
        writer.writeheader()
        for row in csv_rows:
            writer.writerow(row)

    write_study_report(
        out_path=out_root / "study_report.md",
        ordered_configs=executed_configs,
        configs=catalog,
        results=results,
        baseline_ok=baseline_ok,
        baseline_gate_threshold=args.baseline_draw_gate,
        retaliation_positive=retaliation_positive,
    )

    atomic_write_json(
        out_root / "run_state.json",
        {
            "updated_at": now_iso(),
            "status": "complete",
            "executed_configs": executed_configs,
            "baseline_ok": baseline_ok,
            "retaliation_positive": retaliation_positive,
        },
    )

    append_jsonl(
        progress_log_path,
        {
            "ts": now_iso(),
            "event": "run_complete",
            "executed_configs": executed_configs,
            "baseline_ok": baseline_ok,
            "retaliation_positive": retaliation_positive,
        },
    )

    print(f"[done] outputs in {out_root}")
    print("[done] key files: study_report.md, study_summary.csv, progress.jsonl, run_state.json")


if __name__ == "__main__":
    main()
