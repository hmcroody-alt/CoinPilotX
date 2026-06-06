#!/usr/bin/env python3
"""Audit Pulse PWA install prompt behavior."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def require(condition, message):
    if not condition:
        raise AssertionError(message)
    print(f"ok - {message}")


def main():
    bot = (ROOT / "bot.py").read_text(encoding="utf-8")
    install_js_path = ROOT / "static" / "js" / "pulse_pwa_install.js"
    require(install_js_path.exists(), "Pulse PWA install script exists")
    source = install_js_path.read_text(encoding="utf-8")
    for token, label in [
        ("PROMPT_DELAY_MS = 60000", "60 second prompt delay"),
        ("DISMISS_COOLDOWN_MS = 24 * 60 * 60 * 1000", "24 hour dismissal cooldown"),
        ("beforeinstallprompt", "beforeinstallprompt listener"),
        ("event.preventDefault()", "native prompt is deferred"),
        ("deferredPrompt = event", "deferred prompt is stored"),
        ("deferredPrompt.prompt()", "install button calls prompt"),
        ("deferredPrompt.userChoice", "install flow waits for user choice"),
        ("appinstalled", "installed event is handled"),
        ("display-mode: standalone", "standalone mode detection"),
        ("window.navigator.standalone", "iOS standalone mode detection"),
        ("isIOS()", "iOS detection"),
        ("isSafariLike()", "iOS Safari detection"),
        ("Tap Share, then Add to Home Screen", "iOS Add to Home Screen instructions"),
        ("Install Pulse", "Pulse install branding"),
        ("PulseSoc.com", "PulseSoc.com branding"),
        ("Maybe later", "Maybe later dismissal"),
        ("data-pulse-pwa-dismiss", "dismiss/close controls"),
        ("pulsePwaInstallDismissedAt", "dismissal timestamp storage"),
        ("PulsePWAInstall", "QA hook is exposed"),
        ("usageMatured", "meaningful usage state is tracked"),
        ("promptWhenUsageIsMeaningful", "late beforeinstallprompt events can still show after 60 seconds"),
    ]:
        require(token in source, label)
    require("/static/js/pulse_pwa_install.js" in bot, "install script is injected globally")
    require("install-20260606-restore" in bot, "install script cache version is bumped")
    require("coinPilotXTrack" in source or "gtag" in source, "install events are logged safely")
    print("pulse PWA install prompt audit ok")


if __name__ == "__main__":
    main()
