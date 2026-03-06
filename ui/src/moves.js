// ─── Move Generation ─────────────────────────────────────────
import { getRangeLimit } from './pieces.js';

function inBounds(r, c) {
  return r >= 0 && r < 8 && c >= 0 && c < 8;
}

export function cloneBoard(board) {
  return board.map(row => row.map(cell => (cell ? { ...cell } : null)));
}

/**
 * Raw pseudo-legal moves for a piece (no check filtering).
 * Returns [{row, col, isCapture, isEnPassant?}]
 */
export function getPseudoMoves(board, row, col, enPassantTarget) {
  const piece = board[row][col];
  if (!piece) return [];
  const moves = [];
  const color = piece.color;
  const enemy = color === 'white' ? 'black' : 'white';
  const dir = color === 'white' ? -1 : 1;
  const range = getRangeLimit(piece);

  function addSliding(dr, dc) {
    for (let i = 1; i <= range; i++) {
      const r = row + dr * i,
        c = col + dc * i;
      if (!inBounds(r, c)) break;
      if (board[r][c]) {
        if (board[r][c].color === enemy)
          moves.push({ row: r, col: c, isCapture: true });
        break;
      }
      moves.push({ row: r, col: c, isCapture: false });
    }
  }

  switch (piece.type) {
    case 'P': {
      const f1 = row + dir;
      if (inBounds(f1, col) && !board[f1][col]) {
        moves.push({ row: f1, col, isCapture: false });
        const startRow = color === 'white' ? 6 : 1;
        const f2 = row + dir * 2;
        // Only original pawns get the double-move (not collapsed pieces)
        if (row === startRow && piece.originalType === 'P' && inBounds(f2, col) && !board[f2][col]) {
          moves.push({ row: f2, col, isCapture: false });
        }
      }
      for (const dc of [-1, 1]) {
        const r = row + dir,
          c = col + dc;
        if (!inBounds(r, c)) continue;
        if (board[r][c] && board[r][c].color === enemy) {
          moves.push({ row: r, col: c, isCapture: true });
        }
        if (
          enPassantTarget &&
          enPassantTarget.row === r &&
          enPassantTarget.col === c
        ) {
          moves.push({ row: r, col: c, isCapture: true, isEnPassant: true });
        }
      }
      break;
    }
    case 'N':
      for (const [dr, dc] of [
        [-2, -1],
        [-2, 1],
        [-1, -2],
        [-1, 2],
        [1, -2],
        [1, 2],
        [2, -1],
        [2, 1],
      ]) {
        const r = row + dr,
          c = col + dc;
        if (
          inBounds(r, c) &&
          (!board[r][c] || board[r][c].color === enemy)
        ) {
          moves.push({ row: r, col: c, isCapture: !!board[r][c] });
        }
      }
      break;
    case 'W':
      // Wazir: 1 square orthogonal
      for (const [dr, dc] of [
        [-1, 0],
        [1, 0],
        [0, -1],
        [0, 1],
      ]) {
        const r = row + dr,
          c = col + dc;
        if (
          inBounds(r, c) &&
          (!board[r][c] || board[r][c].color === enemy)
        ) {
          moves.push({ row: r, col: c, isCapture: !!board[r][c] });
        }
      }
      break;
    case 'B':
      for (const [dr, dc] of [
        [-1, -1],
        [-1, 1],
        [1, -1],
        [1, 1],
      ])
        addSliding(dr, dc);
      break;
    case 'R':
      for (const [dr, dc] of [
        [-1, 0],
        [1, 0],
        [0, -1],
        [0, 1],
      ])
        addSliding(dr, dc);
      break;
    case 'Q':
      for (const [dr, dc] of [
        [-1, -1],
        [-1, 0],
        [-1, 1],
        [0, -1],
        [0, 1],
        [1, -1],
        [1, 0],
        [1, 1],
      ])
        addSliding(dr, dc);
      break;
    case 'K':
      for (const [dr, dc] of [
        [-1, -1],
        [-1, 0],
        [-1, 1],
        [0, -1],
        [0, 1],
        [1, -1],
        [1, 0],
        [1, 1],
      ]) {
        const r = row + dr,
          c = col + dc;
        if (
          inBounds(r, c) &&
          (!board[r][c] || board[r][c].color === enemy)
        ) {
          moves.push({ row: r, col: c, isCapture: !!board[r][c] });
        }
      }
      break;
  }
  return moves;
}

