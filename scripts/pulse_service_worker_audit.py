#!/usr/bin/env python3
"""Audit PulseSoc service worker registration and installability support."""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

# Brand icons carry a date stamp for cache busting, so every brand revision
# renames them. Match the shape, not the stamp -- what this audit cares about is
# that the worker precaches *an* icon of each size, not which generation it is.
ICON_CACHED = lambda px: re.compile(rf"/static/brand/pulsesoc-icon-{px}-\d{{8}}\.png")


def require(condition, message):
    if not condition:
        raise AssertionError(message)
    print(f"ok - {message}")


def main():
    bot = (ROOT / "bot.py").read_text(encoding="utf-8")
    root_sw = (ROOT / "static" / "sw.js").read_text(encoding="utf-8")
    static_sw = (ROOT / "static" / "service-worker.js").read_text(encoding="utf-8")
    install_js = (ROOT / "static" / "js" / "pulse_pwa_install.js").read_text(encoding="utf-8")
    require('@webhook_app.route("/sw.js"' in bot, "root service worker route exists")
    require('Service-Worker-Allowed' in bot and 'Cache-Control"] = "no-store, max-age=0"' in bot, "service worker response headers are safe")
    require('navigator.serviceWorker.register("/sw.js", { scope: "/" })' in install_js, "global install script registers root service worker")
    source, label = root_sw, "root service worker"
    require("self.addEventListener(\"install\"" in source, f"{label} has install listener")
    require("self.addEventListener(\"activate\"" in source, f"{label} has activate listener")
    require("self.addEventListener(\"fetch\"" in source, f"{label} has fetch listener")
    require("const DEBUG_SW = false;" in source, f"{label} keeps fetch logging disabled by default")
    require("if (DEBUG_SW) console.log" in source, f"{label} gates diagnostic logging")
    require("/manifest.json" in source, f"{label} caches manifest")
    require(ICON_CACHED(192).search(source), f"{label} caches 192 icon")
    require(ICON_CACHED(512).search(source), f"{label} caches 512 icon")
    require("offlineResponse" in source, f"{label} has offline fallback")

    # static/service-worker.js is no longer a second worker. b0c1cc9a merged the
    # fork back into sw.js and left this file as a tombstone that unregisters
    # itself -- it cannot be deleted, because a script fetch that 404s makes the
    # browser's update job fail and strands the old fork on every device that
    # has it. So the contract to audit here is the inverse of the one above: it
    # must NOT serve fetches, and it must still tear itself down.
    label = "static service worker tombstone"
    require("self.registration.unregister()" in static_sw, f"{label} unregisters itself")
    require("self.addEventListener(\"fetch\"" not in static_sw, f"{label} serves no fetches")
    # Unsubscribing without preserve_preferences would read as the user turning
    # push off, and this worker is gone afterwards so nothing would undo it.
    require("preserve_preferences" in static_sw, f"{label} preserves push preferences")
    print("pulsesoc service worker audit ok")


if __name__ == "__main__":
    main()
