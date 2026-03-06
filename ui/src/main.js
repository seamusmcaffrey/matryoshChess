import './style.css';
import { createGameState, makeMove, undoMove } from './game.js';
import { getLegalMoves, getAllLegalMoves, isInCheck } from './moves.js';
import { FILES, getImageType, TIER_NAMES } from './pieces.js';
import { renderBoard, getBoardDisplayIndex } from './board.js';

// ─── App State ──────────────────────────────────────────────
let mode = 'lobby'; // 'lobby' | 'waiting' | 'local' | 'online'
let gameState = null;
let selectedSquare = null;
let currentLegalMoves = [];
let autoPlayInterval = null;
let pendingPromotion = null;
let lastRetaliationInfo = null;

// ─── Networking State ───────────────────────────────────────
let ws = null;
let playerColor = null; // 'white' | 'black' | null (local = null)
let joinCode = '1111';
let connectionError = null;

function getOrientation() {
  if (mode === 'online' && playerColor === 'black') return 'black';
  return 'white';
}

function isMyTurn() {
  if (mode !== 'online') return true; // local mode, always your turn
  return gameState.turn === playerColor;
}

// ─── WebSocket Connection ───────────────────────────────────
function connectToServer(code) {
  const host = window.location.hostname || 'localhost';
  const url = `ws://${host}:3001`;

  connectionError = null;
  ws = new WebSocket(url);

  ws.onopen = () => {
    ws.send(JSON.stringify({ type: 'join', code }));
  };

  ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);

    switch (msg.type) {
      case 'assigned':
        playerColor = msg.color;
        joinCode = msg.code;
        break;

      case 'waiting':
        mode = 'waiting';
        renderLobby();
        break;

      case 'start':
        mode = 'online';
        gameState = createGameState();
        selectedSquare = null;
        currentLegalMoves = [];
        render();
        break;

      case 'move':
        // Opponent made a move — apply it locally
        applyRemoteMove(msg);
        break;

      case 'undo':
        if (undoMove(gameState)) {
          selectedSquare = null;
          currentLegalMoves = [];
          pendingPromotion = null;
          lastRetaliationInfo = null;
          render();
        }
        break;

      case 'resign':
        gameState.status = 'resigned';
        gameState.resignedBy = playerColor === 'white' ? 'black' : 'white';
        selectedSquare = null;
        currentLegalMoves = [];
        render();
        break;

      case 'reset':
        gameState = createGameState();
        selectedSquare = null;
        currentLegalMoves = [];
        pendingPromotion = null;
        lastRetaliationInfo = null;
        render();
        break;

      case 'opponent-disconnected':
        connectionError = 'Opponent disconnected';
        render();
        break;

      case 'error':
        connectionError = msg.message;
        mode = 'lobby';
        renderLobby();
        break;
    }
  };

  ws.onclose = () => {
    if (mode === 'online' || mode === 'waiting') {
      connectionError = 'Connection lost';
      renderLobby();
    }
  };

  ws.onerror = () => {
    connectionError = 'Could not connect to server. Is it running?';
    mode = 'lobby';
    ws = null;
    renderLobby();
  };
}

function applyRemoteMove(msg) {
  const from = msg.from;
  const to = msg.to;
  const promotionChoice = msg.promotionChoice;

  const result = makeMove(gameState, from, to, promotionChoice);
  lastRetaliationInfo = result.retaliationInfo;
  selectedSquare = null;
  currentLegalMoves = [];
  render();

  if (lastRetaliationInfo) {
    setTimeout(() => {
      lastRetaliationInfo = null;
      render();
    }, 2000);
  }
}

function sendMove(from, to, promotionChoice) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(
      JSON.stringify({
        type: 'move',
        from: { row: from.row, col: from.col },
        to: {
          row: to.row,
          col: to.col,
          isCapture: to.isCapture,
          isEnPassant: to.isEnPassant,
          isCastle: to.isCastle,
        },
        promotionChoice,
      })
    );
  }
}

