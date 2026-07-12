# PulseSoc Native Home Visual Parity

Date: 2026-07-11

## Visual Alignment

- Native Home uses the current production deep-space background, glass panels, subtle cyan/emerald borders, and dark elevated surfaces through shared tokens.
- Native Status rail now uses production-facing labels instead of alternate LogiNexus terminology.
- Native composer now presents as `Pulse Composer`, matching production Home.
- The production mode set is visible in the existing native composer as a horizontal rail on compact iPhones.
- Native feed cards now place `Follow` and the overflow menu in the author header, matching the production card hierarchy more closely.
- Native feed cards now use a production-shaped action row: Like, Comment, Repost, Share, and Save, with counts and selected states kept compact.
- Native feed cards now include the production social-context layer before comments, including reaction summary and `View all comments` routing.
- Native feed cards now render an inline comment composer with `Write a comment...`, semantic send, and supported comment affordances.

## Intentional Native Translation

- CSS blur/backdrop filters are approximated through native glass colors, border alpha, and restrained shadows.
- Web hover effects become press/focus states.
- Desktop card dimensions become adaptive iPhone proportions.
- Production inline comment photo/attachment/voice tools are represented as native, explicit unavailable boundaries on the card until those feed-card attachment contracts are exposed.
