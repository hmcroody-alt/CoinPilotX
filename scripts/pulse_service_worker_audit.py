#!/usr/bin/env python3
"""Audit Pulse service worker registration and installability support."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


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
    for source, label in [(root_sw, "root service worker"), (static_sw, "static service worker")]:
        require("self.addEventListener(\"install\"" in source, f"{label} has install listener")
        require("self.addEventListener(\"activate\"" in source, f"{label} has activate listener")
        require("self.addEventListener(\"fetch\"" in source, f"{label} has fetch listener")
        require("/manifest.json" in source, f"{label} caches manifest")
        require("/static/brand/pulse-icon-192-20260606.png" in source, f"{label} caches 192 icon")
        require("/static/brand/pulse-icon-512-20260606.png" in source, f"{label} caches 512 icon")
        require("offlineResponse" in source, f"{label} has offline fallback")
    print("pulse service worker audit ok")


if __name__ == "__main__":
    main()
