#!/usr/bin/env python3
"""Verify media search readiness against a live origin. Read-only.

Agent 09 companion to scripts/search_os/verify_sitemap_vs_live.py. Takes every
product URL from sitemap-products.xml, then checks the three things that decide
whether PulseSoc's media can appear in image or video search:

  1. Every <img> sits inside a ratio-locked .mkt-media box. The box carries the
     aspect ratio in CSS, so an image with no width/height attributes still
     reserves its geometry. An orphan <img> would be a real layout-shift risk.
  2. Every image URL is actually crawlable: 200, real image bytes, an image/*
     content type, and no X-Robots-Tag carrying noindex. A third-party host can
     stamp noindex without PulseSoc knowing -- image.mux.com does exactly that.
  3. The "Image unavailable" fallback span appears exactly once per <img>. It is
     emitted unconditionally and hidden in CSS, so its count tracking the image
     count is the proof that a raw-HTML hit on that string is a non-signal
     rather than evidence of a broken image.

Redirects are not followed: a product image that 302s somewhere is a finding,
not something to chase silently.

Usage:
    python3 scripts/search_os/verify_media_search_readiness.py
    python3 scripts/search_os/verify_media_search_readiness.py --base https://pulsesoc.com
    python3 scripts/search_os/verify_media_search_readiness.py --limit 5 --verbose

Exits non-zero if any check fails.
"""

from __future__ import annotations

import argparse
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field

GOOGLEBOT = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
GOOGLEBOT_IMAGE = "Googlebot-Image/1.0 (+http://www.google.com/bot.html)"

# Leading bytes of the formats a browser will actually decode. A host can serve
# content-type: image/jpeg over an HTML error page, so the type header alone is
# not evidence that an image exists.
IMAGE_MAGIC = (
    b"\xff\xd8\xff",      # JPEG
    b"\x89PNG\r\n\x1a\n",  # PNG
    b"GIF87a",
    b"GIF89a",
    b"RIFF",              # WebP (RIFF....WEBP)
    b"\x00\x00\x00",      # ISO-BMFF, covers AVIF/HEIC
)

IMG_TAG = re.compile(rb"<img\b[^>]*>", re.I)
MEDIA_BOX = re.compile(rb'class="mkt-media(?: [^"]*)?"')
ALT_ATTR = re.compile(rb'\balt="([^"]*)"', re.I)
SRC_ATTR = re.compile(rb'\bsrc="([^"]+)"', re.I)
LOC = re.compile(rb"<loc>([^<]+)</loc>")
JSONLD_IMAGE = re.compile(rb'"image"\s*:\s*(\[[^\]]*\]|"[^"]*")')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def fetch(url: str, ua: str, cap: int = 2_000_000):
    """Return (status, headers_lower, body). Never follows a redirect."""
    opener = urllib.request.build_opener(NoRedirect)
    request = urllib.request.Request(url, headers={"User-Agent": ua, "Accept": "*/*"})
    try:
        with opener.open(request, timeout=30) as response:
            headers = {k.lower(): v for k, v in response.headers.items()}
            return response.status, headers, response.read(cap)
    except urllib.error.HTTPError as exc:
        headers = {k.lower(): v for k, v in exc.headers.items()}
        return exc.code, headers, b""


@dataclass
class Report:
    failures: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    pages: int = 0
    images: int = 0
    boxes: int = 0
    fallbacks: int = 0
    missing_alt: int = 0
    with_srcset: int = 0
    total_bytes: int = 0

    def fail(self, message: str) -> None:
        self.failures.append(message)

    def note(self, message: str) -> None:
        self.notes.append(message)


def check_page(base: str, url: str, report: Report, image_urls: dict) -> None:
    status, headers, body = fetch(url, GOOGLEBOT)
    if status != 200:
        report.fail(f"{url} -> HTTP {status} (expected 200)")
        return
    report.pages += 1

    tags = IMG_TAG.findall(body)
    boxes = len(MEDIA_BOX.findall(body))
    fallbacks = body.count(b"mkt-media-fallback")
    report.images += len(tags)
    report.boxes += boxes
    report.fallbacks += fallbacks

    # 1. ratio-lock parity
    if len(tags) != boxes:
        report.fail(
            f"{url}: {len(tags)} <img> but {boxes} .mkt-media boxes "
            "-- an image outside a ratio-locked box will shift layout"
        )

    # 3. the fallback is unconditional, so its count must track the image count
    if fallbacks != len(tags):
        report.fail(
            f"{url}: {fallbacks} fallback spans for {len(tags)} images "
            "-- the 'Image unavailable' span is emitted per image"
        )

    for tag in tags:
        alt = ALT_ATTR.search(tag)
        if alt is None or not alt.group(1).strip():
            report.missing_alt += 1
            report.fail(f"{url}: an <img> has no usable alt text")
        if b"srcset" in tag:
            report.with_srcset += 1
        src = SRC_ATTR.search(tag)
        if src:
            href = src.group(1).decode("utf-8", "replace")
            if href.startswith("//"):
                href = "https:" + href
            elif href.startswith("/"):
                href = base + href
            image_urls.setdefault(href, url)

    # JSON-LD image claims must be URLs this page actually serves
    match = JSONLD_IMAGE.search(body)
    if match:
        claimed = re.findall(rb'"(https?://[^"]+)"', match.group(1))
        for raw in claimed:
            href = raw.decode("utf-8", "replace")
            if href not in image_urls:
                image_urls.setdefault(href, url + " (JSON-LD)")


