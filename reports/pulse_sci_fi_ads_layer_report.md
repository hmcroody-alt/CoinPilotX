# PulseSoc Sci-Fi Ad Layer Report

## Scope

Phase 4 turns approved PulseSoc ad payloads into native sci-fi sponsored experiences without bypassing the Ads Foundation, Delivery Engine, moderation, frequency caps, or tracking.

## Implemented

- Home now loads `static/js/pulse_ads_hooks.js`.
- Desktop sponsored rail cards are real `data-pulse-ad-zone` containers.
- UFO, hologram, radio, marketplace, and signal-card styles are rendered from approved delivery payloads.
- Mobile-compatible inline UFO placements are supported by the same renderer.
- Sponsored label, CTA, Hide, Report, and Why this signal controls are present on rendered ads.
- Image, muted video, and audio creative previews are supported.
- Video/audio media are lazy, visibility-controlled, and paused when the tab is hidden.
- Reduced-motion users receive static behavior.

## Security

- Ads are fetched only from `/api/pulse/ads/placements`.
- Write tracking uses CSRF-protected authenticated endpoints.
- Rendering uses DOM text APIs, not unsafe HTML injection.
- Destination URLs are validated through the click endpoint before opening.
- Delivery tokens and tracking nonces are used for impression, click, and event tracking.
- Unapproved creatives remain blocked by the delivery engine.

## Files

- `bot.py`
- `services/pulse_ads_service.py`
- `static/js/pulse_ads_hooks.js`
- `static/css/pulse_home_os.css`
- `scripts/pulse_sci_fi_ads_layer_audit.py`

## Remaining Risks

- Real advertiser media performance depends on creative file sizes and CDN behavior.
- Browser QA should be repeated after production deploy with real approved inventory.
