/* XO app: screens, board rendering, scores, sound and install prompt.
 * Game rules and the computer player live in xo-core.js (window.XO). */
(function () {
  'use strict';

  var XO = window.XO;
  var $ = function (id) { return document.getElementById(id); };
  var SVG_NS = 'http://www.w3.org/2000/svg';

  var LEVEL_HINTS = {
    easy: 'Plays loosely and misses threats. Good for warming up.',
    medium: 'Takes its wins and blocks yours, but slips up now and then.',
    hard: 'Plays perfectly. It never loses, so a draw is a good result.'
  };
  var LEVEL_NAMES = { easy: 'Easy', medium: 'Medium', hard: 'Hard' };

  // Hand-drawn strokes on a 100x100 cell. Each mark has a few variants so
  // a board never looks stamped. Paths use pathLength=1 for the ink effect.
  var X_PATHS = [
    ['M22 20 C40 40 60 62 80 82', 'M79 19 C60 40 42 60 20 81'],
    ['M24 24 Q48 46 78 78', 'M76 22 Q52 52 22 78'],
    ['M20 23 C38 38 62 58 81 79', 'M80 22 C62 44 40 62 23 80']
  ];
  var O_PATHS = [
    ['M56 18 C80 20 88 50 78 68 C68 86 36 88 24 72 C12 56 18 28 40 20 C47 18 54 17 62 20'],
    ['M48 17 C72 15 86 38 82 58 C78 80 52 88 34 80 C16 72 12 46 24 30 C32 20 44 16 56 18'],
    ['M60 20 C82 28 86 58 72 74 C58 88 30 84 20 64 C12 46 22 22 46 18 C52 17 58 18 64 21']
  ];

  // ---------- storage (best effort; the game works without it) ----------
  var store = {
    get: function (k, fallback) {
      try {
        var v = localStorage.getItem('xo:' + k);
        return v ? JSON.parse(v) : fallback;
      } catch (e) { return fallback; }
    },
    set: function (k, v) {
      try { localStorage.setItem('xo:' + k, JSON.stringify(v)); } catch (e) { /* ignore */ }
    }
  };

  var settings = Object.assign(
    { mode: 'cpu', level: 'medium', humanMark: 'X', p1: '', p2: '', sound: true },
    store.get('settings', {})
  );
  var scores = store.get('scores', {});
  var game = null;
  var cpuTimer = null;

  function saveSettings() { store.set('settings', settings); }

  // ---------- players ----------
  // p1 is you (vs computer) or the X player (two players); p2 is the other.
  function markOf(p) {
    if (settings.mode === 'pvp') return p === 'p1' ? 'X' : 'O';
    return p === 'p1' ? settings.humanMark : XO.other(settings.humanMark);
  }
  function playerOf(mark) { return markOf('p1') === mark ? 'p1' : 'p2'; }
  function nameOf(p) {
    if (settings.mode === 'cpu') return p === 'p1' ? 'You' : 'Computer';
    return (settings[p] || '').trim() || (p === 'p1' ? 'Player 1' : 'Player 2');
  }
  function scoreKey() { return settings.mode === 'cpu' ? 'cpu-' + settings.level : 'pvp'; }
  function currentScore() {
    var k = scoreKey();
    if (!scores[k]) scores[k] = { p1: 0, p2: 0, draw: 0 };
    return scores[k];
  }
  function isCpuTurn() {
    return settings.mode === 'cpu' && game && !game.result && game.turn === markOf('p2');
  }
  function humanCanMove() { return game && !game.result && !isCpuTurn(); }
  function markOfMove(n) { return n % 2 === 0 ? game.starter : XO.other(game.starter); }

  // ---------- sound ----------
  var audio = null;
  function tone(freq, start, dur, type, gain) {
    if (!settings.sound) return;
    try {
      audio = audio || new (window.AudioContext || window.webkitAudioContext)();
      var t = audio.currentTime + start;
      var osc = audio.createOscillator();
      var g = audio.createGain();
      osc.type = type || 'triangle';
      osc.frequency.value = freq;
      g.gain.setValueAtTime(0.0001, t);
      g.gain.exponentialRampToValueAtTime(gain || 0.18, t + 0.015);
      g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
      osc.connect(g).connect(audio.destination);
      osc.start(t);
      osc.stop(t + dur + 0.02);
    } catch (e) { /* no audio available */ }
  }
  var sfx = {
    place: function (mark) { tone(mark === 'X' ? 560 : 420, 0, 0.12); },
    win: function () { [523, 659, 784, 1047].forEach(function (f, i) { tone(f, i * 0.09, 0.22); }); },
    lose: function () { [392, 330, 262].forEach(function (f, i) { tone(f, i * 0.12, 0.26, 'sine'); }); },
    draw: function () { tone(440, 0, 0.14, 'sine'); tone(440, 0.16, 0.18, 'sine'); }
  };
  function buzz(ms) { try { if (navigator.vibrate) navigator.vibrate(ms); } catch (e) { /* ignore */ } }

  // ---------- svg helpers ----------
  function svgEl(tag, attrs) {
    var el = document.createElementNS(SVG_NS, tag);
    for (var k in attrs) el.setAttribute(k, attrs[k]);
    return el;
  }
  function jitter(n) { return (Math.random() * 2 - 1) * n; }

  function drawGrid(animate) {
    var g = svgEl('g', { 'class': animate ? 'draw' : '' });
    [100, 200].forEach(function (p) {
      g.appendChild(svgEl('path', {
        'class': 'grid-line', pathLength: 1,
        d: 'M' + (p + jitter(3)) + ' 10 Q' + (p + jitter(5)) + ' 150 ' + (p + jitter(3)) + ' 290'
      }));
    });
    [100, 200].forEach(function (p) {
      g.appendChild(svgEl('path', {
        'class': 'grid-line', pathLength: 1,
        d: 'M10 ' + (p + jitter(3)) + ' Q150 ' + (p + jitter(5)) + ' 290 ' + (p + jitter(3))
      }));
    });
    var grid = $('grid');
    grid.textContent = '';
    grid.appendChild(g);
  }

  function markSvg(mark, i, extraClass) {
    var variants = mark === 'X' ? X_PATHS : O_PATHS;
    var paths = variants[(i * 7 + game.round * 3) % variants.length];
    var svg = svgEl('svg', { viewBox: '0 0 100 100', 'aria-hidden': 'true', 'class': (mark === 'X' ? 'mx' : 'mo') + (extraClass ? ' ' + extraClass : '') });
    paths.forEach(function (d) { svg.appendChild(svgEl('path', { d: d, pathLength: 1 })); });
    return svg;
  }

  function drawStrike(line) {
    var layer = $('strike-layer');
    layer.textContent = '';
    if (!line) return;
    var c = function (i) { return [(i % 3) * 100 + 50, Math.floor(i / 3) * 100 + 50]; };
    var a = c(line[0]), b = c(line[2]);
    var dx = b[0] - a[0], dy = b[1] - a[1];
    var len = Math.sqrt(dx * dx + dy * dy);
    var ext = 34 / len;
    var x1 = a[0] - dx * ext, y1 = a[1] - dy * ext;
    var x2 = b[0] + dx * ext, y2 = b[1] + dy * ext;
    var mx = (x1 + x2) / 2 + jitter(4), my = (y1 + y2) / 2 + jitter(4);
    layer.appendChild(svgEl('path', {
      'class': 'strike draw', pathLength: 1,
      d: 'M' + x1 + ' ' + y1 + ' Q' + mx + ' ' + my + ' ' + x2 + ' ' + y2
    }));
  }

  // ---------- game flow ----------
  function startGame() {
    clearTimeout(cpuTimer);
    game = { board: XO.emptyBoard(), round: 0, starter: 'O', turn: 'X', history: [], result: null, lastMove: -1, thinking: false };
    nextRound();
    show('game');
  }

  // Next round: the other mark goes first, so nobody keeps the opening edge.
  function nextRound() {
    game.round += 1;
    game.starter = XO.other(game.starter);
    restartRound();
  }

  function restartRound() {
    clearTimeout(cpuTimer);
    game.board = XO.emptyBoard();
    game.turn = game.starter;
    game.history = [];
    game.result = null;
    game.lastMove = -1;
    game.thinking = false;
    drawGrid(true);
    drawStrike(null);
    render();
    maybeCpuMove();
  }

  function play(i, byComputer) {
    if (!game || game.result || game.board[i]) return;
    if (isCpuTurn() && !byComputer) return;
    var mark = game.turn;
    game.board[i] = mark;
    game.history.push(i);
    game.lastMove = i;
    sfx.place(mark);
    buzz(8);
    game.result = XO.result(game.board);
    if (game.result) finishRound();
    else game.turn = XO.other(mark);
    render();
    maybeCpuMove();
  }

  function finishRound() {
    var s = currentScore();
    var r = game.result;
    var bumped;
    if (r.winner === 'draw') {
      s.draw += 1;
      bumped = 'score-draw-num';
      sfx.draw();
    } else {
      var p = playerOf(r.winner);
      s[p] += 1;
      bumped = 'score-' + p + '-num';
      if (settings.mode === 'cpu' && p === 'p2') sfx.lose(); else sfx.win();
      buzz([20, 40, 30]);
      drawStrike(r.line);
    }
    store.set('scores', scores);
    game.bump = bumped;
  }

  function maybeCpuMove() {
    if (!isCpuTurn()) return;
    game.thinking = true;
    render();
    clearTimeout(cpuTimer);
    cpuTimer = setTimeout(function () {
      game.thinking = false;
      var i = XO.computerMove(game.board, markOf('p2'), settings.level);
      play(i, true);
    }, 420 + Math.random() * 380);
  }

  function canUndo() {
    if (!game || game.result || !game.history.length) return false;
    if (settings.mode === 'pvp') return true;
    // Vs the computer, undo takes back your last move (and its reply).
    for (var n = 0; n < game.history.length; n++) {
      if (markOfMove(n) === markOf('p1')) return true;
    }
    return false;
  }

  function undo() {
    if (!canUndo()) return;
    clearTimeout(cpuTimer);
    game.thinking = false;
    var human = markOf('p1');
    while (game.history.length) {
      var n = game.history.length - 1;
      var idx = game.history.pop();
      game.board[idx] = null;
      if (settings.mode === 'pvp' || markOfMove(n) === human) break;
    }
    game.turn = markOfMove(game.history.length);
    game.lastMove = -1;
    render();
    maybeCpuMove();
  }

  // ---------- rendering ----------
  function statusHtml() {
    var tag = function (mark) { return '<span class="t' + mark.toLowerCase() + '">' + mark + '</span>'; };
    var r = game.result;
    if (r) {
      if (r.winner === 'draw') return 'Draw. Nobody gets three.';
      var p = playerOf(r.winner);
      if (settings.mode === 'cpu') return p === 'p1' ? 'You win this round!' : 'Computer wins this round';
      return escapeHtml(nameOf(p)) + ' wins! ' + tag(r.winner);
    }
    if (game.thinking) return '<span class="thinking">Computer is thinking</span>';
    if (settings.mode === 'cpu') return 'Your move ' + tag(game.turn);
    return escapeHtml(nameOf(playerOf(game.turn))) + '’s move ' + tag(game.turn);
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function render() {
    if (!game) return;
    var s = currentScore();

    $('game-title').textContent = settings.mode === 'cpu'
      ? 'Computer · ' + LEVEL_NAMES[settings.level]
      : 'Two players';
    $('round-label').textContent = 'Round ' + game.round;

    ['p1', 'p2'].forEach(function (p) {
      var card = $('score-' + p);
      var mark = markOf(p);
      card.dataset.mark = mark;
      card.classList.toggle('active', !game.result && game.turn === mark);
      $('score-' + p + '-name').textContent = nameOf(p);
      $('score-' + p + '-mark').textContent = mark;
      $('score-' + p + '-num').textContent = s[p];
    });
    $('score-draw-num').textContent = s.draw;
    if (game.bump) {
      var el = $(game.bump);
      el.classList.remove('bump');
      void el.offsetWidth; // restart the animation
      el.classList.add('bump');
      game.bump = null;
    }

    $('status').innerHTML = statusHtml();

    var live = humanCanMove();
    $('board').classList.toggle('live', live);
    var cells = $('cells');
    var focused = document.activeElement && document.activeElement.dataset
      ? document.activeElement.dataset.index : undefined;
    cells.textContent = '';
    var winLine = game.result && game.result.line;
    for (var i = 0; i < 9; i++) {
      var mark = game.board[i];
      var b = document.createElement('button');
      b.type = 'button';
      b.className = 'cell';
      b.dataset.index = i;
      b.setAttribute('role', 'gridcell');
      b.setAttribute('aria-label', 'Row ' + (Math.floor(i / 3) + 1) + ', column ' + (i % 3 + 1) + ', ' + (mark || 'empty'));
      if (mark) {
        b.dataset.filled = mark;
        b.appendChild(markSvg(mark, i, i === game.lastMove ? 'draw' : ''));
        b.setAttribute('aria-disabled', 'true');
      } else if (live) {
        b.appendChild(markSvg(game.turn, i, 'ghost'));
      } else {
        b.setAttribute('aria-disabled', 'true');
      }
      if (winLine && winLine.indexOf(i) < 0) b.dataset.dim = '';
      if (game.result && game.result.winner === 'draw') b.dataset.dim = '';
      cells.appendChild(b);
    }
    if (focused !== undefined && $('game').contains(document.activeElement) === false) {
      var again = cells.querySelector('[data-index="' + focused + '"]');
      if (again) again.focus({ preventScroll: true });
    }
    game.lastMove = -1; // animate a mark only once

    $('undo').disabled = !canUndo();
    $('next-label').textContent = game.result ? 'Next round' : 'Restart round';
  }

  // ---------- home screen ----------
  function renderHome() {
    document.querySelectorAll('.mode').forEach(function (b) {
      b.setAttribute('aria-pressed', String(b.dataset.mode === settings.mode));
    });
    $('panel-cpu').hidden = settings.mode !== 'cpu';
    $('panel-pvp').hidden = settings.mode !== 'pvp';
    document.querySelectorAll('[data-level]').forEach(function (b) {
      b.setAttribute('aria-pressed', String(b.dataset.level === settings.level));
    });
    document.querySelectorAll('[data-mark]').forEach(function (b) {
      if (b.tagName === 'BUTTON') b.setAttribute('aria-pressed', String(b.dataset.mark === settings.humanMark));
    });
    $('level-hint').textContent = LEVEL_HINTS[settings.level];
    $('name-p1').value = settings.p1;
    $('name-p2').value = settings.p2;
    renderSound();
  }

  function renderSound() {
    $('sound').setAttribute('aria-pressed', String(settings.sound));
    $('sound').setAttribute('aria-label', settings.sound ? 'Sound on' : 'Sound off');
    $('sound-waves').style.display = settings.sound ? '' : 'none';
    $('sound-off').hidden = settings.sound;
  }

  var currentScreen = 'home';
  function show(name) {
    currentScreen = name;
    $('home').hidden = name !== 'home';
    $('game').hidden = name !== 'game';
    $('confirm').hidden = true;
    $('reset').hidden = false;
    if (name === 'home') {
      clearTimeout(cpuTimer);
      renderHome();
    }
    window.scrollTo(0, 0);
  }

  // ---------- events ----------
  document.querySelectorAll('.mode').forEach(function (b) {
    b.addEventListener('click', function () { settings.mode = b.dataset.mode; saveSettings(); renderHome(); });
  });
  document.querySelectorAll('button[data-level]').forEach(function (b) {
    b.addEventListener('click', function () { settings.level = b.dataset.level; saveSettings(); renderHome(); });
  });
  document.querySelectorAll('button[data-mark]').forEach(function (b) {
    b.addEventListener('click', function () { settings.humanMark = b.dataset.mark; saveSettings(); renderHome(); });
  });
  ['p1', 'p2'].forEach(function (p) {
    $('name-' + p).addEventListener('input', function (e) { settings[p] = e.target.value; saveSettings(); });
  });

  $('start').addEventListener('click', startGame);
  $('back').addEventListener('click', function () { show('home'); });
  $('sound').addEventListener('click', function () { settings.sound = !settings.sound; saveSettings(); renderSound(); });
  $('undo').addEventListener('click', undo);
  $('next').addEventListener('click', function () {
    if (game.result) nextRound(); else restartRound();
  });
  $('reset').addEventListener('click', function () { $('reset').hidden = true; $('confirm').hidden = false; $('confirm-yes').focus(); });
  $('confirm-no').addEventListener('click', function () { $('confirm').hidden = true; $('reset').hidden = false; });
  $('confirm-yes').addEventListener('click', function () {
    scores[scoreKey()] = { p1: 0, p2: 0, draw: 0 };
    store.set('scores', scores);
    $('confirm').hidden = true;
    $('reset').hidden = false;
    render();
  });

  $('cells').addEventListener('click', function (e) {
    var cell = e.target.closest('.cell');
    if (cell) play(Number(cell.dataset.index), false);
  });

  // Arrow keys move between cells; 1-9 play the cells left to right, top to bottom.
  $('cells').addEventListener('keydown', function (e) {
    var cell = e.target.closest('.cell');
    if (!cell) return;
    var i = Number(cell.dataset.index);
    var moves = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -3, ArrowDown: 3 };
    if (!(e.key in moves)) return;
    var j = i + moves[e.key];
    if (e.key === 'ArrowLeft' && i % 3 === 0) return;
    if (e.key === 'ArrowRight' && i % 3 === 2) return;
    if (j < 0 || j > 8) return;
    e.preventDefault();
    $('cells').children[j].focus();
  });
  document.addEventListener('keydown', function (e) {
    if (currentScreen !== 'game' || e.target.tagName === 'INPUT') return;
    if (/^[1-9]$/.test(e.key)) play(Number(e.key) - 1, false);
  });

  // ---------- installable app ----------
  var deferredInstall = null;
  var standalone = (window.matchMedia && matchMedia('(display-mode: standalone)').matches) || navigator.standalone;
  window.addEventListener('beforeinstallprompt', function (e) {
    e.preventDefault();
    deferredInstall = e;
    $('install').hidden = false;
    $('install-btn').hidden = false;
  });
  window.addEventListener('appinstalled', function () { $('install').hidden = true; });
  $('install-btn').addEventListener('click', function () {
    if (!deferredInstall) return;
    deferredInstall.prompt();
    deferredInstall = null;
    $('install').hidden = true;
  });
  if (!standalone && /iphone|ipad|ipod/i.test(navigator.userAgent) && /^https?:$/.test(location.protocol)) {
    $('install-text').textContent = 'To install on iPhone, tap Share, then Add to Home Screen.';
    $('install-btn').hidden = true;
    $('install').hidden = false;
  }
  try {
    if ('serviceWorker' in navigator && /^https?:$/.test(location.protocol)) {
      navigator.serviceWorker.register('sw.js').catch(function () { /* offline mode unavailable */ });
    }
  } catch (e) { /* not allowed here */ }

  // ---------- boot ----------
  // When hosted as a live artifact, keep an in-progress game across updates.
  var hot = window.claude && window.claude.hot;
  if (hot && hot.snapshot) {
    hot.snapshot(function () { return { game: game, screen: currentScreen }; });
  }
  function boot(data) {
    renderHome();
    if (data && data.game && data.screen === 'game') {
      game = data.game;
      game.lastMove = -1;
      game.thinking = false;
      drawGrid(false);
      drawStrike(game.result && game.result.line);
      show('game');
      render();
      maybeCpuMove();
    } else {
      show('home');
    }
  }
  if (hot && hot.ready) hot.ready(boot); else boot((hot && hot.data) || {});
})();
