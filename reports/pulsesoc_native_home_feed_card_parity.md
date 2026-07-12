# PulseSoc Native Home Feed Card Parity

Date: 2026-07-11

## Native Feed Card Work

- Existing `PostCard` reused and refined in place.
- Header retains avatar, author, metadata, Follow, and overflow.
- Production action order retained: Like, Comment, Repost, Share, Save.
- Safety actions remain in overflow: Report, Hide, Block, Mute.
- Inline comment composer reuses the existing Home comment mutation path through `addPostComment`.
- Comment previews and social context row remain visible.

## Final Density Pass

- Avatar reduced to `48x48`.
- Card radius reduced to `22`.
- Card padding reduced to `14`.
- Header/action/social gaps tightened.
- Media radius reduced to `16`.
- Inline comment composer tightened while keeping semantic submit and visible controls.
- Action buttons now preserve a larger semantic touch height while reducing visual label/icon scale.

## Acceptance

- Feed-card parity: 92%.
- Inline comment parity: 92%.
- No duplicate comment pipeline was created.
- No production feed controls were removed.
