#!/usr/bin/env python3
"""Check that a mirror produced by sitemirror.py is self-contained.

Walks every saved HTML and CSS file, resolves each asset reference, and reports:
  * references that still point at a remote host (the mirror would hit the net)
  * references that point at a local file that does not exist (broken offline)

Exit code is non-zero when either category is non-empty, so it can gate CI.
"""

from __future__ import annotations

import argparse
import os
import posixpath
import re
import sys
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sitemirror import (  # noqa: E402
    ATTR_RE, CSS_IMPORT_RE, CSS_URL_RE, LINK_REL_SKIP, SRCSET_ATTRS,
    STYLE_BLOCK_RE, TAG_RE, URL_ATTRS, is_skippable,
)

ASSET_TAGS = {"img", "script", "link", "source", "video", "audio", "embed",
              "object", "iframe", "track", "input", "use", "image"}


def refs_in_html(html: str):
    """Yield (kind, reference) for every asset-bearing reference in the page."""
    for m in STYLE_BLOCK_RE.finditer(html):
        yield from (("css", r) for r in refs_in_css(m.group(2)))

    for m in TAG_RE.finditer(html):
        tag = m.group(1).lower()
        attrs = {}
        for am in ATTR_RE.finditer(m.group(2)):
            val = am.group(4) if am.group(4) is not None else (
                am.group(5) if am.group(5) is not None else am.group(6))
            attrs[am.group(1).lower()] = val or ""

        if tag == "link":
            rel = " ".join(attrs.get("rel", "").lower().split())
            if rel in LINK_REL_SKIP:
                continue
        if tag == "a":
            if not is_skippable(attrs.get("href", "")):
                yield "link", attrs["href"]
            continue
        if "style" in attrs:
            yield from (("css", r) for r in refs_in_css(attrs["style"]))

        if tag in ASSET_TAGS:
            for attr in list(URL_ATTRS.get(tag, [])) + URL_ATTRS["*"]:
                if attr in attrs and not is_skippable(attrs[attr]):
                    yield "asset", attrs[attr]
        for attr in SRCSET_ATTRS & attrs.keys():
            for cand in attrs[attr].split(","):
                bits = cand.strip().split()
                if bits and not is_skippable(bits[0]):
                    yield "asset", bits[0]


def refs_in_css(css: str):
    for m in CSS_URL_RE.finditer(css):
        if not is_skippable(m.group("url")):
            yield m.group("url")
    for m in CSS_IMPORT_RE.finditer(css):
        if not is_skippable(m.group("url")):
            yield m.group("url")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", help="mirror directory to verify")
    ap.add_argument("--allow-remote-links", action="store_true",
                    help="do not flag <a href> that points off-site")
    args = ap.parse_args(argv)
    root = os.path.abspath(args.root)

    remote, missing, checked = [], [], 0
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            ext = os.path.splitext(name)[1].lower()
            if ext not in (".html", ".htm", ".css"):
                continue
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, root)
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                text = fh.read()

            if ext == ".css":
                found = [("asset", r) for r in refs_in_css(text)]
            else:
                found = list(refs_in_html(text))

            for kind, ref in found:
                checked += 1
                parsed = urllib.parse.urlsplit(ref)
                if parsed.scheme in ("http", "https") or ref.startswith("//"):
                    if kind == "link" and args.allow_remote_links:
                        continue
                    remote.append((rel, kind, ref))
                    continue
                target = urllib.parse.unquote(parsed.path)
                if not target:
                    continue
                if target.startswith("/"):
                    resolved = os.path.join(root, target.lstrip("/"))
                else:
                    resolved = os.path.normpath(
                        os.path.join(os.path.dirname(path), target))
                if not os.path.exists(resolved):
                    missing.append((rel, kind, ref))

    print(f"references checked : {checked}")
    print(f"still remote       : {len(remote)}")
    print(f"broken local paths : {len(missing)}")

    for label, rows in (("REMOTE", remote), ("MISSING", missing)):
        for rel, kind, ref in rows[:40]:
            print(f"  {label:7s} {rel} [{kind}] -> {ref}")
        if len(rows) > 40:
            print(f"  ... and {len(rows) - 40} more")

    if remote or missing:
        return 1
    print("\nOK: mirror is fully self-contained.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
