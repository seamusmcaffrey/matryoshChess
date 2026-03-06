# Matryoshka Chess Web UI

Two-player hot-seat prototype. Vite + vanilla JS. No frameworks.

## Quick Start

```bash
cd ui
npm run dev
# Open http://localhost:5173
```

---

# Architecture

```
ui/
  index.html              # Shell, loads main.js
  src/
    main.js               # Entry point, wires everything together
    style.css             # All styles (board, pieces, effects, layout)
    board.js              # Board rendering and click handling
    game.js               # Game state, legal moves, Matryoshka rules
    pieces.js             # Piece definitions, tier system, SVG mapping
    retaliation.js        # Retaliation placement logic
    moves.js              # Move generation (per-piece, per-tier)
    ui-effects.js         # Smoke animation, highlights, tier badges
  public/
    pieces/
      white/  K.svg Q.svg R.svg B.svg N.svg P.svg
      black/  K.svg Q.svg R.svg B.svg N.svg P.svg
```

## Assets

SVG chess pieces are already in `public/pieces/`. These are standard Staunton-style SVGs (Colin Burnett style, same as Lichess). White pieces: white fill, black stroke. Black pieces: black fill, white detail strokes. All use `viewBox="0 0 45 45"`.

For degraded tiers, we overlay visual effects (opacity, size reduction, CSS filters) rather than needing separate SVG files per tier.

---

# Build Phases

Each phase produces a visible result at localhost. Build them in order.

## Phase 1: Static Board with Pieces

**Goal:** Chessboard with all 32 pieces in starting position. Looks like chess.

### index.html
- Title: "Matryoshka Chess"
- Single `<div id="app">` container
- Loads `src/main.js` as module

### style.css
- CSS grid: 8x8 board, square aspect ratio
- Light squares: `#f0d9b5`, dark squares: `#b58863` (classic wood theme)
- Board centered on page, max 560px wide (70px squares)
- Each square is a `<div>` with class `.square .light`/`.dark`
- Piece images: `<img>` inside square divs, sized to ~85% of square
- Rank (1-8) and file (a-h) labels along edges
- Dark background around board: `#312e2b`
- Turn indicator above board: "White to move" / "Black to move"

### board.js
- `createBoard()` — generates 64 square divs in an 8x8 CSS grid
- Each square has `data-row` (0-7, top=0=rank8) and `data-col` (0-7, left=0=a-file)
- `renderPieces(gameState)` — places `<img>` elements for each piece
  - Image src: `/pieces/{color}/{type}.svg` where type is K/Q/R/B/N/P
- Board orientation: White at bottom (row 7 = rank 1)

### pieces.js
- `PIECE_TYPES`: K, Q, R, B, N, P
- `STARTING_POSITION`: standard chess array
  - Row 0: black back rank `[R,N,B,Q,K,B,N,R]`
  - Row 1: black pawns
  - Row 6: white pawns
  - Row 7: white back rank
- Each piece object: `{ type, color, tier, id }` where tier=1 (full strength)

### game.js (stub)
- `createGameState()` — returns `{ board: 8x8 array, turn: 'white', moveHistory: [], captured: [] }`
- Board is array of piece objects or null

### main.js
- Imports everything, calls `createGameState()`, `createBoard()`, `renderPieces()`

**Checkpoint:** Board with pieces visible at localhost. No interactivity yet.

---

## Phase 2: Click-to-Show Legal Moves

**Goal:** Click a piece on your turn, see highlighted legal move squares. Click a highlighted square to move.

### moves.js — Move Generation
- `getLegalMoves(gameState, row, col)` returns array of `{row, col, isCapture}`
- Standard chess movement per piece type:
  - **Pawn:** Forward 1 (2 from start), diagonal capture, en passant
  - **Knight:** L-shape jumps (no range limit at tier 1)
  - **Bishop:** Diagonal sliding
  - **Rook:** Orthogonal sliding
  - **Queen:** Both diagonal + orthogonal sliding
  - **King:** 1 square any direction, castling (kingside/queenside)