function disconnectFromServer() {
  if (ws) {
    ws.close();
    ws = null;
  }
  playerColor = null;
  connectionError = null;
}

// ─── Click Handling ─────────────────────────────────────────
function onSquareClick(row, col) {
  if (gameState.status !== 'playing') return;
  if (pendingPromotion) return;
  if (autoPlayInterval) return;
  if (!isMyTurn()) return;

  const piece = gameState.board[row][col];

  if (selectedSquare) {
    const move = currentLegalMoves.find(
      (m) => m.row === row && m.col === col
    );
    if (move) {
      executePlayerMove(selectedSquare, move);
      return;
    }

    if (piece && piece.color === gameState.turn) {
      selectedSquare = { row, col };
      currentLegalMoves = getLegalMoves(gameState, row, col);
      render();
      return;
    }

    selectedSquare = null;
    currentLegalMoves = [];
    render();
    return;
  }

  if (piece && piece.color === gameState.turn) {
    selectedSquare = { row, col };
    currentLegalMoves = getLegalMoves(gameState, row, col);
    render();
  }
}

function executePlayerMove(from, to) {
  const piece = gameState.board[from.row][from.col];
  const promotionRow = piece.color === 'white' ? 0 : 7;

  if (piece.type === 'P' && to.row === promotionRow) {
    pendingPromotion = { from, to };
    selectedSquare = null;
    currentLegalMoves = [];
    render();
    return;
  }

  const result = makeMove(gameState, from, to);
  if (mode === 'online') sendMove(from, to);

  lastRetaliationInfo = result.retaliationInfo;
  selectedSquare = null;
  currentLegalMoves = [];
  render();

  if (lastRetaliationInfo) {
    setTimeout(() => {
      lastRetaliationInfo = null;
      render();
    }, 2000);
  }
}

function onPromotionChoice(choice) {
  if (!pendingPromotion) return;
  const { from, to } = pendingPromotion;
  pendingPromotion = null;

  const result = makeMove(gameState, from, to, choice);
  if (mode === 'online') sendMove(from, to, choice);

  lastRetaliationInfo = result.retaliationInfo;
  render();

  if (lastRetaliationInfo) {
    setTimeout(() => {
      lastRetaliationInfo = null;
      render();
    }, 2000);
  }
}

// ─── Auto-Play ──────────────────────────────────────────────
function autoPlayStep() {
  if (!gameState || gameState.status !== 'playing') {
    stopAutoPlay();
    return;
  }
  const moves = getAllLegalMoves(gameState, gameState.turn);
  if (moves.length === 0) {
    stopAutoPlay();
    return;
  }

  const captures = moves.filter(
    (m) => gameState.board[m.to.row][m.to.col]
  );
  const pool =
    captures.length > 0 && Math.random() < 0.4 ? captures : moves;
  const move = pool[Math.floor(Math.random() * pool.length)];

  const piece = gameState.board[move.from.row][move.from.col];
  const promotionRow = piece.color === 'white' ? 0 : 7;
  const promoChoice =
    piece.type === 'P' && move.to.row === promotionRow ? 'Q' : undefined;

  const result = makeMove(gameState, move.from, move.to, promoChoice);
  lastRetaliationInfo = result.retaliationInfo;
  render();

  if (lastRetaliationInfo) {
    setTimeout(() => {
      lastRetaliationInfo = null;
      if (autoPlayInterval) render();
    }, 500);
  }
}

function startAutoPlay() {
  if (autoPlayInterval) return;
  if (mode === 'online') return;
  if (!gameState || gameState.status !== 'playing') {
    gameState = createGameState();
  }
  selectedSquare = null;
  currentLegalMoves = [];
  pendingPromotion = null;
  autoPlayInterval = setInterval(autoPlayStep, 700);
  render();
}

function stopAutoPlay() {
  if (autoPlayInterval) {
    clearInterval(autoPlayInterval);
    autoPlayInterval = null;
  }
  render();
}

