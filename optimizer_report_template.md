# Variant Optimization Report Template

## Top 10 Variants
- Include: draw%, win split, plies, captures/100, checks/100, novelty KL, interestingness
- Explain: why each is interesting and what rule levers likely caused it

## Pareto Frontier
- Objectives:
  - Minimize draw%
  - Minimize mean plies
  - Minimize color imbalance
  - Maximize novelty KL
- Include frontier table and commentary on tradeoffs

## Draw Forensics
- Top draw signatures and counts
- Typical ending boards (ASCII snapshots)
- Termination reason breakdown
- Last-20-move patterns in representative draws

## Core Questions
1. Why are draws high? What do drawn boards look like?
2. Which levers reduce draws without going fully capture-chaotic?
3. Which variants feel most differentiated from chess while staying fair/stable?

## Recommendations
1. Three variants to iterate next
2. Specific mutations to test next pass
3. Estimated sample size needed for confidence tightening