- Sliding pieces blocked by occupied squares (capture or stop)
- Cannot move into check, must escape check, cannot capture own pieces
- `isInCheck(gameState, color)` — is the king of `color` attacked?
- `isCheckmate(gameState, color)` — in check with no legal moves?
- `isStalemate(gameState, color)` — not in check but no legal moves?

### board.js — Interaction
- Click handler on squares:
  1. If no piece selected: click own piece → select it, highlight legal moves
  2. If piece selected: click highlighted square → make move; click elsewhere → deselect
- Highlight styles:
  - Selected piece square: yellow tint overlay
  - Legal move (empty): green dot in center of square (CSS pseudo-element or small div)
  - Legal move (capture): red corner triangles or red ring around square
- After move: update game state, switch turn, re-render

### game.js — Move Execution
- `makeMove(gameState, from, to)` — moves piece, handles captures (standard chess only for now)
- Updates turn, pushes to moveHistory
- Capture → piece goes to captured list (no Matryoshka yet)

### style.css additions
- `.square.selected` — yellow/gold highlight
- `.square.legal-move::after` — green dot (absolute positioned circle)
- `.square.legal-capture` — red border or red corner markers
- `.square.in-check` — red background tint on king's square
- Cursor: pointer on own pieces, default elsewhere

**Checkpoint:** Playable standard chess (no special rules yet). Two players can take turns.

---

## Phase 3: Matryoshka Degradation

**Goal:** Captured pieces degrade and stay on board (placed at capture square initially, before retaliation).

### pieces.js — Tier System

```
TIER_NAMES = { 1: 'Full', 2: 'Damaged', 3: 'Crippled', 4: 'Pawn' }

DEGRADATION_PATH = {
  Q: ['Q', 'R', 'B', 'P'],   // Queen → Rook → Bishop → Pawn
  R: ['R', 'B', 'P'],         // Rook → Bishop → Pawn
  B: ['B', 'P'],              // Bishop → Pawn
  N: ['N', 'W', 'P'],         // Knight → Wazir → Pawn
  P: [null],                   // Pawn → removed
  K: [null],                   // King → game over (shouldn't happen via capture)
}
```

- `degradePiece(piece)` — returns new piece one tier down, or null if pawn
  - Queen tier 1 → type becomes R, tier becomes 2
  - Queen tier 2 (damaged rook) → type becomes B, tier becomes 3
  - Queen tier 3 (crippled bishop) → type becomes P, tier becomes 4
  - Queen tier 4 (pawn) → null (removed)
  - Original type tracked in `piece.originalType` for visual identity
- Wazir (degraded knight): moves 1 square orthogonally (like a rook with range 1)

### moves.js — Range Limits
- Tier 2 (Damaged): sliding range capped at 5 squares
- Tier 3 (Crippled): sliding range capped at 1 square
- Tier 4 (Pawn): moves as pawn regardless of original type
- Wazir: 1 square orthogonal only
- Range limit applied in sliding move generation loop

### game.js — Capture with Degradation
- `makeMove()` updated: when capturing a non-king piece:
  1. Remove captured piece from its square
  2. Call `degradePiece()` on it
  3. If result is non-null, piece needs redeployment (Phase 4)
  4. For now (Phase 3): place degraded piece on a random empty square
- King captures → permanent removal (king permakill)

### ui-effects.js — Tier Visuals
- CSS classes per tier applied to piece images:
  - `.tier-1` — normal (no effect)
  - `.tier-2` — slight opacity reduction (0.85), subtle amber border glow
  - `.tier-3` — more opacity (0.7), piece scaled to 75%, red-ish tint via CSS filter
  - `.tier-4` — pawn appearance but keep a tiny badge showing original piece type
- Tier badge: small icon in corner of square showing original piece type (e.g., tiny queen icon on a degraded pawn that was originally a queen)
- `renderTierBadge(piece)` — if `piece.originalType !== piece.type`, show badge

