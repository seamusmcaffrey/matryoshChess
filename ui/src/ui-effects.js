// ─── UI Effects: Tier Badges, Smoke, Animations ─────────────

/**
 * Get CSS classes for a piece based on its tier/type.
 */
export function getTierClasses(piece) {
  const classes = [];

  if (piece.type === 'W') {
    classes.push('tier-wazir');
  } else if (piece.tier === 2) {
    classes.push('tier-2');
  } else if (piece.tier === 3) {
    classes.push('tier-3');
  }

  // Collapsed pawn (was originally something else)
  if (piece.type === 'P' && piece.originalType !== 'P') {
    classes.push('collapsed-pawn');
  }

  return classes;
}

/**
 * Create a tier badge element for degraded pieces.
 * Returns null for tier-1 non-collapsed pieces.
 */
export function createTierBadge(piece) {
  // Collapsed pawn: show original type badge
  if (piece.type === 'P' && piece.originalType !== 'P') {
    const badge = document.createElement('span');
    badge.className = 'tier-badge collapsed-badge';
    badge.textContent = piece.originalType === 'W' ? 'N' : piece.originalType;
    return badge;
  }

  // Wazir badge
  if (piece.type === 'W') {
    const badge = document.createElement('span');
    badge.className = 'tier-badge wazir-badge';
    badge.textContent = 'W';
    return badge;
  }

  // Tier 2 or 3 badge
  if (piece.tier > 1) {
    const badge = document.createElement('span');
    badge.className = 'tier-badge';
    const romanNumerals = { 2: 'II', 3: 'III' };
    badge.textContent = romanNumerals[piece.tier];
    return badge;
  }

  return null;
}

/**
 * Check if a piece is permakill-vulnerable.
 */
export function isPermakillVulnerable(gameState, piece) {
  return gameState.permakillTracking.some((t) => t.pieceId === piece.id);
}

/**
 * Create smoke particle elements for permakill-vulnerable pieces.
 */
export function createSmokeParticles() {
  const container = document.createElement('div');
  container.className = 'smoke-container';

  for (let i = 0; i < 4; i++) {
    const particle = document.createElement('div');
    particle.className = 'smoke-particle';
    particle.style.left = `${20 + Math.random() * 40}%`;
    particle.style.animationDelay = `${i * 0.4}s`;
    particle.style.animationDuration = `${1.5 + Math.random() * 0.5}s`;
    container.appendChild(particle);
  }

  return container;
}

/**
 * Get the retaliation target square(s) from current tracking.
 */
export function getRetaliationTargets(gameState) {
  const targets = [];
  for (const t of gameState.permakillTracking) {
    // Find the vulnerable piece on the board
    for (let r = 0; r < 8; r++)
      for (let c = 0; c < 8; c++) {
        const p = gameState.board[r][c];
        if (p && p.id === t.pieceId) {
          targets.push({ row: r, col: c, pieceId: t.pieceId });
        }
      }
  }
  return targets;
}
