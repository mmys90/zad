// Run with: node xo-game/test/xo-core.test.js
'use strict';
const assert = require('assert');
const XO = require('../xo-core.js');

let passed = 0;
function test(name, fn) {
  fn();
  passed++;
  console.log('ok   ' + name);
}

const B = (s) => s.split('').map((c) => (c === '.' ? null : c));

test('detects every winning line for both marks', () => {
  for (const line of XO.LINES) {
    for (const m of ['X', 'O']) {
      const b = XO.emptyBoard();
      line.forEach((i) => (b[i] = m));
      const r = XO.result(b);
      assert.strictEqual(r.winner, m);
      assert.deepStrictEqual(r.line, line);
    }
  }
});

test('detects a draw and an unfinished game', () => {
  assert.strictEqual(XO.result(B('XOXXOOOXX')).winner, 'draw');
  assert.strictEqual(XO.result(B('XO.......')), null);
});

test('every level takes or blocks the obvious move on medium/hard', () => {
  // X to move can win at 2.
  assert.strictEqual(XO.computerMove(B('XX.OO....'), 'X', 'hard'), 2);
  assert.strictEqual(XO.computerMove(B('XX.OO....'), 'X', 'medium'), 2);
  // O must block X at 2.
  assert.strictEqual(XO.computerMove(B('XX..O....'), 'O', 'hard'), 2);
  assert.strictEqual(XO.computerMove(B('XX..O....'), 'O', 'medium'), 2);
});

test('easy and medium always return a free cell', () => {
  let seed = 7;
  const rand = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
  for (let n = 0; n < 500; n++) {
    const b = XO.emptyBoard();
    let turn = 'X';
    while (!XO.result(b)) {
      const level = n % 2 ? 'easy' : 'medium';
      const i = XO.computerMove(b, turn, level, rand);
      assert.strictEqual(b[i], null, 'picked an occupied cell');
      b[i] = turn;
      turn = XO.other(turn);
    }
  }
});

// Walk every possible opponent reply against the hard computer, for every
// tie-break choice it could make. It must never lose, from either side.
function hardNeverLoses(computer) {
  let games = 0;
  function walk(board, turn) {
    const r = XO.result(board);
    if (r) {
      games++;
      assert.notStrictEqual(r.winner, XO.other(computer),
        'hard lost as ' + computer + ': ' + board.map((c) => c || '.').join(''));
      return;
    }
    const moves = turn === computer
      ? (XO.freeCells(board).length === 9 ? [4, 0, 2, 6, 8] : XO.bestMoves(board, computer))
      : XO.freeCells(board);
    for (const i of moves) {
      board[i] = turn;
      walk(board, XO.other(turn));
      board[i] = null;
    }
  }
  walk(XO.emptyBoard(), 'X');
  return games;
}

test('hard computer never loses playing X (exhaustive)', () => {
  assert.ok(hardNeverLoses('X') > 0);
});

test('hard computer never loses playing O (exhaustive)', () => {
  assert.ok(hardNeverLoses('O') > 0);
});

test('hard prefers the fastest win', () => {
  // X can win now at 2, or set up a win elsewhere; it must win now.
  assert.strictEqual(XO.computerMove(B('XX.O.O...'), 'X', 'hard'), 2);
});

console.log('\n' + passed + ' tests passed');
