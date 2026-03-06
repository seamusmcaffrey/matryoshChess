## 2026-03-06 21:00

## Full Matryoshka Chess UI: 5-phase implementation with 2-player networking and mobile support

## Issues identified (if relevant)
During code review, several bugs were caught and fixed before they could impact gameplay:
1. Permakill window off-by-one (expiry counter was +3 instead of +2, giving capturer an extra move)
2. Multi-undo crash (history array not preserved across undo, causing undefined error on second undo)
3. Collapsed pawns incorrectly receiving double-move privilege from pawn starting row
4. Ko/repetition detection recorded positions but never enforced draw
5. Notation bug where promoted pawns showed post-promotion type (e.g. `Qe8=Q` instead of `e8=Q`)
6. Redundant fallback in promotion notation string builder

## Issue resolution implemented (if there was an issue)
1. Changed `expiresOnMoveCount` from `state.moveCount + 3` to `+ 2` in game.js
2. Removed `state.history` restoration in `undoMove()` — the pop itself is sufficient
3. Added `piece.originalType === 'P'` guard to double-move check in moves.js
4. Added threefold repetition detection with `draw-repetition` status in game.js
5. Introduced `wasPawnBeforePromotion` flag captured before type mutation for notation
6. Removed redundant `|| 'Q'` fallback in promotion notation

## File(s) modified:
- `ui/index.html` — Updated favicon from .svg to .png
- `ui/package.json` — Added `ws` dependency, `--host` flag, `server` and `dev:network` scripts
- `ui/server.js` — New WebSocket relay server for 2-player networked play (port 3001)
- `ui/src/pieces.js` — New module: piece types, tier system, degradation ladder, starting board
- `ui/src/moves.js` — New module: move generation with tier range limits, castling, en passant, check/checkmate/stalemate
- `ui/src/retaliation.js` — New module: highest_unsafe retaliation placement targeting enemy's most valuable piece
- `ui/src/game.js` — New module: game state, move execution, Matryoshka capture cycle, permakill tracking, ko detection, undo
- `ui/src/ui-effects.js` — New module: tier badges, smoke particles for permakill-vulnerable pieces
- `ui/src/board.js` — New module: board rendering with orientation support (flipped for Black player)
- `ui/src/main.js` — Rewritten: lobby screen, WebSocket networking, click-to-move, promotion dialog, auto-play, game controls
- `ui/src/style.css` — Rewritten: complete styles for all 5 phases, lobby, networking UI, mobile responsive layout
