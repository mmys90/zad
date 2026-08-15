#!/usr/bin/env python3
"""Mirror a website for offline use, rewriting every asset reference to a local path.

Crawls HTML pages from an entry URL (optionally restricted to the pages linked in
the site's menu/navigation), downloads every asset those pages reference -- from
any host, not just the site's own -- and rewrites the references in the saved
copies so the mirror opens from the filesystem with no network access.

Handled reference sites:
  * element attributes: src, href, poster, data-src / data-original / lazy-load
    variants, <source>, <embed>, <object data>, <video>, <audio>, <track>
  * srcset / imagesrcset candidate lists (each candidate + descriptor preserved)
  * <link> rel=stylesheet/icon/preload/manifest/apple-touch-icon
  * meta og:image / twitter:image / msapplication-TileImage
  * <style> blocks and style="" attributes
  * CSS url(...) and @import, followed recursively into imported sheets
  * absolute URLs appearing as string literals in inline and external JavaScript,
    limited to URLs that were actually downloaded

Stdlib only -- no third-party packages needed to run it.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import mimetypes
import os
import posixpath
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from dataclasses import dataclass, field

DEFAULT_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Attributes whose value is a single URL, keyed by the tag they may appear on.
# "*" means the attribute is honoured on any tag (covers lazy-loading patterns).
URL_ATTRS = {
    "a": ["href"],
    "area": ["href"],
    "audio": ["src"],
    "embed": ["src"],
    "form": ["action"],
    "frame": ["src"],
    "iframe": ["src"],
    "img": ["src", "longdesc"],
    "input": ["src"],
    "link": ["href"],
    "object": ["data"],
    "script": ["src"],
    "source": ["src"],
    "track": ["src"],
    "use": ["href", "xlink:href"],
    "image": ["href", "xlink:href"],
    "video": ["src", "poster"],
    "*": [
        "data-src",
        "data-original",
        "data-lazy",
        "data-lazy-src",
        "data-bg",
        "data-background",
        "data-background-image",
        "data-poster",
        "data-thumb",
        "data-image",
    ],
}

SRCSET_ATTRS = {"srcset", "data-srcset", "imagesrcset", "data-lazy-srcset"}

# <meta> tags whose content attribute holds a URL, matched on name/property.
META_URL_KEYS = {
    "og:image",
    "og:image:url",
    "og:image:secure_url",
    "og:audio",
    "og:video",
    "og:url",
    "twitter:image",
    "twitter:image:src",
    "msapplication-tileimage",
    "msapplication-config",
}

# Tags that carry a full URL only when rel= says so -- <link rel=canonical> etc.
# should stay pointing at the live site, not the mirror.
LINK_REL_DOWNLOAD = {
    "stylesheet",
    "icon",
    "shortcut icon",
    "apple-touch-icon",
    "apple-touch-icon-precomposed",
    "mask-icon",
    "manifest",
    "preload",
    "prefetch",
    "modulepreload",
    "fluid-icon",
}
LINK_REL_SKIP = {"canonical", "alternate", "dns-prefetch", "preconnect", "author",
                 "license", "next", "prev", "search", "pingback", "profile"}

TAG_RE = re.compile(r"""<([a-zA-Z][-\w:]*)((?:[^>"']|"[^"]*"|'[^']*')*?)(/?)>""")
ATTR_RE = re.compile(
    r"""([-\w:.@]+)(\s*=\s*)("([^"]*)"|'([^']*)'|([^\s"'>=`]+))""", re.S
)
STYLE_BLOCK_RE = re.compile(r"(<style\b[^>]*>)(.*?)(</style>)", re.I | re.S)
SCRIPT_BLOCK_RE = re.compile(r"(<script\b(?![^>]*\bsrc\s*=)[^>]*>)(.*?)(</script>)", re.I | re.S)
CSS_URL_RE = re.compile(r"""url\(\s*(?P<q>["']?)(?P<url>[^"')]+)(?P=q)\s*\)""", re.I)
CSS_IMPORT_RE = re.compile(r"""@import\s+(?P<q>["'])(?P<url>[^"']+)(?P=q)""", re.I)
BASE_RE = re.compile(r"""<base\b[^>]*\bhref\s*=\s*["']([^"']+)["']""", re.I)
BASE_TAG_RE = re.compile(r"<base\b[^>]*>", re.I)
# Absolute URLs sitting in JS string literals, e.g. fetch("https://cdn/x.json").
JS_URL_RE = re.compile(r"""(["'])(https?://[^"'\s\\]+)\1""")
JS_ASSET_EXT = re.compile(
    r"\.(?:json|png|jpe?g|gif|svg|webp|avif|ico|css|js|mjs|woff2?|ttf|otf|eot|"
    r"mp4|webm|mp3|ogg|wav|pdf|xml|txt)(?:$|[?#])", re.I)
CHARSET_RE = re.compile(rb"""charset\s*=\s*["']?\s*([-\w]+)""", re.I)

NAV_HINT_RE = re.compile(
    r"(?:^|[-_\s])(?:nav|navbar|navigation|menu|topbar|header|masthead)(?:$|[-_\s])", re.I
)

SKIP_SCHEMES = ("data:", "javascript:", "mailto:", "tel:", "about:", "blob:", "#")

HTML_TYPES = ("text/html", "application/xhtml+xml")
CSS_TYPES = ("text/css",)
JS_TYPES = ("javascript", "ecmascript")


def log(msg: str) -> None:
    sys.stderr.write(msg + "\n")
    sys.stderr.flush()


def norm_url(url: str) -> str:
    """Drop the fragment; keep query. Fragments never change what is fetched."""
    return urllib.parse.urldefrag(url)[0]


def is_skippable(ref: str) -> bool:
    r = ref.strip()
    if not r:
        return True
    low = r.lower()
    return any(low.startswith(s) for s in SKIP_SCHEMES)


@dataclass
class Resource:
    url: str
    local: str            # path relative to the output root
    kind: str             # page | css | js | asset
    content_type: str = ""
    written: bool = False


@dataclass
class Stats:
    pages: int = 0
    assets: int = 0
    bytes: int = 0
    failures: list = field(default_factory=list)
    skipped_external_pages: set = field(default_factory=set)


class Mirror:
    def __init__(self, entry: str, out: str, *, max_pages: int, max_depth: int,
                 menu_only: bool, delay: float, user_agent: str, timeout: int,
                 same_prefix: bool, retries: int, verbose: bool,
                 js_assets: bool = True):
        self.entry = norm_url(entry)
        parts = urllib.parse.urlsplit(self.entry)
        self.host = parts.netloc
        self.scheme = parts.scheme or "https"
        self.prefix = posixpath.dirname(parts.path.rstrip("/") or "/")
        if same_prefix and parts.path.rstrip("/"):
            # /en/  -> keep crawling inside /en
            self.prefix = parts.path if parts.path.endswith("/") else parts.path + "/"
        else:
            self.prefix = "/"
        self.out = os.path.abspath(out)
        self.max_pages = max_pages
        self.max_depth = max_depth
        self.menu_only = menu_only
        self.delay = delay
        self.ua = user_agent
        self.timeout = timeout
        self.retries = retries
        self.verbose = verbose
        self.js_assets = js_assets

        self.resources: dict[str, Resource] = {}
        self.page_queue: list[tuple[str, int]] = []
        self.seen_pages: set[str] = set()
        self.stats = Stats()
        self._used_locals: set[str] = set()

    # ------------------------------------------------------------------ fetch

    def fetch(self, url: str) -> tuple[bytes, str] | None:
        last = None
        for attempt in range(self.retries + 1):
            try:
                req = urllib.request.Request(url, headers={
                    "User-Agent": self.ua,
                    "Accept": "*/*",
                    "Accept-Encoding": "gzip, deflate",
                    "Accept-Language": "en-US,en;q=0.9",
                })
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    raw = resp.read()
                    enc = (resp.headers.get("Content-Encoding") or "").lower()
                    if enc == "gzip":
                        raw = gzip.decompress(raw)
                    elif enc == "deflate":
                        try:
                            raw = zlib.decompress(raw)
                        except zlib.error:
                            raw = zlib.decompress(raw, -zlib.MAX_WBITS)
                    ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                    return raw, ctype
            except Exception as exc:  # noqa: BLE001 - report, keep crawling
                last = exc
                if attempt < self.retries:
                    time.sleep(2 ** attempt)
        self.stats.failures.append({"url": url, "error": str(last)})
        log(f"  !! failed: {url} ({last})")
        return None

    # ------------------------------------------------------------- local paths

    def local_path_for(self, url: str, kind: str, ctype: str = "") -> str:
        parts = urllib.parse.urlsplit(url)
        host, path, query = parts.netloc, parts.path, parts.query

        if kind == "page":
            if not path or path.endswith("/"):
                rel = posixpath.join(path.strip("/"), "index.html")
            else:
                rel = path.lstrip("/")
                if not re.search(r"\.[a-zA-Z0-9]{1,6}$", rel):
                    rel += ".html"
            base = "" if host == self.host else posixpath.join("_external", host)
            candidate = posixpath.join(base, rel) if base else rel
        else:
            rel = path.lstrip("/") or "index"
            if host == self.host:
                candidate = posixpath.join("assets", rel)
            else:
                candidate = posixpath.join("assets", "_external", host, rel)
            if not posixpath.splitext(candidate)[1]:
                ext = mimetypes.guess_extension(ctype) if ctype else None
                candidate += ext or ".bin"

        if query:
            root, ext = posixpath.splitext(candidate)
            candidate = f"{root}.{hashlib.sha1(query.encode()).hexdigest()[:8]}{ext}"

        candidate = candidate.replace("\\", "/")
        candidate = "/".join(
            urllib.parse.unquote(seg).replace(":", "_").strip() or "_"
            for seg in candidate.split("/")
        )

        # A URL may want a path that an earlier URL already claimed as a
        # directory (or vice versa); disambiguate rather than clobber.
        if candidate in self._used_locals:
            root, ext = posixpath.splitext(candidate)
            candidate = f"{root}.{hashlib.sha1(url.encode()).hexdigest()[:6]}{ext}"
        self._used_locals.add(candidate)
        return candidate

    def resource_for(self, url: str, kind: str, ctype: str = "") -> Resource:
        url = norm_url(url)
        if url not in self.resources:
            self.resources[url] = Resource(url, self.local_path_for(url, kind, ctype), kind)
        return self.resources[url]

    def write(self, res: Resource, data: bytes) -> None:
        dest = os.path.join(self.out, res.local)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        if os.path.isdir(dest):
            dest = dest + ".file"
            res.local += ".file"
        with open(dest, "wb") as fh:
            fh.write(data)
        res.written = True
        self.stats.bytes += len(data)

    @staticmethod
    def relpath(from_local: str, to_local: str) -> str:
        rel = posixpath.relpath(to_local, posixpath.dirname(from_local) or ".")
        return rel if rel.startswith(".") else "./" + rel

    # -------------------------------------------------------------- discovery

    def in_scope_page(self, url: str) -> bool:
        p = urllib.parse.urlsplit(url)
        if p.scheme not in ("http", "https"):
            return False
        if p.netloc != self.host:
            self.stats.skipped_external_pages.add(p.netloc)
            return False
        if not p.path.startswith(self.prefix):
            return False
        if re.search(r"\.(zip|pdf|jpe?g|png|gif|svg|webp|mp4|mp3|css|js|ico|woff2?|ttf|xml|json)$",
                     p.path, re.I):
            return False
        return True

    def menu_links(self, html: str, base: str) -> list[str]:
        """Links inside navigation containers, in document order, de-duplicated."""
        found: list[str] = []
        for m in re.finditer(
            r"<(nav|header|ul|div)\b([^>]*)>(.*?)</\1>", html, re.I | re.S
        ):
            attrs = m.group(2)
            body = m.group(3)
            tag = m.group(1).lower()
            hint = NAV_HINT_RE.search(attrs) or 'role="navigation"' in attrs.lower()
            if tag == "nav" or hint:
                for a in re.finditer(r"""<a\b[^>]*\bhref\s*=\s*["']([^"']+)["']""", body, re.I):
                    ref = a.group(1)
                    if not is_skippable(ref):
                        found.append(norm_url(urllib.parse.urljoin(base, ref)))
        seen, ordered = set(), []
        for u in found:
            if u not in seen:
                seen.add(u)
                ordered.append(u)
        return ordered

    def all_links(self, html: str, base: str) -> list[str]:
        out = []
        for m in re.finditer(r"""<a\b[^>]*\bhref\s*=\s*["']([^"']+)["']""", html, re.I):
            ref = m.group(1)
            if not is_skippable(ref):
                out.append(norm_url(urllib.parse.urljoin(base, ref)))
        return out

    # ---------------------------------------------------------------- rewrite

    def handle_asset(self, raw_ref: str, base: str, owner_local: str) -> str | None:
        """Download the referenced asset if needed; return the local relative ref."""
        if is_skippable(raw_ref):
            return None
        abs_url = norm_url(urllib.parse.urljoin(base, raw_ref.strip()))
        p = urllib.parse.urlsplit(abs_url)
        if p.scheme not in ("http", "https"):
            return None

        known = self.resources.get(abs_url)
        if known and known.written:
            return self.relpath(owner_local, known.local)
        if known and not known.written and known.kind == "page":
            return self.relpath(owner_local, known.local)

        got = self.fetch(abs_url)
        if got is None:
            return None
        data, ctype = got
        kind = "css" if any(t in ctype for t in CSS_TYPES) else (
            "js" if any(t in ctype for t in JS_TYPES) else "asset")
        res = self.resource_for(abs_url, kind, ctype)
        res.content_type = ctype
        self.stats.assets += 1
        if self.verbose:
            log(f"    + {kind:5s} {abs_url}")

        if kind == "css":
            text = self.decode(data, ctype)
            text = self.rewrite_css(text, abs_url, res.local)
            self.write(res, text.encode("utf-8"))
        elif kind == "js":
            text = self.decode(data, ctype)
            text = self.rewrite_js(text, abs_url, res.local)
            self.write(res, text.encode("utf-8"))
        else:
            self.write(res, data)
        return self.relpath(owner_local, res.local)

    @staticmethod
    def decode(data: bytes, ctype: str = "") -> str:
        m = CHARSET_RE.search(data[:4096]) if data else None
        for enc in filter(None, [m.group(1).decode("ascii", "ignore") if m else None,
                                 "utf-8", "cp1252", "latin-1"]):
            try:
                return data.decode(enc)
            except (UnicodeDecodeError, LookupError):
                continue
        return data.decode("utf-8", "replace")

    def rewrite_css(self, css: str, base: str, owner_local: str, quote: str = '"') -> str:
        """Rewrite url()/@import. `quote` lets callers avoid nesting a quote
        character inside an HTML attribute that is already delimited by it."""
        def repl_url(m: re.Match) -> str:
            new = self.handle_asset(m.group("url"), base, owner_local)
            return m.group(0) if new is None else f"url({quote}{new}{quote})"

        def repl_import(m: re.Match) -> str:
            new = self.handle_asset(m.group("url"), base, owner_local)
            return m.group(0) if new is None else f"@import {quote}{new}{quote}"

        css = CSS_IMPORT_RE.sub(repl_import, css)
        return CSS_URL_RE.sub(repl_url, css)

    def rewrite_js(self, js: str, base: str, owner_local: str) -> str:
        """Point absolute URLs in JavaScript at the local copies.

        Two passes: URLs already in the resource map are always rewritten, and
        -- unless --no-js-assets is given -- absolute URLs that look like assets
        are fetched too, since a script may be the only place a file is named.
        Dynamically assembled URLs cannot be resolved statically and are left
        alone; run verify_mirror.py to see what remains.
        """
        if self.js_assets:
            for m in JS_URL_RE.finditer(js):
                url = norm_url(m.group(2))
                if url in self.resources or not JS_ASSET_EXT.search(url):
                    continue
                if urllib.parse.urlsplit(url).netloc:
                    self.handle_asset(url, base, owner_local)

        for url, res in sorted(self.resources.items(), key=lambda kv: -len(kv[0])):
            if not res.written or res.kind == "page" or url not in js:
                continue
            js = js.replace(url, self.relpath(owner_local, res.local))
        return js

    def rewrite_html(self, html: str, page_url: str, owner_local: str) -> str:
        base = page_url
        bm = BASE_RE.search(html)
        if bm:
            base = urllib.parse.urljoin(page_url, bm.group(1))

        def rewrite_tag(m: re.Match) -> str:
            tag = m.group(1).lower()
            attrs_src, selfclose = m.group(2), m.group(3)
            attrs = {}
            for am in ATTR_RE.finditer(attrs_src):
                val = am.group(4) if am.group(4) is not None else (
                    am.group(5) if am.group(5) is not None else am.group(6))
                attrs[am.group(1).lower()] = val or ""

            if tag == "link":
                rel = " ".join(attrs.get("rel", "").lower().split())
                if rel in LINK_REL_SKIP or (rel and rel not in LINK_REL_DOWNLOAD):
                    return m.group(0)
            if tag == "meta":
                key = (attrs.get("property") or attrs.get("name") or "").lower()
                if key not in META_URL_KEYS:
                    return m.group(0)

            replacements: dict[str, str] = {}
            wanted = list(URL_ATTRS.get(tag, [])) + URL_ATTRS["*"]
            if tag == "meta":
                wanted = ["content"]

            for attr in wanted:
                if attr not in attrs:
                    continue
                value = attrs[attr]
                if tag == "a" and attr == "href":
                    continue  # page links are rewritten after the crawl settles
                if tag == "form" and attr == "action":
                    continue  # a mirrored form has nowhere to post
                new = self.handle_asset(value, base, owner_local)
                if new:
                    replacements[attr] = new

            for attr in SRCSET_ATTRS & attrs.keys():
                parts = []
                changed = False
                for cand in attrs[attr].split(","):
                    cand = cand.strip()
                    if not cand:
                        continue
                    bits = cand.split()
                    new = self.handle_asset(bits[0], base, owner_local)
                    if new:
                        changed = True
                        bits[0] = new
                    parts.append(" ".join(bits))
                if changed:
                    replacements[attr] = ", ".join(parts)

            if "style" in attrs and "url(" in attrs["style"].lower():
                # Single quotes inside: the rebuilt attribute is double-quoted.
                new_style = self.rewrite_css(attrs["style"], base, owner_local, quote="'")
                if new_style != attrs["style"]:
                    replacements["style"] = new_style

            if not replacements:
                return m.group(0)

            def sub_attr(am: re.Match) -> str:
                name = am.group(1).lower()
                if name not in replacements:
                    return am.group(0)
                value = replacements[name].replace('"', "&quot;")
                return f'{am.group(1)}{am.group(2)}"{value}"'

            return f"<{m.group(1)}{ATTR_RE.sub(sub_attr, attrs_src)}{selfclose}>"

        html = STYLE_BLOCK_RE.sub(
            lambda m: m.group(1) + self.rewrite_css(m.group(2), base, owner_local) + m.group(3),
            html)
        html = TAG_RE.sub(rewrite_tag, html)
        html = SCRIPT_BLOCK_RE.sub(
            lambda m: m.group(1) + self.rewrite_js(m.group(2), base, owner_local) + m.group(3),
            html)
        return html

    def relink_pages(self) -> None:
        """Second pass: point <a href> at mirrored pages, now that all are known."""
        pages = {u: r for u, r in self.resources.items() if r.kind == "page" and r.written}
        for url, res in pages.items():
            path = os.path.join(self.out, res.local)
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                html = fh.read()

            base = url
            bm = BASE_RE.search(html)
            if bm:
                base = urllib.parse.urljoin(url, bm.group(1))

            def fix(m: re.Match) -> str:
                ref = m.group(2)
                if is_skippable(ref):
                    return m.group(0)
                target = norm_url(urllib.parse.urljoin(base, ref))
                hit = pages.get(target) or pages.get(target.rstrip("/")) or \
                    pages.get(target + "/")
                if not hit:
                    return m.group(0)
                return f"{m.group(1)}{self.relpath(res.local, hit.local)}{m.group(3)}"

            html = re.sub(r"""(<a\b[^>]*?\bhref\s*=\s*["'])([^"']+)(["'])""",
                          fix, html, flags=re.I)
            # A <base href> pointing at the live origin would defeat every
            # relative path we just wrote. Strip the whole tag -- matching only
            # up to the href would leave a stray ">" in the markup.
            html = BASE_TAG_RE.sub("", html)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(html)

    # ------------------------------------------------------------------- run

    def crawl(self) -> None:
        os.makedirs(self.out, exist_ok=True)
        log(f"Entry : {self.entry}")
        log(f"Output: {self.out}")

        got = self.fetch(self.entry)
        if got is None:
            raise SystemExit(f"Could not fetch entry URL: {self.entry}")
        data, ctype = got
        html = self.decode(data, ctype)

        seeds = self.menu_links(html, self.entry) if self.menu_only else \
            self.all_links(html, self.entry)
        seeds = [u for u in seeds if self.in_scope_page(u)]
        log(f"Menu/seed pages discovered: {len(seeds)}")
        for s in seeds:
            log(f"  - {s}")

        self.page_queue.append((self.entry, 0))
        self.seen_pages.add(self.entry)
        for s in seeds:
            if s not in self.seen_pages:
                self.seen_pages.add(s)
                self.page_queue.append((s, 1))

        first_body: dict[str, str] = {self.entry: html}

        while self.page_queue and self.stats.pages < self.max_pages:
            url, depth = self.page_queue.pop(0)
            body = first_body.pop(url, None)
            if body is None:
                got = self.fetch(url)
                if got is None:
                    continue
                raw, ctype = got
                if not any(t in ctype for t in HTML_TYPES) and ctype:
                    continue
                body = self.decode(raw, ctype)

            res = self.resource_for(url, "page")
            self.stats.pages += 1
            log(f"[{self.stats.pages:3d}] page {url}")

            rewritten = self.rewrite_html(body, url, res.local)
            self.write(res, rewritten.encode("utf-8"))

            if depth < self.max_depth:
                for link in self.all_links(body, url):
                    if link in self.seen_pages or not self.in_scope_page(link):
                        continue
                    self.seen_pages.add(link)
                    self.page_queue.append((link, depth + 1))

            if self.delay:
                time.sleep(self.delay)

        log("Relinking pages ...")
        self.relink_pages()
        self.write_manifest()

    def write_manifest(self) -> None:
        manifest = {
            "entry": self.entry,
            "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "pages": self.stats.pages,
            "assets": self.stats.assets,
            "bytes": self.stats.bytes,
            "external_hosts_downloaded": sorted({
                urllib.parse.urlsplit(u).netloc
                for u, r in self.resources.items()
                if r.kind != "page" and urllib.parse.urlsplit(u).netloc != self.host
            }),
            "external_pages_not_followed": sorted(self.stats.skipped_external_pages),
            "failures": self.stats.failures,
            "resources": [
                {"url": r.url, "local": r.local, "kind": r.kind}
                for r in sorted(self.resources.values(), key=lambda r: r.local)
                if r.written
            ],
        }
        with open(os.path.join(self.out, "mirror-manifest.json"), "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, indent=2)

        log("")
        log(f"Pages   : {self.stats.pages}")
        log(f"Assets  : {self.stats.assets}")
        log(f"Bytes   : {self.stats.bytes:,}")
        log(f"Failures: {len(self.stats.failures)}")
        log(f"Manifest: {os.path.join(self.out, 'mirror-manifest.json')}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url", help="entry URL, e.g. https://www.joinzad.com/en/")
    ap.add_argument("-o", "--out", default="site", help="output directory (default: site)")
    ap.add_argument("--max-pages", type=int, default=200)
    ap.add_argument("--max-depth", type=int, default=2,
                    help="link depth beyond the seed pages (default: 2)")
    ap.add_argument("--menu-only", action="store_true",
                    help="seed the crawl from navigation/menu links only")
    ap.add_argument("--all-links", dest="menu_only", action="store_false",
                    help="seed from every internal link on the entry page")
    ap.add_argument("--same-prefix", action="store_true",
                    help="stay under the entry URL's path (e.g. /en/)")
    ap.add_argument("--delay", type=float, default=0.3, help="seconds between pages")
    ap.add_argument("--timeout", type=int, default=30)
    ap.add_argument("--retries", type=int, default=3)
    ap.add_argument("--user-agent", default=DEFAULT_UA)
    ap.add_argument("-v", "--verbose", action="store_true", help="log every asset")
    ap.add_argument("--no-js-assets", dest="js_assets", action="store_false",
                    help="do not fetch asset URLs found in JavaScript string literals")
    ap.set_defaults(menu_only=True, js_assets=True)
    args = ap.parse_args(argv)

    Mirror(args.url, args.out,
           max_pages=args.max_pages, max_depth=args.max_depth,
           menu_only=args.menu_only, delay=args.delay,
           user_agent=args.user_agent, timeout=args.timeout,
           same_prefix=args.same_prefix, retries=args.retries,
           verbose=args.verbose, js_assets=args.js_assets).crawl()
    return 0


if __name__ == "__main__":
    sys.exit(main())
