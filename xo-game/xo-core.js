/* XO core: board rules and the computer player. No DOM access, so it runs
 * in the browser (as window.XO) and under Node (module.exports) for tests.
 *
 * A board is an array of 9 cells, each 'X', 'O' or null, indexed:
 *   0 | 1 | 2
 *   3 | 4 | 5
 *   6 | 7 | 8
 */
(function (root) {
  'use strict';

  var LINES = [
    [0, 1, 2], [3, 4, 5], [6, 7, 8], // rows
    [0, 3, 6], [1, 4, 7], [2, 5, 8], // columns
    [0, 4, 8], [2, 4, 6]             // diagonals
  ];

  function emptyBoard() {
    return [null, null, null, null, null, null, null, null, null];
  }

  function other(mark) {
    return mark === 'X' ? 'O' : 'X';
  }

  function freeCells(board) {
    var out = [];
    for (var i = 0; i < 9; i++) if (!board[i]) out.push(i);
    return out;
  }

  // Returns {winner: 'X'|'O', line: [a,b,c]} or {winner: 'draw'} or null.
  function result(board) {
    for (var i = 0; i < LINES.length; i++) {
      var l = LINES[i];
      var m = board[l[0]];
      if (m && m === board[l[1]] && m === board[l[2]]) {
        return { winner: m, line: l.slice() };
      }
    }
    return freeCells(board).length === 0 ? { winner: 'draw', line: null } : null;
  }

  // Minimax with alpha-beta. Scores favour quicker wins and slower losses,
  // so the computer finishes a won game instead of toying with it.
  function minimax(board, toMove, me, depth, alpha, beta) {
    var r = result(board);
    if (r) {
      if (r.winner === 'draw') return 0;
      return r.winner === me ? 10 - depth : depth - 10;
    }
    var cells = freeCells(board);
    var maximizing = toMove === me;
    var best = maximizing ? -Infinity : Infinity;
    for (var i = 0; i < cells.length; i++) {
      board[cells[i]] = toMove;
      var s = minimax(board, other(toMove), me, depth + 1, alpha, beta);
      board[cells[i]] = null;
      if (maximizing) {
        if (s > best) best = s;
        if (best > alpha) alpha = best;
      } else {
        if (s < best) best = s;
        if (best < beta) beta = best;
      }
      if (beta <= alpha) break;
    }
    return best;
  }

  // All moves tied for the best minimax score.
  function bestMoves(board, me) {
    var cells = freeCells(board);
    var top = -Infinity;
    var moves = [];
    for (var i = 0; i < cells.length; i++) {
      var b = board.slice();
      b[cells[i]] = me;
      var s = minimax(b, other(me), me, 1, -Infinity, Infinity);
      if (s > top) { top = s; moves = [cells[i]]; }
      else if (s === top) moves.push(cells[i]);
    }
    return moves;
  }

  // A cell that completes three-in-a-row for `mark`, or -1.
  function finishingMove(board, mark) {
    var cells = freeCells(board);
    for (var i = 0; i < cells.length; i++) {
      var b = board.slice();
      b[cells[i]] = mark;
      var r = result(b);
      if (r && r.winner === mark) return cells[i];
    }
    return -1;
  }

  function pick(list, rand) {
    return list[Math.floor(rand() * list.length)];
  }

  /* Choose the computer's move.
   *   easy   - takes a win if it sees one, otherwise plays anywhere
   *   medium - wins, blocks, otherwise plays perfectly about half the time
   *   hard   - perfect play; it can't be beaten, only drawn
   * `rand` is injectable so tests can make the choice deterministic.
   */
  function computerMove(board, me, level, rand) {
    rand = rand || Math.random;
    var cells = freeCells(board);
    if (!cells.length) return -1;

    if (level === 'easy') {
      var win = finishingMove(board, me);
      if (win >= 0 && rand() < 0.6) return win;
      return pick(cells, rand);
    }

    if (level === 'medium') {
      var w = finishingMove(board, me);
      if (w >= 0) return w;
      var block = finishingMove(board, other(me));
      if (block >= 0) return block;
      if (rand() < 0.5) return pick(cells, rand);
      return pick(bestMoves(board, me), rand);
    }

    // hard: on an empty board every opening draws with perfect play, so
    // vary it between centre and corners to keep games from repeating.
    if (cells.length === 9) return pick([4, 0, 2, 6, 8], rand);
    return pick(bestMoves(board, me), rand);
  }

  var XO = {
    LINES: LINES,
    emptyBoard: emptyBoard,
    other: other,
    freeCells: freeCells,
    result: result,
    bestMoves: bestMoves,
    computerMove: computerMove
  };

  if (typeof module !== 'undefined' && module.exports) module.exports = XO;
  else root.XO = XO;
})(this);
