#!/usr/bin/env python3
"""Guard mobile Pulse video layouts against desktop/source regressions."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BOT = ROOT / "bot.py"
RENDERER = ROOT / "static/js/pulse_media_renderer.js"
REELS_CSS = ROOT / "static/css/pulse_reels_experience.css"
MEDIA_CSS = ROOT / "static/css/pulse_cinematic_media.css"


def expect(condition: bool, label: str) -> None:
    if not condition:
        raise AssertionError(label)
    print(f"PASS: {label}")


def main() -> None:
    bot = BOT.read_text(encoding="utf-8")
    renderer = RENDERER.read_text(encoding="utf-8")
    reels_css = REELS_CSS.read_text(encoding="utf-8")
    media_css = MEDIA_CSS.read_text(encoding="utf-8")

    expect("window.PulseVideo = PulseVideo" in renderer, "shared PulseVideo component is exported")
    expect("pulse_reels_experience.css?v=reels-global-ui-20260613b" in bot, "Reels mobile guard stylesheet loads after inline shell CSS")
    expect("canonicalMuxHlsUrl || muxHlsUrlValue || item.playback_url || directUrl" in renderer, "shared PulseVideo blocks raw upload preference when Mux exists")
    expect("const muxHls=m.mux_playback_id?`https://stream.mux.com/${m.mux_playback_id}.m3u8`" in bot, "Feed helper blocks raw media when Mux exists")
    expect("muxHls=media.mux_playback_id?`https://stream.mux.com/${media.mux_playback_id}.m3u8`" in bot, "Reels helper blocks raw media when Mux exists")
    expect("media.mux_playback_id?`https://stream.mux.com/${media.mux_playback_id}.m3u8`" in bot, "Status helper blocks raw media when Mux exists")
    expect("playsinline webkit-playsinline" in bot + renderer, "mobile videos keep inline playback attributes")
    expect('preload="metadata"' in renderer and "preload='metadata'" in bot, "mobile videos preload metadata")
    expect("@media (max-width: 900px)" in reels_css, "Reels mobile layout is scoped to mobile")
    for token in [
        "body:has(.reels-immersive) .layout > aside",
        ".reel-details-panel",
        ".reels-desktop-intel",
        ".reel-comments-preview",
        ".reel-inline-comment",
        ".reel-comments:not(.open)",
        "display: none !important",
        "height: 100dvh !important",
        "grid-template-columns: 1fr !important",
        "bottom: calc(var(--reels-bottom-safe) + 70px)",
        "right: 9px !important",
    ]:
        expect(token in reels_css, f"mobile Reels guard includes {token}")
    expect(".post.is-video .pulse-media-wrap" in media_css, "Feed mobile video compact rules are present")
    expect(".post.is-video .comment-box:not(.open)" in media_css, "Feed mobile video comments collapse by default")
    expect(".post.is-video .reaction-pill" in media_css, "Feed mobile video reaction row stays compact")
    expect(".pulse-media-fallback" in media_css and "display: none !important" in media_css, "media fallback is hidden until a real failure")
    expect(".pulse-media-wrap.is-broken .pulse-media-fallback" in media_css and "display: grid !important" in media_css, "media fallback appears only after broken state")


if __name__ == "__main__":
    main()
