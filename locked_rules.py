"""Locked structural rules from Phase 1 optimization (run_open_20260305).

These settings were determined to be robust across engine strengths because
they change what is legal/illegal, not how deeply you calculate.
RULES.md remains the canonical game definition and is not modified.
"""

from __future__ import annotations

from simulate_variant_study import RuleConfig

# Structural locks (high confidence, engine-independent)
LOCKED_STRUCTURAL = {
    "ruleset": "matryoshka",
    "tier2_slider_max_range": 5,
    "stalemate_is_loss": True,
    "win_condition": "checkmate_or_king_capture",
    "ko_repetition_illegal": True,
}

# King mode: keep both top contenders in active testing.
KING_MODES_TO_TEST = [
    {"king_move_mode": "king_capture_line", "king_capture_line_range": 3},
    {"king_move_mode": "king_dash", "king_dash_max": 2},
]

# Retaliation parameters remain an open design space for Phase 2/3.
# Note: current simulator supports targeting values:
# highest_safe | localized_safe | top2_pool_safe.
RETALIATION_SEARCH_SPACE = {
    "retaliation_enabled": [True, False],
    "retaliation_targeting": ["highest_safe", "localized_safe", "top2_pool_safe"],
    "retaliation_local_radius": [2, 3, 4, 5],
    "retaliation_strike_window": [1, 2, 3, 4],
    "retaliation_tiebreak": ["random", "max_threat", "min_king_distance"],
    "strike_effect": ["perma_kill", "double_demote"],
}

# Doom clock remains variable until the stronger engine confirms necessity.
DOOM_CLOCK_SEARCH_SPACE = {
    "doom_clock_full_moves": [0, 24, 32],
    "doom_clock_effect": ["demote_random_non_king", "bonus_capture_damage"],
}

# Values intentionally excluded from future search.
EXCLUDED = {
    "ruleset": ["normal", "circe", "anticirce"],
    "king_move_mode": ["king_k_range"],
    "quiet_halfmove_limit": [0, 100],
}


def build_locked_config(**overrides: object) -> RuleConfig:
    """Build a RuleConfig with structural locks plus any overrides."""

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


def build_corrected_config(**overrides: object) -> RuleConfig:
    """Build a RuleConfig aligned with designer's intent.

    Changes from build_locked_config:
    - win_condition: checkmate_only (not king_capture)
    - retaliation_mode: attacker_rekill (not defender_strike)
    - stalemate_is_loss: False (standard stalemate = draw)
    - king_move_mode: normal (per RULES.md)
    - king_capture_insta_kill: on (king permakill is in RULES.md)
    """
    defaults = {
        "ruleset": "matryoshka",
        "tier2_slider_max_range": 5,
        "tier3_slider_max_range": 1,
        "win_condition": "checkmate_only",
        "stalemate_is_loss": False,
        "ko_repetition_illegal": True,
        "king_move_mode": "normal",
        "king_capture_insta_kill": "on",
        "king_infinite_kill": False,
        "king_dash_max": 2,
        "king_k_range": 2,
        "king_capture_line_range": 3,
        "quiet_halfmove_limit": 60,
        "retaliation_enabled": True,
        "retaliation_mode": "attacker_rekill",
        "retaliation_targeting": "highest_unsafe",
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
    }
    defaults.update(overrides)
    return RuleConfig(**defaults)
