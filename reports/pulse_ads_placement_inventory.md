# PulseSoc Ads Placement Inventory

Date: 2026-06-24

## Placement Strategy

PulseSoc ads are designed as sponsored signals that can fit the sci-fi UI without blocking feed, composer, navigation, or touch targets.

## Inventory

| Placement Key | Device | Type | Intended Surface |
| --- | --- | --- | --- |
| `feed_inline` | all | feed | Between feed posts |
| `feed_side_ufo_desktop` | desktop | side | Desktop side district |
| `feed_inline_ufo_mobile` | mobile | feed | Mobile inline card |
| `pulse_network_hologram` | all | network | Pulse Network panel |
| `creator_sidebar_signal` | desktop | sidebar | Creator sidebar |
| `marketplace_sponsor` | all | marketplace | Marketplace browse |
| `pulse_radio_sponsor` | all | radio | Pulse Radio |
| `video_pre_roll` | all | video | Video pre-roll shell |
| `status_interstitial` | mobile | status | Status viewer interstitial |
| `search_sponsored_result` | all | search | Search results |
| `dashboard_sponsor` | all | dashboard | User dashboard sponsor |
| `profile_sponsor` | all | profile | Profile surface |

## Rollout Note

The frontend hook is available in `static/js/pulse_ads_hooks.js`, but exact surface injection should be done per page with visual QA to avoid overlays, z-index issues, scrolling regressions, and mobile touch blocking.
