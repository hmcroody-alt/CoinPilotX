# CoinPlotXAI Inc. Lighthouse Readiness Report

Date: 2026-05-10

## Code-Level Performance Improvements

- Service worker cache version bumped to refresh stale desktop/mobile caches.
- Service worker keeps navigation, auth, API, admin, debug, and account routes network-first or no-store.
- Static assets remain the only cache-first candidates.
- Added image width, height, and `decoding="async"` to module visuals to reduce layout shift.
- SEO pages use inline critical CSS and defer analytics.
- Lazy loading remains enabled for non-critical images.

## PWA SEO Compatibility

- Manifest and service worker remain linked.
- Public SEO pages expose canonical URLs and structured data while avoiding stale API caching.
- `/health`, `/reset-pwa`, API, admin, and auth routes remain protected from stale cache behavior.

## Lighthouse Targets

- SEO: 95+
- Best Practices: 95+
- Accessibility: 90+
- Performance: 90+

## Manual Lighthouse Step

Run Chrome DevTools Lighthouse against production after deploy:

1. Open `https://coinpilotx.app/`.
2. Run Mobile and Desktop Lighthouse.
3. Confirm installability, SEO metadata, tap targets, image sizing, CLS, LCP, and INP.

The local environment does not include a committed Lighthouse artifact because browser-based Lighthouse should be measured against the deployed Railway build.
