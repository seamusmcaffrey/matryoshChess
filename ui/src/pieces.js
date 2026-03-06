// ─── Piece Definitions & Tier System ─────────────────────────

export const FILES = 'abcdefgh';

export const PIECE_VALUES = { K: 100, Q: 9, R: 5, B: 3, N: 3, W: 1, P: 1 };

export const TIER_NAMES = { 1: 'Full', 2: 'Damaged', 3: 'Crippled' };

const BACK_RANK = ['R', 'N', 'B', 'Q', 'K', 'B', 'N', 'R'];

let pieceIdCounter = 0;

export function resetPieceIds() {
  pieceIdCounter = 0;
}

export function makePiece(type, color, startCol, startRow) {
  return {
    type,
    color,
    tier: 1,
    id: `${color[0]}-${type}-${startCol}-${pieceIdCounter++}`,
    originalType: type,
    startCol,
    startRow,
    hasMoved: false,
  };
}

export function createStartingBoard() {
  resetPieceIds();
  const board = Array.from({ length: 8 }, () => Array(8).fill(null));
  for (let col = 0; col < 8; col++) {
    board[0][col] = makePiece(BACK_RANK[col], 'black', col, 0);
    board[1][col] = makePiece('P', 'black', col, 1);
    board[6][col] = makePiece('P', 'white', col, 6);
    board[7][col] = makePiece(BACK_RANK[col], 'white', col, 7);
  }
  return board;
}

/**
 * Degrade a piece after capture. Mutates the piece.
 * Returns true if piece survives, false if dead.
 *
 * Degradation ladder:
 *   Q/R/B: tier 1→2→3→collapse to Pawn
 *   N: → Wazir → Pawn
 *   P: dead
 *   K: never degraded (king permakill handled by caller)
 */
export function degradePiece(piece) {
  if (piece.type === 'P') return false;
  if (piece.type === 'K') return false;

  if (piece.type === 'N') {
    piece.type = 'W';
    piece.tier = 1;
    return true;
  }

  if (piece.type === 'W') {
    piece.type = 'P';
    piece.tier = 1;
    return true;
  }

  // Q, R, B: tier 1→2→3→Pawn
  if (piece.tier < 3) {
    piece.tier++;
    return true;
  }

  // Tier 3 → collapse to Pawn
  piece.type = 'P';
  piece.tier = 1;
  return true;
}

export function getRangeLimit(piece) {
  if (piece.tier === 2) return 5;
  if (piece.tier === 3) return 1;
  return 8;
}

/** Get the image filename type for a piece (Wazir uses Knight image) */
export function getImageType(piece) {
  if (piece.type === 'W') return 'N';
  return piece.type;
}