def check_image(url: str, origin: str, report: Report, verbose: bool) -> None:
    status, headers, body = fetch(url, GOOGLEBOT_IMAGE, cap=4096)
    kind = (headers.get("content-type") or "").split(";")[0].strip()
    robots = headers.get("x-robots-tag") or ""

    if status != 200:
        report.fail(f"image {status}: {url}  (on {origin})")
        return
    if not kind.startswith("image/"):
        report.fail(f"image content-type {kind!r}: {url}")
    if "noindex" in robots.lower():
        report.fail(f"image carries X-Robots-Tag {robots!r} -- cannot be indexed: {url}")
    if not body.startswith(IMAGE_MAGIC):
        report.fail(f"image bytes are not a known image format: {url}")

    length = headers.get("content-length")
    if length and length.isdigit():
        size = int(length)
        report.total_bytes += size
        if size > 1_000_000:
            report.note(f"{size // 1024} KB is large for a page image: {url}")
    if kind == "image/jpg":
        report.note(f"non-standard content-type image/jpg (should be image/jpeg): {url}")
    if verbose:
        print(f"    ok {status} {kind:12s} {length or '?':>9s}  {url[:88]}")


def check_sitemaps(base: str, report: Report) -> None:
    for name, required in (
        ("sitemap-products.xml", True),
        ("sitemap-images.xml", False),
        ("sitemap-videos.xml", False),
    ):
        status, _headers, body = fetch(f"{base}/{name}", GOOGLEBOT)
        if status == 200:
            count = len(LOC.findall(body))
            print(f"  {name:24s} 200  {count} <loc>")
            if count == 0:
                report.note(f"{name} is served but lists nothing")
        elif required:
            report.fail(f"{base}/{name} -> HTTP {status} (required)")
        else:
            print(f"  {name:24s} {status}  absent")
            report.note(f"{name} does not exist -- that media type is not submitted")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="https://pulsesoc.com")
    parser.add_argument("--limit", type=int, default=0, help="check only N product pages")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    base = args.base.rstrip("/")

    report = Report()
    print(f"media search readiness :: {base}\n")

    print("sitemaps")
    check_sitemaps(base, report)

    status, _headers, body = fetch(f"{base}/sitemap-products.xml", GOOGLEBOT)
    if status != 200:
        print("\nCannot continue without sitemap-products.xml.")
        return 1
    urls = [u.decode("utf-8", "replace") for u in LOC.findall(body)]
    if args.limit:
        urls = urls[: args.limit]

    print(f"\nproduct pages ({len(urls)})")
    image_urls: dict[str, str] = {}
    for url in urls:
        check_page(base, url, report, image_urls)
    print(
        f"  {report.pages} pages, {report.images} <img>, {report.boxes} ratio-locked boxes, "
        f"{report.fallbacks} fallback spans"
    )
    print(f"  alt text missing on {report.missing_alt}, srcset present on {report.with_srcset}")

    print(f"\ndistinct image URLs ({len(image_urls)})")
    for url, origin in image_urls.items():
        check_image(url, origin, report, args.verbose)
    if report.total_bytes:
        count = len(image_urls) or 1
        print(
            f"  {report.total_bytes / 1_048_576:.2f} MB total, "
            f"{report.total_bytes // count // 1024} KB mean"
        )

    if report.notes:
        print(f"\nnotes ({len(report.notes)})")
        for note in sorted(set(report.notes)):
            print(f"  - {note}")

    if report.failures:
        print(f"\nFAILED ({len(report.failures)})")
        for failure in report.failures:
            print(f"  x {failure}")
        return 1

    print("\nPASS -- every image is crawlable, ratio-locked and labelled.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
