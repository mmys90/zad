# XO Paper Game

Tic-tac-toe drawn on graph paper (or a chalkboard, in dark mode). It runs in
any browser and installs to a phone's home screen like a native app, then
works offline.

## Modes

- **Computer (single player)**: three levels.
  - *Easy* plays loosely and misses threats.
  - *Medium* takes its wins and blocks yours, but slips up about half the time.
  - *Hard* plays perfect minimax. It never loses; a draw is the best you can do.
  - Choose whether you play X or O.
- **Two players**: pass-and-play on one device, with editable names.

X and O take turns going first each round. Scores are kept per matchup
(each difficulty, and two players) and survive closing the app. **Reset score**
under the board sets the current matchup back to 0 after you confirm.

Controls: tap a square, or use arrow keys and 1-9 (top-left to bottom-right).
Undo takes back the last move (against the computer, your move and its reply).

## Run it

It's a static site with no build step:

```bash
cd xo-game
python3 -m http.server 8000
# open http://localhost:8000
```

## Install as an app

Host the folder anywhere with HTTPS (GitHub Pages, Netlify, Vercel), open it on
your phone, then:

- **Android / Chrome**: tap the **Install** button on the menu screen, or
  browser menu > *Install app*.
- **iPhone / Safari**: tap Share > *Add to Home Screen*.

The service worker (`sw.js`) caches the app, so it opens without a connection
after the first visit. Bump `VERSION` in `sw.js` when you change any file.

## Files

| File | Purpose |
|---|---|
| `index.html` | Screens and markup |
| `styles.css` | Paper and chalkboard themes |
| `xo-core.js` | Rules and the computer player, no DOM |
| `app.js` | UI, scores, sound, install prompt |
| `manifest.webmanifest`, `sw.js`, `icons/` | Installable, offline app |

## Tests

```bash
node xo-game/test/xo-core.test.js
```

Checks win and draw detection, that Medium and Hard take wins and block
threats, and walks every possible game against Hard, from both sides, to prove
it never loses.
