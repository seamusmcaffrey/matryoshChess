#!/usr/bin/env python3
"""Run large-scale variant sweeps and sample-size diagnostics.

Produces:
- per-variant game CSV + summary JSON
- combined metrics table (CSV + Markdown)
- metric glossary
- sample-size validation report for 1k-game stability
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import json
import math
import os
import statistics
from dataclasses import dataclass
from typing import Dict, List, Sequence

from simulate_variant_study import RuleConfig, run_batch


@dataclass(frozen=True)
class VariantSpec:
    name: str
    description: str
    rules: RuleConfig


def ci95_proportion(successes: int, n: int) -> float:
    if n <= 0:
        return 0.0
    p = successes / n
    return 1.96 * math.sqrt(max(0.0, p * (1.0 - p)) / n)


def ci95_mean(values: Sequence[float]) -> float:
    n = len(values)
    if n <= 1:
        return 0.0
    sd = statistics.stdev(values)
    return 1.96 * (sd / math.sqrt(n))


def summarize_prefix(games: Sequence[Dict[str, object]], k: int) -> Dict[str, float]:
    rows = list(games[:k])
    n = len(rows)
    if n == 0:
        return {"draw_rate": 0.0, "mean_plies": 0.0, "captures_per_100_plies": 0.0}

    draws = sum(1 for g in rows if g["winner"] == "D")
    mean_plies = statistics.mean(float(g["plies"]) for g in rows)
    mean_captures = statistics.mean(float(g["captures_total"]) for g in rows)
    cap_rate = (mean_captures / mean_plies * 100.0) if mean_plies else 0.0
    return {
        "draw_rate": draws / n,
        "mean_plies": mean_plies,
        "captures_per_100_plies": cap_rate,
    }


def variant_specs() -> List[VariantSpec]:
    return [
        VariantSpec(
            "mat_base",
            "Matryoshka baseline",
            RuleConfig(ruleset="matryoshka", tier2_slider_max_range=4, retaliation_strike_window=1),
        ),
        VariantSpec(
            "mat_primary_sw2",
            "Matryoshka primary tweak: strike window 2",
            RuleConfig(ruleset="matryoshka", tier2_slider_max_range=4, retaliation_strike_window=2),
        ),
        VariantSpec(
            "mat_secondary_t2r3",
            "Matryoshka secondary tweak: Tier-2 slider range 3",
            RuleConfig(ruleset="matryoshka", tier2_slider_max_range=3, retaliation_strike_window=1),
        ),
        VariantSpec(
            "mat_combo_sw2_t2r3",
            "Matryoshka combo: strike window 2 + Tier-2 range 3",
            RuleConfig(ruleset="matryoshka", tier2_slider_max_range=3, retaliation_strike_window=2),
        ),
        VariantSpec(
            "mat_base_kinginf",
            "Matryoshka baseline + king infinite kill",
            RuleConfig(
                ruleset="matryoshka",
                tier2_slider_max_range=4,
                retaliation_strike_window=1,
                king_infinite_kill=True,
            ),
        ),
        VariantSpec(
            "mat_primary_sw2_kinginf",
            "Matryoshka primary tweak + king infinite kill",
            RuleConfig(
                ruleset="matryoshka",
                tier2_slider_max_range=4,
                retaliation_strike_window=2,
                king_infinite_kill=True,
            ),
        ),
        VariantSpec(
            "mat_secondary_t2r3_kinginf",
            "Matryoshka secondary tweak + king infinite kill",
            RuleConfig(
                ruleset="matryoshka",
                tier2_slider_max_range=3,
                retaliation_strike_window=1,
                king_infinite_kill=True,
            ),
        ),
        VariantSpec(
            "mat_combo_sw2_t2r3_kinginf",
            "Matryoshka combo + king infinite kill",
            RuleConfig(
                ruleset="matryoshka",
                tier2_slider_max_range=3,
                retaliation_strike_window=2,
                king_infinite_kill=True,
            ),
        ),
        VariantSpec(
            "normal",
            "Normal chess baseline (same engine simplifications)",
            RuleConfig(ruleset="normal"),
        ),
        VariantSpec(
            "normal_kinginf",
            "Normal chess + king infinite kill",
            RuleConfig(ruleset="normal", king_infinite_kill=True),
        ),
        VariantSpec(
            "circe",
            "Circe-like variant: captured piece reborn on origin square if empty",
            RuleConfig(ruleset="circe"),
        ),
        VariantSpec(
            "anticirce",
            "Anticirce-like variant: capturing piece reborn on origin square",
            RuleConfig(ruleset="anticirce"),
        ),
        VariantSpec(
            "circe_kinginf",
            "Circe-like + king infinite kill",
            RuleConfig(ruleset="circe", king_infinite_kill=True),
        ),
        VariantSpec(
            "anticirce_kinginf",
            "Anticirce-like + king infinite kill",
            RuleConfig(ruleset="anticirce", king_infinite_kill=True),
        ),
    ]


def write_variant_games_csv(path: str, games: Sequence[Dict[str, object]]) -> None:
    if not games:
        return

    rows = []
    for i, g in enumerate(games, start=1):
        row = dict(g)
        row["game_id"] = i
        row.pop("move_log", None)
        rows.append(row)

    fieldnames = ["game_id"] + [k for k in rows[0].keys() if k != "game_id"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run variant suite.")
    parser.add_argument("--games", type=int, default=1000, help="Games per variant")
    parser.add_argument("--max-plies", type=int, default=220, help="Max plies per game")
    parser.add_argument("--seed", type=int, default=314159, help="Base seed")
    parser.add_argument("--workers", type=int, default=8, help="Worker processes per variant")
    parser.add_argument(
        "--output-dir",
        type=str,
        default="outputs_suite",
        help="Output directory",
    )
    args = parser.parse_args()

    specs = variant_specs()
    os.makedirs(args.output_dir, exist_ok=True)
    variants_dir = os.path.join(args.output_dir, "variants")
    os.makedirs(variants_dir, exist_ok=True)

    all_game_rows: List[Dict[str, object]] = []
    summary_rows: List[Dict[str, object]] = []
    validation_rows: List[Dict[str, object]] = []

    checkpoints = [100, 200, 400, 600, 800, 1000]

    for idx, spec in enumerate(specs):
        variant_seed = args.seed + (idx * 1_000_003)
        print(f"Running {spec.name} ({idx + 1}/{len(specs)}) ...")

        games, summary, _ = run_batch(
            num_games=args.games,
            max_plies=args.max_plies,
            seed=variant_seed,
            rules=spec.rules,
            include_move_log=False,
            workers=max(1, args.workers),
        )
        games_sorted = sorted(games, key=lambda g: int(g["seed"]))

        variant_out = os.path.join(variants_dir, spec.name)
        os.makedirs(variant_out, exist_ok=True)

        write_variant_games_csv(os.path.join(variant_out, "games.csv"), games_sorted)

        with open(os.path.join(variant_out, "summary.json"), "w", encoding="utf-8") as f:
            json.dump(
                {
                    "variant": spec.name,
                    "description": spec.description,
                    "rules": dataclasses.asdict(spec.rules),
                    "summary": summary,
                    "notes": {
                        "engine_simplifications": [
                            "No castling",
                            "No en passant",
                            "Promotion always to queen",
                        ]
                    },
                },
                f,
                indent=2,
            )

        for game_id, g in enumerate(games_sorted, start=1):
            row = dict(g)
            row.pop("move_log", None)
            row["variant"] = spec.name
            row["game_id"] = game_id
            all_game_rows.append(row)

        n = len(games_sorted)
        draws = int(summary["winner_counts"].get("D", 0))
        plies_vals = [float(g["plies"]) for g in games_sorted]
        captures_vals = [float(g["captures_total"]) for g in games_sorted]

        draw_ci_hw = ci95_proportion(draws, n)
        plies_ci_hw = ci95_mean(plies_vals)
        captures_ci_hw = ci95_mean(captures_vals)

        available_checkpoints = [k for k in checkpoints if k <= n]
        prefix = {k: summarize_prefix(games_sorted, k) for k in available_checkpoints}

        delta_draw_800_1000 = None
        delta_plies_800_1000 = None
        delta_caprate_800_1000 = None
        if 800 in prefix and 1000 in prefix:
            delta_draw_800_1000 = abs(prefix[1000]["draw_rate"] - prefix[800]["draw_rate"])
            delta_plies_800_1000 = abs(prefix[1000]["mean_plies"] - prefix[800]["mean_plies"])
            delta_caprate_800_1000 = abs(
                prefix[1000]["captures_per_100_plies"] - prefix[800]["captures_per_100_plies"]
            )

        settled_1k = (
            n >= 1000
            and draw_ci_hw <= 0.03
            and plies_ci_hw <= 2.5
            and captures_ci_hw <= 1.1
            and (delta_draw_800_1000 is not None and delta_draw_800_1000 <= 0.015)
            and (delta_plies_800_1000 is not None and delta_plies_800_1000 <= 1.8)
            and (delta_caprate_800_1000 is not None and delta_caprate_800_1000 <= 0.8)
        )

        summary_rows.append(
            {
                "variant": spec.name,
                "description": spec.description,
                "ruleset": spec.rules.ruleset,
                "king_infinite_kill": spec.rules.king_infinite_kill,
                "tier2_range": spec.rules.tier2_slider_max_range,
                "strike_window": spec.rules.retaliation_strike_window,
                "fallback_policy": spec.rules.fallback_policy,
                "games": n,
                "draw_rate": summary["draw_rate"],
                "decisive_rate": summary["decisive_rate"],
                "mean_plies": summary["mean_plies"],
                "mean_full_moves": summary["mean_full_moves"],
                "captures_per_100_plies": summary["captures_per_100_plies"],
                "mean_captures": summary["mean_captures"],
                "checkmate_rate": summary["checkmate_rate"],
                "king_capture_termination_rate": summary["king_capture_termination_rate"],
                "mean_permanent_removals": summary["mean_permanent_removals"],
                "permanent_capture_share": summary["permanent_capture_share"],
                "mean_redeployments": summary["mean_redeployments"],
                "mean_circe_captured_rebirths": summary["mean_circe_captured_rebirths"],
                "mean_anticirce_attacker_rebirths": summary["mean_anticirce_attacker_rebirths"],
                "strike_success": summary["retaliation_target_capture_success_rate"],
                "safe_redeploy_share": summary["safe_redeploy_share"],
                "mean_material_volatility": summary["mean_material_volatility"],
                "mean_lead_sign_changes": summary["mean_lead_sign_changes"],
                "draw_ci95_halfwidth": round(draw_ci_hw, 4),
                "mean_plies_ci95_halfwidth": round(plies_ci_hw, 3),
                "mean_captures_ci95_halfwidth": round(captures_ci_hw, 3),
                "settled_1k": settled_1k,
            }
        )

        validation_rows.append(
            {
                "variant": spec.name,
                "games": n,
                "draw_ci95_halfwidth": round(draw_ci_hw, 4),
                "mean_plies_ci95_halfwidth": round(plies_ci_hw, 3),
                "mean_captures_ci95_halfwidth": round(captures_ci_hw, 3),
                "delta_draw_800_1000": (
                    round(delta_draw_800_1000, 4) if delta_draw_800_1000 is not None else None
                ),
                "delta_plies_800_1000": (
                    round(delta_plies_800_1000, 3) if delta_plies_800_1000 is not None else None
                ),
                "delta_captures_per_100_plies_800_1000": (
                    round(delta_caprate_800_1000, 3) if delta_caprate_800_1000 is not None else None
                ),
                "settled_1k": settled_1k,
                "prefix": prefix,
            }
        )

        print(
            f"  done: draw={summary['draw_rate']:.3f}, mean_plies={summary['mean_plies']:.1f}, "
            f"checkmate_rate={summary['checkmate_rate']:.3f}, settled_1k={settled_1k}"
        )

    # Write combined game-level data.
    all_games_csv = os.path.join(args.output_dir, "all_games.csv")
    if all_game_rows:
        fieldnames = ["variant", "game_id"] + [
            k for k in all_game_rows[0].keys() if k not in {"variant", "game_id"}
        ]
        with open(all_games_csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(all_game_rows)

    # Write summary CSV.
    summary_csv = os.path.join(args.output_dir, "variant_summary.csv")
    if summary_rows:
        fieldnames = list(summary_rows[0].keys())
        with open(summary_csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(summary_rows)

    # Write summary JSON.
    with open(os.path.join(args.output_dir, "variant_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary_rows, f, indent=2)

    with open(os.path.join(args.output_dir, "sample_size_validation.json"), "w", encoding="utf-8") as f:
        json.dump(validation_rows, f, indent=2)

    # Markdown table.
    table_md = os.path.join(args.output_dir, "variant_table.md")
    with open(table_md, "w", encoding="utf-8") as f:
        f.write("# Variant Metrics Table\n\n")
        f.write(
            "| Variant | Ruleset | King∞Kill | Draw% | Decisive% | Mean Plies | Mean Moves | Captures/100 plies | Checkmate% | KingCaptureEnd% | Redeploys | CirceRebirths | AntiCirceRebirths | Strike Success | Settled@1k |\n"
        )
        f.write("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|\n")
        for row in summary_rows:
            f.write(
                f"| {row['variant']} | {row['ruleset']} | {int(bool(row['king_infinite_kill']))} "
                f"| {row['draw_rate']*100:.1f} | {row['decisive_rate']*100:.1f} "
                f"| {row['mean_plies']:.1f} | {row['mean_full_moves']:.1f} "
                f"| {row['captures_per_100_plies']:.2f} | {row['checkmate_rate']*100:.1f} "
                f"| {row['king_capture_termination_rate']*100:.1f} "
                f"| {row['mean_redeployments']:.2f} | {row['mean_circe_captured_rebirths']:.2f} "
                f"| {row['mean_anticirce_attacker_rebirths']:.2f} "
                f"| {row['strike_success']:.3f} | {str(row['settled_1k'])} |\n"
            )

    # Metric glossary.
    glossary_md = os.path.join(args.output_dir, "metric_glossary.md")
    with open(glossary_md, "w", encoding="utf-8") as f:
        f.write("# Metric Glossary\n\n")
        f.write("- **Ply**: one half-move (one player's turn).\n")
        f.write("- **Mean Moves**: mean full moves, computed as mean plies / 2.\n")
        f.write("- **Draw%**: fraction of games ending in draw (stalemate or max-plies).\n")
        f.write("- **Decisive%**: 1 - draw rate.\n")
        f.write("- **Captures/100 plies**: capture tempo; average captures normalized by game length.\n")
        f.write("- **Checkmate%**: fraction of games ending by checkmate.\n")
        f.write("- **KingCaptureEnd%**: fraction ending by king capture under modeled rules.\n")
        f.write("- **Redeploys**: mean count of Matryoshka retaliation redeployments per game.\n")
        f.write("- **CirceRebirths**: mean number of captured-piece rebirths in Circe-style variants.\n")
        f.write("- **AntiCirceRebirths**: mean number of capturing-piece rebirths in Anticirce-style variants.\n")
        f.write("- **Strike Success**: in Matryoshka, share of marked-target capture attempts that succeed.\n")
        f.write("- **Settled@1k**: heuristic stability check at 1,000 games (CI width + prefix drift thresholds).\n")

    # Sample-size validation report.
    validation_md = os.path.join(args.output_dir, "sample_size_validation.md")
    settled_count = sum(1 for row in validation_rows if row["settled_1k"])
    with open(validation_md, "w", encoding="utf-8") as f:
        f.write("# Sample Size Validation\n\n")
        f.write("## Heuristic Criteria for `Settled@1k`\n")
        f.write("- draw-rate 95% CI half-width <= 0.03\n")
        f.write("- mean-plies 95% CI half-width <= 2.5\n")
        f.write("- mean-captures 95% CI half-width <= 1.1\n")
        f.write("- |draw_rate(1000) - draw_rate(800)| <= 0.015\n")
        f.write("- |mean_plies(1000) - mean_plies(800)| <= 1.8\n")
        f.write("- |captures/100(1000) - captures/100(800)| <= 0.8\n\n")
        f.write(f"Variants passing settled@1k: {settled_count} / {len(validation_rows)}\n\n")
        f.write("## Per-Variant Diagnostics\n")
        f.write(
            "| Variant | Draw CI95 ± | Mean Plies CI95 ± | Mean Captures CI95 ± | ΔDraw(800→1000) | ΔPlies(800→1000) | ΔCapRate(800→1000) | Settled@1k |\n"
        )
        f.write("|---|---:|---:|---:|---:|---:|---:|---:|\n")
        for row in validation_rows:
            f.write(
                f"| {row['variant']} | {row['draw_ci95_halfwidth']:.4f} | {row['mean_plies_ci95_halfwidth']:.3f} "
                f"| {row['mean_captures_ci95_halfwidth']:.3f} "
                f"| {row['delta_draw_800_1000'] if row['delta_draw_800_1000'] is not None else 'n/a'} "
                f"| {row['delta_plies_800_1000'] if row['delta_plies_800_1000'] is not None else 'n/a'} "
                f"| {row['delta_captures_per_100_plies_800_1000'] if row['delta_captures_per_100_plies_800_1000'] is not None else 'n/a'} "
                f"| {row['settled_1k']} |\n"
            )

    print("\nSuite complete.")
    print(f"Wrote: {summary_csv}")
    print(f"Wrote: {all_games_csv}")
    print(f"Wrote: {table_md}")
    print(f"Wrote: {glossary_md}")
    print(f"Wrote: {validation_md}")


if __name__ == "__main__":
    main()
