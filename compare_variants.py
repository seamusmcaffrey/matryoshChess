#!/usr/bin/env python3
"""Compare Matryoshka Chess simulation variant summaries.

Reads summary.json files from variant output folders, computes derived metrics,
and writes a compact comparison report.
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import os
from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass
class VariantRow:
    name: str
    rules: Dict[str, object]
    num_games: int
    mean_plies: float
    mean_captures: float
    mean_redeployments: float
    mean_permanent_removals: float
    strike_success: float
    safe_share: float
    lead_changes: float
    volatility: float
    draw_rate: float
    decisive_rate: float
    king_capture_rate: float
    checkmate_rate: float
    capture_per_100_plies: float
    permanent_share: float
    interesting_score: float


def load_variant(path: str) -> Optional[VariantRow]:
    summary_path = os.path.join(path, "summary.json")
    if not os.path.exists(summary_path):
        return None

    with open(summary_path, "r", encoding="utf-8") as f:
        blob = json.load(f)

    s = blob["summary"]
    rules = blob.get("rules", {})
    n = int(s["num_games"])

    draws = int(s["winner_counts"].get("D", 0))
    draw_rate = draws / n if n else 0.0
    decisive_rate = 1.0 - draw_rate
    king_capture_rate = int(s["termination_counts"].get("king_captured", 0)) / n if n else 0.0
    checkmate_rate = int(s["termination_counts"].get("checkmate", 0)) / n if n else 0.0

    mean_plies = float(s["mean_plies"])
    mean_captures = float(s["mean_captures"])
    capture_per_100 = (mean_captures / mean_plies * 100.0) if mean_plies else 0.0
    permanent_share = (float(s["mean_permanent_removals"]) / mean_captures) if mean_captures else 0.0

    # Heuristic "interestingness" metric:
    # - decisive outcomes matter most
    # - checkmates are weighted slightly above king captures
    # - tactical churn from captures / lead changes adds variety
    interesting_score = (
        (decisive_rate * 50.0)
        + (checkmate_rate * 25.0)
        + (capture_per_100 * 1.1)
        + (float(s["mean_lead_sign_changes"]) * 2.2)
    )

    return VariantRow(
        name=os.path.basename(path.rstrip("/")),
        rules=rules,
        num_games=n,
        mean_plies=mean_plies,
        mean_captures=mean_captures,
        mean_redeployments=float(s["mean_redeployments"]),
        mean_permanent_removals=float(s["mean_permanent_removals"]),
        strike_success=float(s["retaliation_target_capture_success_rate"]),
        safe_share=float(s["safe_redeploy_share"]),
        lead_changes=float(s["mean_lead_sign_changes"]),
        volatility=float(s["mean_material_volatility"]),
        draw_rate=draw_rate,
        decisive_rate=decisive_rate,
        king_capture_rate=king_capture_rate,
        checkmate_rate=checkmate_rate,
        capture_per_100_plies=capture_per_100,
        permanent_share=permanent_share,
        interesting_score=interesting_score,
    )


def write_report(rows: List[VariantRow], output_dir: str) -> None:
    os.makedirs(output_dir, exist_ok=True)

    rows_sorted = sorted(rows, key=lambda r: r.interesting_score, reverse=True)

    csv_path = os.path.join(output_dir, "comparison.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "variant",
                "games",
                "draw_rate",
                "decisive_rate",
                "mean_plies",
                "capture_per_100_plies",
                "checkmate_rate",
                "king_capture_rate",
                "mean_permanent_removals",
                "strike_success",
                "safe_redeploy_share",
                "lead_changes",
                "volatility",
                "interesting_score",
                "rules",
            ]
        )
        for row in rows_sorted:
            writer.writerow(
                [
                    row.name,
                    row.num_games,
                    round(row.draw_rate, 4),
                    round(row.decisive_rate, 4),
                    round(row.mean_plies, 3),
                    round(row.capture_per_100_plies, 3),
                    round(row.checkmate_rate, 4),
                    round(row.king_capture_rate, 4),
                    round(row.mean_permanent_removals, 3),
                    round(row.strike_success, 4),
                    round(row.safe_share, 4),
                    round(row.lead_changes, 3),
                    round(row.volatility, 3),
                    round(row.interesting_score, 3),
                    f"t2={row.rules.get('tier2_slider_max_range')};"
                    f"sw={row.rules.get('retaliation_strike_window')};"
                    f"fb={row.rules.get('fallback_policy')}",
                ]
            )

    md_path = os.path.join(output_dir, "comparison.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("# Variant Comparison\n\n")
        f.write("## Ranking (higher interesting_score is better)\n")
        for idx, row in enumerate(rows_sorted, start=1):
            f.write(
                f"{idx}. **{row.name}** - score {row.interesting_score:.2f}, draw_rate {row.draw_rate:.3f}, mean_plies {row.mean_plies:.1f}\n"
            )

        f.write("\n## Metrics\n")
        f.write(
            "| Variant | Draw Rate | Decisive Rate | Mean Plies | Captures/100 plies | Checkmate Rate | King Capture Rate | Strike Success | Safe Redeploy Share | Lead Changes | Rules |\n"
        )
        f.write("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|\n")
        for row in rows_sorted:
            rules = (
                f"t2={row.rules.get('tier2_slider_max_range')}, "
                f"sw={row.rules.get('retaliation_strike_window')}, "
                f"fb={row.rules.get('fallback_policy')}"
            )
            f.write(
                f"| {row.name} | {row.draw_rate:.3f} | {row.decisive_rate:.3f} | {row.mean_plies:.1f} | {row.capture_per_100_plies:.2f} | {row.checkmate_rate:.3f} | {row.king_capture_rate:.3f} | {row.strike_success:.3f} | {row.safe_share:.3f} | {row.lead_changes:.2f} | {rules} |\n"
            )

        if rows_sorted:
            top = rows_sorted[0]
            f.write("\n## Suggested Direction\n")
            f.write(
                f"Based on this batch, `{top.name}` is the strongest candidate for more engaging play under the chosen scoring blend (decisiveness + tactical churn).\n"
            )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare variant summary files.")
    parser.add_argument(
        "--root",
        default="outputs_experiments",
        help="Root directory containing variant subfolders with summary.json",
    )
    parser.add_argument(
        "--output-dir",
        default="outputs_experiments",
        help="Directory for comparison.csv and comparison.md",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    paths = sorted([p for p in glob.glob(os.path.join(args.root, "*")) if os.path.isdir(p)])

    rows: List[VariantRow] = []
    for path in paths:
        row = load_variant(path)
        if row is not None:
            rows.append(row)

    if not rows:
        raise SystemExit("No variant summary.json files found.")

    write_report(rows, output_dir=args.output_dir)
    print(f"Compared {len(rows)} variants.")
    print(f"Wrote: {os.path.join(args.output_dir, 'comparison.csv')}")
    print(f"Wrote: {os.path.join(args.output_dir, 'comparison.md')}")


if __name__ == "__main__":
    main()
