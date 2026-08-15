#!/usr/bin/env python3
"""Generate a two-origin fixture site exercising the reference styles sitemirror handles.

Origin A (the "site") is served on one port, origin B (a stand-in CDN) on another,
so cross-origin asset rewriting is exercised for real rather than simulated.

Coverage: menu-based navigation, <base href>, pages with and without a base,
query-string assets, @import chains, CSS url() at several depths, cross-origin
CSS/font/image refs, srcset, lazy-load data-src, inline style attributes,
<style> blocks, <video poster> + <source>, og:image meta, rel=canonical and
rel=preconnect (must be left alone), data: URIs (must be left alone), external
links, and absolute URLs embedded in inline and external JavaScript.
"""

import os
import struct
import sys
import zlib


def png(rgb) -> bytes:
    """Smallest valid 1x1 PNG in the given colour."""
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(bytes([0]) + bytes(rgb)))
            + chunk(b"IEND", b""))


def write(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb" if isinstance(data, bytes) else "w") as fh:
        fh.write(data)


NAV = """<nav class="main-menu">
      <a href="/en/">Home</a>
      <a href="/en/about/">About</a>
      <a href="/en/products/">Products</a>
      <a href="/en/contact.html">Contact</a>
      <a href="https://example.org/external">External</a>
      <a href="#anchor">Anchor</a>
      <a href="mailto:hi@example.com">Mail</a>
    </nav>"""


def page(title: str, body: str, cdn: str, *, use_base: bool) -> str:
    # With <base href="/en/"> every relative ref resolves against /en/ no matter
    # where the page lives; without it, the page uses root-relative paths.
    base_tag = '<base href="/en/">' if use_base else ""
    p = (lambda rel: rel) if use_base else (lambda rel: "/en/" + rel)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title}</title>
{base_tag}
<link rel="canonical" href="https://site.example/en/">
<link rel="preconnect" href="https://fonts.example">
<link rel="stylesheet" href="{p('css/main.css')}?v=3">
<link rel="icon" href="{cdn}/favicon.png">
<link rel="preload" as="font" href="{cdn}/font.woff2" crossorigin>
<meta property="og:image" content="{cdn}/social.png">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  .hero {{ background-image: url("{p('img/hero.png')}"); }}
  .cdn  {{ background: url({cdn}/bg.png) no-repeat; }}
</style>
</head>
<body>
{NAV}
<h1>{title}</h1>
{body}
<img src="{p('img/logo.png')}" alt="logo"
     srcset="{p('img/logo.png')} 1x, {cdn}/logo@2x.png 2x">
<img data-src="{p('img/lazy.png')}" src="data:image/gif;base64,R0lGODlhAQABAAAAACw=" alt="lazy">
<div style="background:url('{p('img/inline.png')}')">styled</div>
<video poster="{p('img/poster.png')}">
  <source src="{p('media/clip.mp4')}" type="video/mp4">
</video>
<script src="{p('js/app.js')}"></script>
<script>
  var API = "{cdn}/data.json";
  var LOGO = "http://SITE_ORIGIN/en/img/logo.png";
</script>
</body>
</html>
"""


def build(root: str, cdn_base: str) -> None:
    site = os.path.join(root, "site")
    cdn = os.path.join(root, "cdn")

    write(os.path.join(site, "en", "index.html"),
          page("Home", "<p>Welcome.</p>", cdn_base, use_base=True))
    write(os.path.join(site, "en", "about", "index.html"),
          page("About", "<p>About us.</p>", cdn_base, use_base=True))
    write(os.path.join(site, "en", "products", "index.html"),
          page("Products", '<p>Products.</p><a href="/en/products/detail.html">Detail</a>',
               cdn_base, use_base=True))
    write(os.path.join(site, "en", "products", "detail.html"),
          page("Detail", "<p>Reachable only by crawling past the menu.</p>",
               cdn_base, use_base=True))
    write(os.path.join(site, "en", "contact.html"),
          page("Contact", "<p>Contact us.</p>", cdn_base, use_base=False))

    write(os.path.join(site, "en", "css", "main.css"), f"""@import "fonts.css";
body {{ margin: 0; font-family: ZadFont, sans-serif; }}
.banner {{ background-image: url('../img/banner.png'); }}
.icon {{ background: url("{cdn_base}/icon.svg") center/contain; }}
.inline {{ background: url(data:image/gif;base64,R0lGODlhAQABAAAAACw=); }}
""")
    write(os.path.join(site, "en", "css", "fonts.css"), f"""@font-face {{
  font-family: ZadFont;
  src: url("{cdn_base}/font.woff2") format("woff2"),
       url('../fonts/local.woff') format("woff");
}}
""")
    write(os.path.join(site, "en", "js", "app.js"),
          'console.log("app");\n'
          'var CFG = { logo: "http://SITE_ORIGIN/en/img/logo.png" };\n')

    for name in ("hero", "logo", "lazy", "inline", "poster", "banner"):
        write(os.path.join(site, "en", "img", f"{name}.png"), png((10, 20, 30)))
    write(os.path.join(site, "en", "media", "clip.mp4"), b"\x00\x00\x00\x18ftypmp42fake")
    write(os.path.join(site, "en", "fonts", "local.woff"), b"wOFFfake")

    write(os.path.join(cdn, "favicon.png"), png((200, 10, 10)))
    write(os.path.join(cdn, "social.png"), png((0, 200, 0)))
    write(os.path.join(cdn, "bg.png"), png((0, 0, 200)))
    write(os.path.join(cdn, "logo@2x.png"), png((90, 90, 90)))
    write(os.path.join(cdn, "font.woff2"), b"wOF2fake")
    write(os.path.join(cdn, "icon.svg"), '<svg xmlns="http://www.w3.org/2000/svg"/>')
    write(os.path.join(cdn, "data.json"), '{"ok":true}')


if __name__ == "__main__":
    build(sys.argv[1], sys.argv[2])
    print("fixture built")
