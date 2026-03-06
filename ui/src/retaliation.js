// ─── Retaliation Placement Logic ─────────────────────────────
import { PIECE_VALUES } from './pieces.js';
import { getPseudoMoves, cloneBoard } from './moves.js';

/**
 * Get the Circe square (original starting position) for a piece.
 */
export function getCirceSquare(piece) {
  const backRank = piece.color === 'white' ? 7 : 0;
  return { row: backRank, col: piece.startCol };
}

/**
 * Find the best retaliation square for a degraded piece.
 *
 * Strategy (highest_unsafe):
 *   1. Target enemy pieces by value (Q > R > B > N > P)
 *   2. For each target, find empty squares where this piece could attack it
 *   3. No safety check — place on any attacking square
 *   4. Fallback: Circe square, then random empty square
 */
export function findRetaliationSquare(gameState, piece) {
  const board = gameState.board;
  const enemyColor = piece.color === 'white' ? 'black' : 'white';

  // Collect enemy pieces sorted by value (highest first)
  const enemies = [];
  for (let r = 0; r < 8; r++)
    for (let c = 0; c < 8; c++)
      if (board[r][c] && board[r][c].color === enemyColor)
        enemies.push({ piece: board[r][c], row: r, col: c });

  enemies.sort(
    (a, b) => (PIECE_VALUES[b.piece.type] || 0) - (PIECE_VALUES[a.piece.type] || 0)
  );

  // For each enemy target, find squares where our piece could attack it
  for (const target of enemies) {
    const candidates = [];

    for (let r = 0; r < 8; r++) {
      for (let c = 0; c < 8; c++) {
        if (board[r][c]) continue;

        // Temporarily place piece to check attacks
        const testBoard = cloneBoard(board);
        testBoard[r][c] = piece;

        const moves = getPseudoMoves(testBoard, r, c, null);
        if (moves.some((m) => m.row === target.row && m.col === target.col)) {
          candidates.push({ row: r, col: c });
        }
      }
    }

    if (candidates.length > 0) {
      const choice = candidates[Math.floor(Math.random() * candidates.length)];
      return {
        row: choice.row,
        col: choice.col,
        targetRow: target.row,
        targetCol: target.col,
      };
    }
  }

  // Fallback: Circe square
  const circe = getCirceSquare(piece);
  if (!board[circe.row][circe.col]) {
    return { row: circe.row, col: circe.col };
  }

  // Final fallback: random empty square
  const empties = [];
  for (let r = 0; r < 8; r++)
    for (let c = 0; c < 8; c++) if (!board[r][c]) empties.push({ row: r, col: c });

  if (empties.length > 0) {
    return empties[Math.floor(Math.random() * empties.length)];
  }

  return null;
}