function resetGame() {
  stopAutoPlay();
  gameState = createGameState();
  selectedSquare = null;
  currentLegalMoves = [];
  pendingPromotion = null;
  lastRetaliationInfo = null;
  if (mode === 'online' && ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: 'reset' }));
  }
  render();
}

function handleUndo() {
  if (autoPlayInterval) return;
  if (undoMove(gameState)) {
    selectedSquare = null;
    currentLegalMoves = [];
    pendingPromotion = null;
    lastRetaliationInfo = null;
    if (mode === 'online' && ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'undo' }));
    }
    render();
  }
}

function handleResign() {
  if (autoPlayInterval) return;
  if (gameState.status !== 'playing') return;
  const loser = mode === 'online' ? playerColor : gameState.turn;
  gameState.status = 'resigned';
  gameState.resignedBy = loser;
  if (mode === 'online' && ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: 'resign' }));
  }
  render();
}

function goToLobby() {
  stopAutoPlay();
  disconnectFromServer();
  mode = 'lobby';
  gameState = null;
  selectedSquare = null;
  currentLegalMoves = [];
  pendingPromotion = null;
  lastRetaliationInfo = null;
  renderLobby();
}

// ─── Status Text ────────────────────────────────────────────
function getStatusText() {
  if (gameState.status === 'checkmate') {
    const winner = gameState.turn === 'white' ? 'Black' : 'White';
    return `Checkmate! ${winner} wins`;
  }
  if (gameState.status === 'stalemate') return 'Stalemate — Draw';
  if (gameState.status === 'draw-repetition') return 'Draw — Threefold Repetition';
  if (gameState.status === 'resigned') {
    const winner = gameState.resignedBy === 'white' ? 'Black' : 'White';
    return `${gameState.resignedBy === 'white' ? 'White' : 'Black'} resigned — ${winner} wins`;
  }
  const turn = gameState.turn === 'white' ? 'White' : 'Black';
  const check = isInCheck(gameState.board, gameState.turn) ? ' (Check!)' : '';
  let yourTurn = '';
  if (mode === 'online') {
    yourTurn = isMyTurn() ? ' — Your turn' : ' — Waiting...';
  }
  return `${turn} to move${check}${yourTurn}`;
}

function getStatusClass() {
  if (gameState.status === 'checkmate' || gameState.status === 'resigned')
    return 'status-gameover';
  if (gameState.status === 'stalemate' || gameState.status === 'draw-repetition')
    return 'status-draw';
  if (isInCheck(gameState.board, gameState.turn)) return 'status-check';
  return '';
}

// ─── Move Log ───────────────────────────────────────────────
function formatMoveLog(log) {
  if (log.length === 0)
    return '<span class="empty-log">Click a piece or press Auto Play...</span>';
  let html = '';
  for (let i = 0; i < log.length; i += 2) {
    const moveNum = Math.floor(i / 2) + 1;
    const white = log[i];
    const black = log[i + 1] || '';
    const isLast = i >= log.length - 2;
    html += `<div class="move-pair ${isLast ? 'latest' : ''}">`;
    html += `<span class="move-num">${moveNum}.</span> `;
    html += `<span class="move-white">${white}</span> `;
    html += `<span class="move-black">${black}</span>`;
    html += `</div>`;
  }
  return html;
}

// ─── Captured Pieces Display ────────────────────────────────
function renderCapturedPieces(captured, color) {
  const pieces = captured.filter((p) => p.color === color);
  if (pieces.length === 0) return '';

  return pieces
    .map((p) => {
      const imgType = p.type === 'W' ? 'N' : (p.originalType || p.type);
      return `<img class="captured-piece" src="/pieces/${p.color}/${imgType}.png" alt="${p.color} ${p.type}" title="${p.originalType || p.type} (removed)">`;
    })
    .join('');
}