export function findKing(board, color) {
  for (let r = 0; r < 8; r++)
    for (let c = 0; c < 8; c++)
      if (board[r][c] && board[r][c].type === 'K' && board[r][c].color === color)
        return { row: r, col: c };
  return null;
}

export function isSquareAttacked(board, row, col, byColor) {
  for (let r = 0; r < 8; r++)
    for (let c = 0; c < 8; c++)
      if (board[r][c] && board[r][c].color === byColor) {
        const moves = getPseudoMoves(board, r, c, null);
        if (moves.some((m) => m.row === row && m.col === col)) return true;
      }
  return false;
}

export function isInCheck(board, color) {
  const king = findKing(board, color);
  if (!king) return false;
  const enemy = color === 'white' ? 'black' : 'white';
  return isSquareAttacked(board, king.row, king.col, enemy);
}

/**
 * Legal moves for a piece, including castling and en passant.
 * Filters out moves that leave own king in check.
 */
export function getLegalMoves(gameState, row, col) {
  const piece = gameState.board[row][col];
  if (!piece) return [];
  const pseudo = getPseudoMoves(
    gameState.board,
    row,
    col,
    gameState.enPassantTarget
  );
  const legal = [];

  for (const m of pseudo) {
    const test = cloneBoard(gameState.board);

    // En passant: remove the captured pawn
    if (m.isEnPassant) {
      const capturedRow = piece.color === 'white' ? m.row + 1 : m.row - 1;
      test[capturedRow][m.col] = null;
    }

    test[m.row][m.col] = test[row][col];
    test[row][col] = null;

    if (!isInCheck(test, piece.color)) {
      legal.push(m);
    }
  }

  // Castling
  if (piece.type === 'K' && !piece.hasMoved) {
    const backRank = piece.color === 'white' ? 7 : 0;
    if (row === backRank && col === 4) {
      const enemy = piece.color === 'white' ? 'black' : 'white';

      if (!isInCheck(gameState.board, piece.color)) {
        // Kingside
        const ksRook = gameState.board[backRank][7];
        if (
          ksRook &&
          ksRook.type === 'R' &&
          !ksRook.hasMoved &&
          ksRook.color === piece.color
        ) {
          if (!gameState.board[backRank][5] && !gameState.board[backRank][6]) {
            if (
              !isSquareAttacked(gameState.board, backRank, 5, enemy) &&
              !isSquareAttacked(gameState.board, backRank, 6, enemy)
            ) {
              legal.push({
                row: backRank,
                col: 6,
                isCapture: false,
                isCastle: 'kingside',
              });
            }
          }
        }

        // Queenside
        const qsRook = gameState.board[backRank][0];
        if (
          qsRook &&
          qsRook.type === 'R' &&
          !qsRook.hasMoved &&
          qsRook.color === piece.color
        ) {
          if (
            !gameState.board[backRank][1] &&
            !gameState.board[backRank][2] &&
            !gameState.board[backRank][3]
          ) {
            if (
              !isSquareAttacked(gameState.board, backRank, 2, enemy) &&
              !isSquareAttacked(gameState.board, backRank, 3, enemy)
            ) {
              legal.push({
                row: backRank,
                col: 2,
                isCapture: false,
                isCastle: 'queenside',
              });
            }
          }
        }
      }
    }
  }

  return legal;
}

export function getAllLegalMoves(gameState, color) {
  const all = [];
  for (let r = 0; r < 8; r++)
    for (let c = 0; c < 8; c++)
      if (gameState.board[r][c] && gameState.board[r][c].color === color) {
        const moves = getLegalMoves(gameState, r, c);
        for (const m of moves) all.push({ from: { row: r, col: c }, to: m });
      }
  return all;
}
