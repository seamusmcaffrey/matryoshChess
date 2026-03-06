// ─── Game State & Move Execution ─────────────────────────────
import { createStartingBoard, degradePiece, FILES } from './pieces.js';
import { getAllLegalMoves, isInCheck, cloneBoard } from './moves.js';
import { findRetaliationSquare } from './retaliation.js';

export function createGameState() {
  return {
    board: createStartingBoard(),
    turn: 'white',
    moveCount: 0,
    log: [],
    status: 'playing',
    enPassantTarget: null,
    captured: [],
    permakillTracking: [],
    history: [],
    positionHashes: [],
  };
}

function deepCloneState(state) {
  return {
    board: state.board.map((row) => row.map((cell) => (cell ? { ...cell } : null))),
    turn: state.turn,
    moveCount: state.moveCount,
    log: [...state.log],
    status: state.status,
    enPassantTarget: state.enPassantTarget
      ? { ...state.enPassantTarget }
      : null,
    captured: state.captured.map((p) => ({ ...p })),
    permakillTracking: state.permakillTracking.map((t) => ({ ...t })),
    positionHashes: [...state.positionHashes],
  };
}

export function getPositionHash(state) {
  let hash = '';
  for (let r = 0; r < 8; r++)
    for (let c = 0; c < 8; c++) {
      const p = state.board[r][c];
      hash += p ? `${p.color[0]}${p.type}${p.tier}` : '..';
    }
  hash += state.turn[0];
  if (state.enPassantTarget)
    hash += `e${state.enPassantTarget.row}${state.enPassantTarget.col}`;
  return hash;
}

/**
 * Execute a move. Returns { notation, retaliationInfo, isPermakill }.
 *
 * @param {object} state - game state (mutated)
 * @param {{row,col}} from - source square
 * @param {{row,col,isCapture?,isEnPassant?,isCastle?}} to - target square
 * @param {string} [promotionChoice] - 'Q'|'R'|'B'|'N' if promoting
 */
