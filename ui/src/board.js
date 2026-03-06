// ─── Board Rendering ─────────────────────────────────────────
import { FILES, getImageType } from './pieces.js';
import { isInCheck, findKing } from './moves.js';
import {
  getTierClasses,
  createTierBadge,
  isPermakillVulnerable,
  createSmokeParticles,
} from './ui-effects.js';

/**
 * Render the board into the given container element.
 *
 * @param {HTMLElement} container - the .board element
 * @param {object} gameState
 * @param {{row,col}|null} selectedSquare
 * @param {Array} legalMoves - [{row,col,isCapture,...}]
 * @param {function} onSquareClick - (row,col) => void
 * @param {string} orientation - 'white' (default) or 'black' (flipped)
 */
export function renderBoard(
  container,
  gameState,
  selectedSquare,
  legalMoves,
  onSquareClick,
  orientation = 'white'
) {
  container.innerHTML = '';

  const flipped = orientation === 'black';

  // Determine check square
  let checkSquare = null;
  if (isInCheck(gameState.board, gameState.turn)) {
    checkSquare = findKing(gameState.board, gameState.turn);
  }

  for (let displayRow = 0; displayRow < 8; displayRow++) {
    for (let displayCol = 0; displayCol < 8; displayCol++) {
      // Map display position to board coordinates
      const row = flipped ? 7 - displayRow : displayRow;
      const col = flipped ? 7 - displayCol : displayCol;

      const isLight = (row + col) % 2 === 0;
      const square = document.createElement('div');
      square.className = `square ${isLight ? 'light' : 'dark'}`;

      // Selected square highlight
      if (selectedSquare && selectedSquare.row === row && selectedSquare.col === col) {
        square.classList.add('selected');
      }

      // Check highlight
      if (checkSquare && checkSquare.row === row && checkSquare.col === col) {
        square.classList.add('in-check');
      }

      // Legal move indicators
      const legalMove = legalMoves.find((m) => m.row === row && m.col === col);
      if (legalMove) {
        if (legalMove.isCapture) {
          square.classList.add('legal-capture');
        } else {
          square.classList.add('legal-move');
        }
      }

      // Rank labels (leftmost column of display)
      if (displayCol === 0) {
        const rank = document.createElement('span');
        rank.className = 'rank-label';
        rank.textContent = 8 - row;
        square.appendChild(rank);
      }

      // File labels (bottom row of display)
      if (displayRow === 7) {
        const file = document.createElement('span');
        file.className = 'file-label';
        file.textContent = FILES[col];
        square.appendChild(file);
      }

      // Piece
      const piece = gameState.board[row][col];
      if (piece) {
        const pieceContainer = document.createElement('div');
        pieceContainer.className = 'piece-container';

        // Tier classes
        const tierClasses = getTierClasses(piece);
        for (const cls of tierClasses) {
          pieceContainer.classList.add(cls);
        }

        // Clickable cursor for own pieces
        if (piece.color === gameState.turn && gameState.status === 'playing') {
          square.classList.add('clickable');
        }

        const img = document.createElement('img');
        img.src = `/pieces/${piece.color}/${getImageType(piece)}.png`;
        img.alt = `${piece.color} ${piece.type}`;
        img.draggable = false;
        pieceContainer.appendChild(img);

        // Tier badge
        const badge = createTierBadge(piece);
        if (badge) {
          pieceContainer.appendChild(badge);
        }

        // Permakill smoke effect
        if (isPermakillVulnerable(gameState, piece)) {
          square.classList.add('permakill-vulnerable');
          pieceContainer.appendChild(createSmokeParticles());
        }

        square.appendChild(pieceContainer);
      }

      // Click handler (passes board coordinates, not display coordinates)
      square.addEventListener('click', () => onSquareClick(row, col));

      container.appendChild(square);
    }
  }
}

/**
 * Get the display index of a board square for adding CSS classes after render.
 */
export function getBoardDisplayIndex(row, col, orientation = 'white') {
  const flipped = orientation === 'black';
  const displayRow = flipped ? 7 - row : row;
  const displayCol = flipped ? 7 - col : col;
  return displayRow * 8 + displayCol;
}
