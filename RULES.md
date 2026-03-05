MATRYOSHKA CHESS (Range Decay + Retaliation Variant)

Overview
Captured pieces do not immediately disappear. Instead they lose strength
and re-enter the board, creating immediate counter-pressure on the player
who performed the capture.

Board and Setup
Standard chess board and standard starting position.

Movement
Pieces move as in standard chess except their maximum range may be limited
depending on their damage tier.

Damage System
All non-king pieces have three tiers of strength.

Tier 1 — Full
Normal chess movement.

Tier 2 — Damaged
Sliding pieces (queen, rook, bishop) may move at most 4 squares per move.

Tier 3 — Crippled
Sliding pieces may move only 1 square per move.

Tier 4 — Collapse
The piece becomes a pawn of the same color.

If a pawn is captured, it is removed from the board normally.

Knights degrade differently since they do not use sliding range:

Knight → Wazir (moves 1 square orthogonally) → Pawn → removed.

Capture and Demotion
When a non-king piece is captured:

1. The captured piece survives but demotes by one tier.
2. The demoted piece is immediately redeployed according to the
   Retaliation Placement Rule.

Retaliation Placement Rule
The game attempts to redeploy the piece to a square where it attacks
the highest-value opposing non-king piece that can be safely threatened.

A target is considered safely threatened if the target piece itself
cannot capture the redeployed piece on that square. Other opposing
pieces may still capture the redeployed piece.

Piece value order:
Queen > Rook > Bishop > Knight > Pawn.

If multiple squares satisfy this rule, one is chosen randomly.

If no square exists that safely threatens any opposing non-king piece:

- the piece is placed on its Circe square (its original starting square),
- if that square is unavailable, the piece appears on a random empty square.

Retaliation Strike
When a piece is redeployed via retaliation, the attacked piece becomes
the target piece.

If the redeployed piece captures the target piece on its very next move,
the captured piece is permanently removed instead of demoting.

If the redeployed piece captures any other piece, normal demotion rules apply.

King Capture Rule
If a king captures a piece, the captured piece is permanently removed
and does not demote or redeploy.

Kings
Kings do not degrade or collapse. If a king is captured, the game ends.

Check and Checkmate
Check and checkmate follow normal chess rules, but piece range limits apply.

Example:
A damaged queen (maximum range 4) cannot give check to a king farther
than four squares away, even if aligned on a rank, file, or diagonal.

Edge Case — Demotion Before Placement
When a piece is captured, it demotes before retaliation placement is
calculated. The redeployed piece must obey the movement rules of its
new demoted tier when determining whether it threatens a target.

Promotion
Pawn promotion produces a full-tier piece.

Goal
Checkmate the opposing king.