// ─── Promotion Dialog ───────────────────────────────────────
function renderPromotionDialog() {
  if (!pendingPromotion) return '';
  const piece = gameState.board[pendingPromotion.from.row][pendingPromotion.from.col];
  const color = piece.color;

  return `
    <div class="promotion-overlay">
      <div class="promotion-dialog">
        <div class="promotion-title">Promote to:</div>
        <div class="promotion-options">
          ${['Q', 'R', 'B', 'N']
            .map(
              (type) => `
            <button class="promotion-choice" data-choice="${type}">
              <img src="/pieces/${color}/${type}.png" alt="${type}">
            </button>
          `
            )
            .join('')}
        </div>
      </div>
    </div>
  `;
}

// ─── Lobby Render ───────────────────────────────────────────
function renderLobby() {
  const app = document.querySelector('#app');

  const errorHtml = connectionError
    ? `<div class="lobby-error">${connectionError}</div>`
    : '';

  if (mode === 'waiting') {
    const networkAddr = window.location.hostname || 'localhost';
    const port = window.location.port || '5173';
    app.innerHTML = `
      <div class="lobby">
        <h1>Matryoshka Chess</h1>
        <div class="lobby-card">
          <div class="lobby-waiting">
            <div class="waiting-spinner"></div>
            <div class="waiting-text">Waiting for opponent...</div>
            <div class="join-code-display">
              <div class="join-code-label">Game Code</div>
              <div class="join-code-value">${joinCode}</div>
            </div>
            <div class="connect-info">
              Other player: open <strong>http://${networkAddr}:${port}</strong> and join with code <strong>${joinCode}</strong>
            </div>
            <div class="your-color">You are playing as <strong>White</strong></div>
          </div>
          <button class="btn btn-secondary" id="btn-cancel">Cancel</button>
        </div>
      </div>
    `;
    document.getElementById('btn-cancel')?.addEventListener('click', goToLobby);
    return;
  }

  app.innerHTML = `
    <div class="lobby">
      <h1>Matryoshka Chess</h1>
      ${errorHtml}
      <div class="lobby-card">
        <div class="lobby-section">
          <button class="btn btn-primary btn-large" id="btn-local">Local (Hot Seat)</button>
          <div class="lobby-hint">Two players, one screen</div>
        </div>
        <div class="lobby-divider"><span>or play online</span></div>
        <div class="lobby-section">
          <div class="join-row">
            <input type="text" id="join-code" value="${joinCode}" maxlength="8"
              placeholder="Game code" class="code-input" />
            <button class="btn btn-primary" id="btn-join">Join Game</button>
          </div>
          <div class="lobby-hint">First player creates the room. Second player joins it.</div>
        </div>
      </div>
      <div class="lobby-rules">
        <p>Captured pieces <strong>degrade</strong> and redeploy. King captures = permakill.</p>
      </div>
    </div>
  `;

  document.getElementById('btn-local')?.addEventListener('click', () => {
    mode = 'local';
    gameState = createGameState();
    render();
  });

  document.getElementById('btn-join')?.addEventListener('click', () => {
    const code = document.getElementById('join-code')?.value?.trim();
    if (!code) return;
    joinCode = code;
    connectToServer(code);
  });

  // Allow Enter key to join
  document.getElementById('join-code')?.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      document.getElementById('btn-join')?.click();
    }
  });
}

