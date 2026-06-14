# PulseSoc Premium Post Card Redesign Report

Date: 2026-06-14

## Root Causes

- The active `/pulse` core boot path still rendered the older post-card structure from `static/js/pulse_home_core.js`.
- The old engagement summary rendered metrics in a way that could collapse into unreadable text such as `comments0`, `reposts0`, `shares0`, or `views0`.
- Feed video media mounted as inline media controls instead of a lightweight preview that opens the full post/video viewer.
- The mobile card CSS did not force the creator header into a stable avatar + identity grid, so the header could stack awkwardly.
- Existing mobile action and metric rows were too bulky for a 390px viewport.

## Files Changed

- `bot.py`
  - Bumped Pulse feed CSS/JS asset versions to force the corrected renderer and stylesheet to load.
- `static/js/pulse_home_core.js`
  - Reworked the shared core post-card renderer structure for Home and profile feed surfaces.
  - Added clean creator header, top-right post menu, full-width rounded media, separated engagement summary metrics, clean actions, and slim comment composer.
  - Converted feed videos to lightweight previews with a play affordance and `post-video-chip`; tapping opens the post viewer instead of mounting full video controls in-feed.
- `static/css/pulse_desktop_feed.css`
  - Added premium social post card rules for shared Home/profile feed cards.
  - Added desktop max-width card layout, mobile no-overflow layout, aligned creator header, compact action row, and slim comment composer.
- `scripts/pulse_premium_post_card_audit.py`
  - Added a focused regression audit for the active asset versions and shared premium card renderer.

## Verification

- `node --check static/js/pulse_home_core.js`: PASS
- `python3 scripts/pulse_premium_post_card_audit.py`: PASS
- `git diff --check`: PASS
- Browser QA mobile `/pulse`: PASS
  - 12 premium cards rendered.
  - Image, video, and text cards present.
  - No horizontal overflow.
  - Metrics do not contain broken `comments0`/`reposts0`/`shares0`/`views0` text.
  - Action row renders `Like`, `Comment`, `Repost`, `Share`, `Save`.
  - Video cards render lightweight previews.
- Browser QA desktop `/pulse`: PASS
  - 12 premium cards rendered.
  - 760px max-width card presentation.
  - No horizontal overflow.
  - Same shared renderer used.
- Browser QA profile feed `/pulse/my-posts`: PASS
  - Profile feed uses the same premium card structure.
  - No horizontal overflow.
  - Header, menu, metrics, actions, and composer render correctly.
- Browser console errors: none observed during QA.

## Screenshots

- Mobile post card: `reports/pulse-post-cards-redesign/mobile-post-card-after-final.png`
- Desktop post card: `reports/pulse-post-cards-redesign/desktop-post-card-after-final.png`
- Profile post card: `reports/pulse-post-cards-redesign/profile-post-card-after-final.png`

## Remaining Notes

- The supplied user mockup was used as the visual target. The implementation keeps real backend feed data and does not inject fake placeholder posts.
- The full Reels/status UI is no longer mounted inside normal feed video cards by this renderer; video feed cards are previews that route to the full viewer.