export function makeMove(state, from, to, promotionChoice) {
  // Save state for undo
  state.history.push(deepCloneState(state));

  const piece = state.board[from.row][from.col];
  const movingColor = piece.color;

  // Determine captured piece
  let captured = null;
  if (to.isEnPassant) {
    const capturedRow = movingColor === 'white' ? to.row + 1 : to.row - 1;
    captured = state.board[capturedRow][to.col];
    state.board[capturedRow][to.col] = null;
  } else if (state.board[to.row][to.col]) {
    captured = state.board[to.row][to.col];
  }

  let retaliationInfo = null;
  let isPermakill = false;

  // Check if this is a permakill recapture
  if (captured) {
    const trackIdx = state.permakillTracking.findIndex(
      (t) => t.pieceId === captured.id && t.capturerId === piece.id
    );
    if (trackIdx !== -1) {
      isPermakill = true;
      state.permakillTracking.splice(trackIdx, 1);
    }
  }

  // Execute castling rook movement
  if (to.isCastle) {
    const backRank = movingColor === 'white' ? 7 : 0;
    if (to.isCastle === 'kingside') {
      state.board[backRank][5] = state.board[backRank][7];
      state.board[backRank][7] = null;
      state.board[backRank][5].hasMoved = true;
    } else {
      state.board[backRank][3] = state.board[backRank][0];
      state.board[backRank][0] = null;
      state.board[backRank][3].hasMoved = true;
    }
  }

  // Move piece
  state.board[to.row][to.col] = piece;
  state.board[from.row][from.col] = null;
  piece.hasMoved = true;

  // En passant target
  state.enPassantTarget = null;
  if (piece.type === 'P' && Math.abs(to.row - from.row) === 2) {
    state.enPassantTarget = {
      row: (from.row + to.row) / 2,
      col: from.col,
    };
  }

  // Handle capture with Matryoshka rules
  if (captured) {
    if (isPermakill || piece.type === 'K') {
      // Permanent removal (permakill or king capture)
      state.captured.push({ ...captured, permakilled: true });
    } else {
      const degraded = { ...captured };
      const survives = degradePiece(degraded);

      if (!survives) {
        state.captured.push({ ...captured, permakilled: true });
      } else {
        // Find retaliation square
        const retSquare = findRetaliationSquare(state, degraded);
        if (retSquare) {
          state.board[retSquare.row][retSquare.col] = degraded;

          // Track permakill vulnerability
          // Expires after the capturer's side makes their next move
          // (2 half-moves from now: opponent moves, then capturer moves)
          state.permakillTracking.push({
            pieceId: degraded.id,
            capturerId: piece.id,
            capturerColor: movingColor,
            expiresOnMoveCount: state.moveCount + 2,
          });

          retaliationInfo = {
            piece: degraded,
            square: retSquare,
            targetRow: retSquare.targetRow,
            targetCol: retSquare.targetCol,
          };
        } else {
          state.captured.push({ ...degraded, permakilled: true });
        }
      }
    }
  }

  // Pawn promotion (track pre-promotion type for notation)
  const wasPawnBeforePromotion = piece.type === 'P';
  const promotionRow = movingColor === 'white' ? 0 : 7;
  if (piece.type === 'P' && to.row === promotionRow) {
    const choice = promotionChoice || 'Q';
    piece.type = choice;
    piece.tier = 1;
    if (piece.originalType === 'P') piece.originalType = choice;
  }

  // Switch turn
  state.turn = state.turn === 'white' ? 'black' : 'white';
  state.moveCount++;

  // Expire permakill trackings
  state.permakillTracking = state.permakillTracking.filter(
    (t) => t.expiresOnMoveCount > state.moveCount
  );

  // Check game status
  const nextMoves = getAllLegalMoves(state, state.turn);
  const inCheck = isInCheck(state.board, state.turn);
  let suffix = '';

  if (nextMoves.length === 0) {
    if (inCheck) {
      state.status = 'checkmate';
      suffix = '#';
    } else {
      state.status = 'stalemate';
    }
  } else if (inCheck) {
    suffix = '+';
  }

  // Ko / repetition detection
  const hash = getPositionHash(state);
  state.positionHashes.push(hash);
  const repetitions = state.positionHashes.filter((h) => h === hash).length;
  if (repetitions >= 3 && state.status === 'playing') {
    state.status = 'draw-repetition';
  }

  // Build notation
  let notation;
  if (to.isCastle === 'kingside') {
    notation = 'O-O';
  } else if (to.isCastle === 'queenside') {
    notation = 'O-O-O';
  } else {
    // Use pre-promotion type for notation (a promoted pawn should show as pawn move)
    const isPawnMove = wasPawnBeforePromotion;
    const prefix = isPawnMove
      ? ''
      : piece.type === 'W'
        ? 'W'
        : piece.type;
    const notationPrefix =
      captured && isPawnMove
        ? FILES[from.col]
        : prefix;
    const capStr = captured ? 'x' : '';
    const toSq = FILES[to.col] + (8 - to.row);
    notation = notationPrefix + capStr + toSq;

    if (promotionChoice || (piece.originalType !== 'P' && piece.type !== piece.originalType)) {
      // Only show promotion for actual pawn promotions
      if (promotionChoice) notation += '=' + promotionChoice;
    }
  }

  // Retaliation annotation
  if (retaliationInfo) {
    const rp = retaliationInfo.piece;
    const retSq =
      FILES[retaliationInfo.square.col] +
      (8 - retaliationInfo.square.row);
    notation += ` [${rp.type}${rp.tier > 1 ? rp.tier : ''}\u2192${retSq}]`;
  }

  notation += suffix;
  state.log.push(notation);

  return { captured, notation, retaliationInfo, isPermakill };
}

export function undoMove(state) {
  if (state.history.length === 0) return false;
  const prev = state.history.pop();
  // Restore everything except history (the pop already shortened it correctly)
  state.board = prev.board;
  state.turn = prev.turn;
  state.moveCount = prev.moveCount;
  state.log = prev.log;
  state.status = prev.status;
  state.enPassantTarget = prev.enPassantTarget;
  state.captured = prev.captured;
  state.permakillTracking = prev.permakillTracking;
  state.positionHashes = prev.positionHashes;
  // Note: state.history is NOT restored — the pop above is sufficient
  return true;
}