### style.css additions
- `.tier-2 img` — `opacity: 0.85; filter: sepia(0.2);`
- `.tier-3 img` — `opacity: 0.7; transform: scale(0.75); filter: sepia(0.4) saturate(1.5);`
- `.tier-badge` — tiny 14px icon positioned at bottom-right of square
- Capture animation: brief flash on square when capture+degradation happens

**Checkpoint:** Capturing pieces causes them to degrade visually and stay on board. King permanently kills.

---

## Phase 4: Retaliation Placement

**Goal:** Degraded pieces redeploy threatening the opponent's most valuable piece.

### retaliation.js — Placement Logic

- `findRetaliationSquare(gameState, piece)` — finds best square to place degraded piece
  1. Get all enemy (from piece's perspective) pieces, sorted by value (Q > R > B > N > P)
  2. For each enemy target (highest value first):
     a. Find all empty squares where `piece` (at its new tier/type) could attack the target
     b. **Unsafe targeting** (skip safety check per game assessment recommendation): place on any attacking square, even if the piece can be recaptured
     c. If multiple squares found, pick randomly
     d. If a square is found, return `{ square, target }`
  3. Fallback: Circe square (piece's original starting square)
  4. Final fallback: random empty square

- `getCirceSquare(piece)` — the original starting square for this piece
  - Based on `piece.startCol` and `piece.color`
  - White back rank = row 7, Black back rank = row 0

### game.js — Integrate Retaliation
- After capture + degradation:
  1. Call `findRetaliationSquare()`
  2. Place piece on returned square
  3. Mark piece with `permakillVulnerable: 1` (window=1 attacker-rekill)
  4. Record `capturer` piece ID for tracking
- On capturer's next move:
  - If capturer captures the redeployed piece → **permanent removal** (permakill)
  - Decrement `permakillVulnerable` counter after capturer's side moves
  - When counter hits 0, piece is safe (no longer vulnerable to permakill)

### ui-effects.js — Retaliation Visuals
- **Smoke effect on vulnerable pieces:**
  - CSS animation: wispy smoke particles drifting upward from piece
  - Implementation: 3-4 small semi-transparent circles with `@keyframes` animation
  - Applied via `.permakill-vulnerable` class
  - Smoke color: dark gray/red tint
  - Disappears when vulnerability window expires
- **Retaliation placement animation:**
  - Brief "whoosh" — piece fades in on new square with scale-up animation
  - Arrow or line briefly showing what piece is being threatened
- **Target highlight:**
  - The threatened piece gets a pulsing border to show it's under retaliation threat

### style.css additions
```css
.permakill-vulnerable {
  position: relative;
}
.permakill-vulnerable::before,
.permakill-vulnerable::after {
  content: '';
  position: absolute;
  border-radius: 50%;
  background: rgba(100, 100, 100, 0.3);
  animation: smoke 2s infinite ease-out;
}
@keyframes smoke {
  0% { transform: translateY(0) scale(1); opacity: 0.4; }
  100% { transform: translateY(-20px) scale(2); opacity: 0; }
}
.retaliation-target {
  box-shadow: 0 0 8px 2px rgba(255, 0, 0, 0.5);
  animation: pulse 1s infinite;
}
```

**Checkpoint:** Full Matryoshka capture cycle visible: capture → degrade → redeploy with smoke → permakill window.

---

## Phase 5: Game End + Polish

**Goal:** Complete game with win/draw detection, move history, and UX polish.

### game.js — Win/Draw Conditions
- Checkmate detection (already from Phase 2, but now with tier-limited move generation)
- Stalemate = draw
- Ko repetition detection: track position hashes, ban repeated positions
- Resignation button
- Game-over overlay with result

### UI Polish
- **Move history panel** (right side of board):
  - Algebraic notation with tier annotations
  - e.g., `Qxe5 → [R2]e5` (queen captured on e5, rook tier-2 deployed to e5)
  - Scrollable list, latest move highlighted
- **Captured pieces display** (above/below board):
  - Show permanently removed pieces (permakilled)
  - Organized by color
- **Turn indicator** with player names ("White" / "Black")
- **New Game button**
- **Undo button** (pops last move from history)
- **Pawn promotion dialog**: popup asking Q/R/B/N when pawn reaches back rank
  - Promoted piece is always tier 1 (full strength)
- Sound effects (optional, low priority):
  - Move sound, capture sound, check sound
  - Can use Web Audio API for simple tones

### Layout
```
┌──────────────────────────────────────┐
│         MATRYOSHKA CHESS             │
│         Black to move                │
├──────────────────┬───────────────────┤
│                  │  Move History     │
│                  │  1. e4  e5        │
│    8x8 Board     │  2. Nf3 Nc6      │
│    (560x560)     │  3. Bb5 ...      │
│                  │                   │
│                  │                   │
│                  │                   │
├──────────────────┴───────────────────┤
│  [New Game]  [Undo]  [Resign]        │
│  Removed: ♛ ♜ ♝                      │
└──────────────────────────────────────┘
```

**Checkpoint:** Complete, polished, playable two-player Matryoshka Chess.

---

# Game Rules Reference (for implementation)

These are the corrected/validated rules from Phase 4 testing:

| Rule | Value |
|---|---|
| Piece degradation | Q→R(d)→B(c)→P→dead; N→Wazir→P→dead |
| Tier 2 range cap | 5 squares |
| Tier 3 range cap | 1 square |
| Retaliation mode | attacker-rekill |
| Retaliation targeting | highest_unsafe (skip safety check) |
| Permakill window | 1 (capturer gets 1 move to re-kill) |
| King permakill | ON (king captures = permanent removal) |
| Win condition | Checkmate only |
| Stalemate | Draw |
| Ko repetition | Banned (repeated positions illegal) |
| Castling | Standard rules |
| En passant | Standard rules (captured pawn degrades... but pawns just die) |
| Promotion | Pawn reaching back rank promotes to full-tier piece |

### Degradation Ladder (detailed)

| Original | Tier 1 | Tier 2 (Damaged) | Tier 3 (Crippled) | Tier 4 (Collapse) | Tier 5 |
|---|---|---|---|---|---|
| Queen | Queen | Rook (range 5) | Bishop (range 1) | Pawn | Dead |
| Rook | Rook | Bishop (range 5) | Pawn | Dead | — |
| Bishop | Bishop | Pawn | Dead | — | — |
| Knight | Knight | Wazir (1sq ortho) | Pawn | Dead | — |
| Pawn | Pawn | Dead | — | — | — |

### Retaliation Flow (per capture)

```
1. White captures Black's Queen on e5
2. Queen demotes → Damaged Rook (tier 2, range 5)
3. Find square where Damaged Rook attacks White's highest-value piece
   - Example: White Queen on d1 → place rook on d6 (attacks d1 along file)
4. Damaged Rook appears on d6 with smoke effect (permakill vulnerable)
5. White's NEXT move:
   - If White captures the rook on d6 → PERMANENT REMOVAL (permakill)
   - If White does anything else → rook on d6 becomes safe, smoke clears
```

### King Permakill Flow

```
1. King captures any piece → that piece is permanently removed
2. No degradation, no retaliation, no respawn
3. Piece goes to the "removed" display
```

---

# Technical Notes

- **No build dependencies beyond Vite.** Pure vanilla JS, no React/Vue/etc.
- **All state in memory.** No backend, no persistence, no network.
- **SVG pieces loaded as `<img>` tags** pointing to `/pieces/{color}/{type}.svg`. Vite serves `public/` at root.
- **Board coordinates:** `row` 0-7 (top to bottom), `col` 0-7 (left to right). Row 0 = rank 8, row 7 = rank 1.
- **Piece identity:** Each piece gets a unique ID at game start (e.g., `w-Q-1`, `b-N-2`). Tracked through degradation.
- **Hot reload:** Vite HMR means saved file changes appear instantly in browser. Build phases incrementally.
- **CSS-only effects:** Smoke, highlights, and animations are pure CSS — no canvas or WebGL needed.
- **Mobile-friendly (stretch goal):** CSS grid adapts. Touch events work like click events.
