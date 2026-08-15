#!/usr/bin/env python3
"""End-to-end test: mirror a live two-origin fixture site and assert the result.

Serves the fixture over real HTTP on two ports, runs sitemirror against it, then
checks the saved copy is complete and self-contained. Run with: python3 tests/test_mirror.py
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import sys
import tempfile
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.join(HERE, "fixture"))

import build_fixture  # noqa: E402
import verify_mirror  # noqa: E402
from sitemirror import Mirror  # noqa: E402

FAILURES: list[str] = []


def check(cond: bool, label: str) -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {label}")
    if not cond:
        FAILURES.append(label)


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):  # noqa: A002 - silence per-request logging
        pass


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def serve(directory: str, port: int) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer(
        ("127.0.0.1", port), partial(QuietHandler, directory=directory))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="sitemirror-test-")
    site_port, cdn_port = free_port(), free_port()
    site_origin = f"127.0.0.1:{site_port}"
    cdn_base = f"http://127.0.0.1:{cdn_port}"

    try:
        build_fixture.build(tmp, cdn_base)
        # The fixture embeds a placeholder for its own origin inside JS strings.
        for dirpath, _d, files in os.walk(os.path.join(tmp, "site")):
            for name in files:
                if not name.endswith((".html", ".js", ".css")):
                    continue
                p = os.path.join(dirpath, name)
                with open(p, encoding="utf-8") as fh:
                    text = fh.read()
                if "SITE_ORIGIN" in text:
                    with open(p, "w", encoding="utf-8") as fh:
                        fh.write(text.replace("SITE_ORIGIN", site_origin))

        servers = [serve(os.path.join(tmp, "site"), site_port),
                   serve(os.path.join(tmp, "cdn"), cdn_port)]
        out = os.path.join(tmp, "mirror")

        print("\n=== crawling fixture ===")
        Mirror(f"http://{site_origin}/en/", out,
               max_pages=50, max_depth=2, menu_only=True, delay=0,
               user_agent="sitemirror-test", timeout=10, same_prefix=True,
               retries=1, verbose=False).crawl()
        for s in servers:
            s.shutdown()

        manifest = json.load(open(os.path.join(out, "mirror-manifest.json")))
        locals_by_url = {r["url"]: r["local"] for r in manifest["resources"]}
        all_local = set(locals_by_url.values())

        print("\n=== pages ===")
        # The mirror preserves the site's own URL path structure under the root.
        for want in ("en/index.html", "en/about/index.html",
                     "en/products/index.html", "en/contact.html"):
            check(os.path.exists(os.path.join(out, want)), f"menu page saved: {want}")
        check(os.path.exists(os.path.join(out, "en/products/detail.html")),
              "page found by crawling past the menu: en/products/detail.html")
        check(manifest["pages"] == 5, f"page count is 5 (got {manifest['pages']})")
        check(manifest["failures"] == [], "no fetch failures")

        print("\n=== assets downloaded ===")
        for frag in ("fonts.css", "app.js", "logo.png", "hero.png",
                     "lazy.png", "inline.png", "poster.png", "banner.png",
                     "clip.mp4", "local.woff"):
            check(any(frag in p for p in all_local), f"same-origin asset: {frag}")
        for frag in ("favicon.png", "social.png", "bg.png", "logo@2x.png",
                     "font.woff2", "icon.svg"):
            check(any(frag in p for p in all_local), f"cross-origin asset: {frag}")
        check(any("_external" in p for p in all_local),
              "cross-origin assets stored under assets/_external/")
        check(f"127.0.0.1:{cdn_port}" in manifest["external_hosts_downloaded"],
              "manifest records the external host")

        print("\n=== query strings and collisions ===")
        css_url = f"http://{site_origin}/en/css/main.css?v=3"
        check(css_url in locals_by_url, "asset with ?query was fetched")
        check(re.search(r"main\.[0-9a-f]{8}\.css$", locals_by_url.get(css_url, "")) is not None,
              "?query is folded into the filename, not dropped")

        check(any(re.search(r"main\.[0-9a-f]{8}\.css$", p) for p in all_local),
              "same-origin asset: main.css (query folded into the name)")

        home = open(os.path.join(out, "en/index.html"), encoding="utf-8").read()
        about = open(os.path.join(out, "en/about/index.html"), encoding="utf-8").read()
        contact = open(os.path.join(out, "en/contact.html"), encoding="utf-8").read()

        print("\n=== rewriting in HTML ===")
        check(f"http://{site_origin}" not in home, "no same-origin absolute URLs left in home")
        check(cdn_base not in home, "no CDN absolute URLs left in home")
        check("<base" not in home.lower(), "<base href> stripped so relative paths hold")
        check('href="./about/index.html"' in home, "menu link rewritten to the local page")
        check(not any(line.strip() == ">" for line in home.splitlines()),
              "no orphaned '>' left behind when <base> was removed")
        check('href="https://example.org/external"' in home, "external link left intact")
        check('href="#anchor"' in home and "mailto:hi@example.com" in home,
              "anchor and mailto links left intact")
        check('rel="canonical" href="https://site.example/en/"' in home,
              "rel=canonical left pointing at the live site")
        check("https://fonts.example" in home, "rel=preconnect left intact")
        check("data:image/gif;base64" in home, "data: URI left intact")
        check('content="width=device-width, initial-scale=1"' in home,
              "non-URL meta content untouched")

        print("\n=== srcset, lazy-load, media, meta ===")
        srcset = re.search(r'srcset="([^"]+)"', home)
        check(srcset is not None and "http" not in srcset.group(1),
              "both srcset candidates rewritten")
        check(srcset is not None and "1x" in srcset.group(1) and "2x" in srcset.group(1),
              "srcset density descriptors preserved")
        check(re.search(r'data-src="\.[^"]*lazy[^"]*\.png"', home) is not None,
              "data-src lazy image rewritten")
        check(re.search(r'poster="\.[^"]*poster[^"]*\.png"', home) is not None,
              "<video poster> rewritten")
        check(re.search(r'<source src="\.[^"]*clip[^"]*\.mp4"', home) is not None,
              "<source src> rewritten")
        check(re.search(r'property="og:image" content="\.[^"]+"', home) is not None,
              "og:image rewritten to the local copy")
        check(re.search(r"""style="background:url\('\.[^"]+'\)""", home) is not None,
              "url() inside a style attribute rewritten")
        check('url("' not in re.search(r'style="[^"]*"', home).group(0),
              "style attribute stays valid: no double quotes nested in a double-quoted attr")
        check(re.search(r'background-image: url\("\.[^"]+hero', home) is not None,
              "url() inside a <style> block rewritten")

        print("\n=== CSS ===")
        css_local = locals_by_url[css_url]
        css_text = open(os.path.join(out, css_local), encoding="utf-8").read()
        check("@import" in css_text and "http" not in css_text.split("@import")[1][:60],
              "@import rewritten to the local sheet")
        check(cdn_base not in css_text, "cross-origin url() in CSS rewritten")
        check("url(data:image/gif" in css_text, "data: URI in CSS left intact")
        # A same-origin relative url() can legitimately come out byte-identical,
        # since the mirror preserves the site's path structure -- so assert the
        # reference resolves to a real file rather than that the text changed.
        banner = re.search(r"""url\(['"]?([^'")]*banner[^'")]*)['"]?\)""", css_text)
        check(banner is not None and os.path.exists(os.path.normpath(
            os.path.join(out, os.path.dirname(css_local), banner.group(1)))),
            "relative url() in CSS resolves to the mirrored file")
        fonts_local = next(v for k, v in locals_by_url.items() if k.endswith("fonts.css"))
        fonts_text = open(os.path.join(out, fonts_local), encoding="utf-8").read()
        check(cdn_base not in fonts_text and "local.woff" in fonts_text,
              "@font-face srcs rewritten in the imported sheet")
        check('format("woff2")' in fonts_text, "font format() hints preserved")

        print("\n=== JavaScript ===")
        js_local = next(v for k, v in locals_by_url.items() if k.endswith("app.js"))
        js_text = open(os.path.join(out, js_local), encoding="utf-8").read()
        check(f"http://{site_origin}" not in js_text,
              "absolute mirrored URL rewritten in external JS")
        check(cdn_base not in home.split("<script>")[-1],
              "absolute mirrored URL rewritten in inline JS")
        check(any("data.json" in p for p in all_local),
              "asset named only inside a JS string literal was fetched")

        print("\n=== relative depth from nested pages ===")
        check("../assets/" in about or "./../assets/" in about,
              "nested page walks up to shared assets")
        check(f"http://{site_origin}" not in about and cdn_base not in about,
              "nested page fully rewritten")
        check(f"http://{site_origin}" not in contact and cdn_base not in contact,
              "page without <base href> fully rewritten")

        print("\n=== verifier ===")
        rc = verify_mirror.main([out, "--allow-remote-links"])
        check(rc == 0, "verify_mirror reports a self-contained mirror")

        print("\n=== offline integrity ===")
        broken = []
        for r in manifest["resources"]:
            if not os.path.exists(os.path.join(out, r["local"])):
                broken.append(r["local"])
        check(not broken, f"every manifest entry exists on disk ({len(manifest['resources'])} files)")
        pngs = [os.path.join(out, p) for p in all_local if p.endswith(".png")]
        check(pngs and all(open(p, "rb").read(8) == b"\x89PNG\r\n\x1a\n" for p in pngs),
              "binary assets saved byte-intact")

        print("\n" + "=" * 52)
        if FAILURES:
            print(f"FAILED: {len(FAILURES)} check(s)")
            for f in FAILURES:
                print(f"  - {f}")
            return 1
        print("ALL CHECKS PASSED")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
