# sitemirror

Download a website for offline use and rewrite every asset reference — CSS, JS,
images, fonts, media — to point at the local copies, so the mirror opens from
the filesystem with no network access.

Built to mirror `https://www.joinzad.com/en/` and the pages linked from its menu.

Python 3.8+, standard library only. Nothing to install.

## Usage

```bash
python3 tools/sitemirror.py https://www.joinzad.com/en/ -o site --same-prefix -v
python3 tools/verify_mirror.py site --allow-remote-links
```

The first command writes the mirror into `site/`. The second walks the result
and exits non-zero if any reference still points at a remote host or at a local
file that does not exist — so a clean exit means the mirror is self-contained.

Then just open `site/en/index.html` in a browser.

### Options that matter

| Flag | Effect |
|---|---|
| `--menu-only` (default) | Seed the crawl from navigation/menu links on the entry page |
| `--all-links` | Seed from every internal link on the entry page instead |
| `--same-prefix` | Stay under the entry URL's path, e.g. only `/en/` |
| `--max-depth N` | How far to follow links past the seed pages (default 2) |
| `--max-pages N` | Hard cap on pages (default 200) |
| `--delay S` | Seconds between page fetches (default 0.3); raise it to be gentle |
| `--no-js-assets` | Skip fetching asset URLs found in JavaScript string literals |

## What gets rewritten

- Element attributes: `src`, `href`, `poster`, `data`, and lazy-load variants
  (`data-src`, `data-original`, `data-lazy`, `data-bg`, …)
- `srcset` / `imagesrcset` — every candidate, with density descriptors preserved
- `<link>` for stylesheets, icons, preloads, and manifests
- `<meta>` `og:image` / `twitter:image` / `msapplication-TileImage`
- `<style>` blocks and inline `style=""` attributes
- CSS `url()` and `@import`, followed recursively into imported sheets
- Absolute URLs in inline and external JavaScript

Assets are downloaded from **any** host, not just the site's own — fonts, CDN
scripts, and third-party images all come local, under `assets/_external/<host>/`.

Deliberately left alone, because rewriting them would be wrong: `rel=canonical`,
`rel=preconnect` / `dns-prefetch`, `data:` URIs, `mailto:` / `tel:` links,
in-page `#anchors`, off-site `<a href>` links, and `<form action>`.

## Layout

```
site/
  en/index.html              pages, mirroring the site's own URL structure
  en/about/index.html
  assets/en/css/…            same-origin assets
  assets/_external/<host>/…  assets from other origins
  mirror-manifest.json       every URL → local path, plus any fetch failures
```

`?query` strings are folded into the filename as a short hash, so
`main.css?v=3` and `main.css?v=4` stay distinct files.

## Tests

```bash
python3 tests/test_mirror.py
```

Builds a two-origin fixture site, serves it over real HTTP on two ports, mirrors
it, and runs 51 assertions over the result — cross-origin assets, `<base href>`
handling, `srcset`, `@import` chains, query-string filenames, quote nesting in
style attributes, and byte-integrity of binaries. No network access needed.

## Known limits

- URLs that JavaScript assembles at runtime from fragments cannot be resolved
  statically. Absolute URLs in string literals are fetched; concatenated ones
  are not. `verify_mirror.py` reports whatever remains remote.
- Content rendered entirely client-side (a SPA that fetches its routes as JSON)
  mirrors as the shell page. Pages reachable only through JS routing won't be
  discovered by link crawling.
- `<form action>` is left pointing at the live site; a static mirror has nowhere
  to post.
- `robots.txt` is not consulted. Check that you have the right to mirror a site,
  and use `--delay` to avoid hammering it.

---

This repo also holds **[XO Paper Game](xo-game/)**, a tic-tac-toe app with
single-player (vs computer) and two-player modes. See `xo-game/README.md`.
