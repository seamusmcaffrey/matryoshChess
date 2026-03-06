import './style.css'

// Starting position: row 0 = rank 8 (black back rank), row 7 = rank 1 (white back rank)
const BACK_RANK = ['R', 'N', 'B', 'Q', 'K', 'B', 'N', 'R']

function createStartingPosition() {
  const board = Array.from({ length: 8 }, () => Array(8).fill(null))

  // Black pieces
  for (let col = 0; col < 8; col++) {
    board[0][col] = { type: BACK_RANK[col], color: 'black', tier: 1 }
    board[1][col] = { type: 'P', color: 'black', tier: 1 }
  }

  // White pieces
  for (let col = 0; col < 8; col++) {
    board[7][col] = { type: BACK_RANK[col], color: 'white', tier: 1 }
    board[6][col] = { type: 'P', color: 'white', tier: 1 }
  }

  return board
}

function renderBoard(board) {
  const app = document.querySelector('#app')
  const files = 'abcdefgh'

  app.innerHTML = `
    <h1>Matryoshka Chess</h1>
    <div class="turn-indicator">White to move</div>
    <div class="board"></div>
  `

  const boardEl = app.querySelector('.board')

  for (let row = 0; row < 8; row++) {
    for (let col = 0; col < 8; col++) {
      const isLight = (row + col) % 2 === 0
      const square = document.createElement('div')
      square.className = `square ${isLight ? 'light' : 'dark'}`
      square.dataset.row = row
      square.dataset.col = col

      // Rank labels on a-file
      if (col === 0) {
        const rank = document.createElement('span')
        rank.className = 'rank-label'
        rank.textContent = 8 - row
        square.appendChild(rank)
      }

      // File labels on rank 1
      if (row === 7) {
        const file = document.createElement('span')
        file.className = 'file-label'
        file.textContent = files[col]
        square.appendChild(file)
      }

      // Piece
      const piece = board[row][col]
      if (piece) {
        const img = document.createElement('img')
        img.src = `/pieces/${piece.color}/${piece.type}.svg`
        img.alt = `${piece.color} ${piece.type}`
        square.appendChild(img)
      }

      boardEl.appendChild(square)
    }
  }
}

// Boot
const board = createStartingPosition()
renderBoard(board)