// ─── Main Game Render ───────────────────────────────────────
function render() {
  if (!gameState) {
    renderLobby();
    return;
  }

  const app = document.querySelector('#app');
  const orientation = getOrientation();

  // For flipped board, swap which captured row is on top
  const topCaptured = orientation === 'black'
    ? renderCapturedPieces(gameState.captured, 'white')
    : renderCapturedPieces(gameState.captured, 'black');
  const bottomCaptured = orientation === 'black'
    ? renderCapturedPieces(gameState.captured, 'black')
    : renderCapturedPieces(gameState.captured, 'white');

  const gameOverOverlay =
    gameState.status !== 'playing'
      ? `<div class="gameover-overlay">
          <div class="gameover-message">${getStatusText()}</div>
          <button class="btn" id="btn-newgame-overlay">New Game</button>
        </div>`
      : '';

  const disconnectedBanner = connectionError
    ? `<div class="disconnect-banner">${connectionError}</div>`
    : '';

  const onlineInfo = mode === 'online'
    ? `<div class="online-badge">Online — You are <strong>${playerColor === 'white' ? 'White' : 'Black'}</strong></div>`
    : '';

  const autoPlayBtn = mode !== 'online'
    ? `<button class="btn" id="btn-auto">${autoPlayInterval ? 'Pause' : 'Auto Play'}</button>`
    : '';

  app.innerHTML = `
    <h1>Matryoshka Chess</h1>
    ${onlineInfo}
    ${disconnectedBanner}
    <div class="main-layout">
      <div class="board-area">
        <div class="captured-row">${topCaptured}</div>
        <div class="turn-indicator ${getStatusClass()}">${getStatusText()}</div>
        <div class="board-wrapper">
          <div class="board"></div>
          ${gameOverOverlay}
        </div>
        <div class="captured-row">${bottomCaptured}</div>
        <div class="controls">
          ${autoPlayBtn}
          <button class="btn" id="btn-undo" ${gameState.history.length === 0 || autoPlayInterval ? 'disabled' : ''}>Undo</button>
          <button class="btn" id="btn-resign" ${gameState.status !== 'playing' || autoPlayInterval ? 'disabled' : ''}>Resign</button>
          <button class="btn" id="btn-reset">New Game</button>
          <button class="btn btn-secondary" id="btn-lobby">Lobby</button>
        </div>
      </div>
      <div class="sidebar">
        <div class="move-log">
          <h2>Move History</h2>
          <div class="move-log-content">${formatMoveLog(gameState.log)}</div>
        </div>
        <div class="info-panel">
          <h2>Matryoshka Rules</h2>
          <div class="rules-summary">
            <p>Captured pieces <strong>degrade</strong> and redeploy threatening your best piece.</p>
            <p>King captures = permanent kill.</p>
            <p>Recapture within 1 move = permakill.</p>
          </div>
        </div>
      </div>
    </div>
    ${renderPromotionDialog()}
  `;

  // Render the board with orientation
  const boardEl = document.querySelector('.board');
  if (boardEl) {
    renderBoard(
      boardEl,
      gameState,
      selectedSquare,
      currentLegalMoves,
      onSquareClick,
      orientation
    );

    // Add retaliation highlight using display-index mapping
    if (lastRetaliationInfo) {
      const { square } = lastRetaliationInfo;
      const idx = getBoardDisplayIndex(square.row, square.col, orientation);
      const squareEl = boardEl.children[idx];
      if (squareEl) squareEl.classList.add('retaliation-placed');

      if (lastRetaliationInfo.targetRow !== undefined) {
        const targetIdx = getBoardDisplayIndex(
          lastRetaliationInfo.targetRow,
          lastRetaliationInfo.targetCol,
          orientation
        );
        const targetEl = boardEl.children[targetIdx];
        if (targetEl) targetEl.classList.add('retaliation-target');
      }
    }
  }

  // Scroll move log to bottom
  const logContent = document.querySelector('.move-log-content');
  if (logContent) logContent.scrollTop = logContent.scrollHeight;

  // Event listeners
  document
    .getElementById('btn-auto')
    ?.addEventListener('click', () => {
      if (autoPlayInterval) stopAutoPlay();
      else startAutoPlay();
    });
  document.getElementById('btn-reset')?.addEventListener('click', resetGame);
  document.getElementById('btn-undo')?.addEventListener('click', handleUndo);
  document
    .getElementById('btn-resign')
    ?.addEventListener('click', handleResign);
  document
    .getElementById('btn-newgame-overlay')
    ?.addEventListener('click', resetGame);
  document.getElementById('btn-lobby')?.addEventListener('click', goToLobby);

  // Promotion dialog handlers
  document.querySelectorAll('.promotion-choice').forEach((btn) => {
    btn.addEventListener('click', () => {
      onPromotionChoice(btn.dataset.choice);
    });
  });
}

// ─── Boot ───────────────────────────────────────────────────
renderLobby();
