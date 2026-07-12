# PulseSoc Native Home Production Layout Parity

Date: 2026-07-11

## Completed

- Preserved production section order: header, Pulse Network hero, Status rail, Pulse Composer, Pulse Radio layer, feed categories, feed cards, bottom navigation.
- Preserved wide structure: left command rail, center feed, right intelligence rail.
- Preserved native server-authoritative feed/status/composer data flows.
- Removed the non-production Home subtitle from the native Home command strip.
- Restored production-facing status empty copy.
- Added the production composer title and full production mode rail visibility to the existing native composer.
- Refined the existing native feed card toward production hierarchy: author/header controls, creator/visibility badges, social context, Like/Comment/Repost/Share/Save action row, and overflow safety actions.
- Added the production inline comment path directly on the Home feed card while keeping Post Detail as the full thread destination.

## Remaining Differences

- Some right-rail metrics remain lightweight feed/status-derived values until dedicated production APIs are available to native.
- Production desktop nav pills are represented through native tabs/drawer/command rail rather than a duplicated desktop browser nav inside the app shell.
- Inline comment attachment/photo/voice affordances are explicit native boundaries until production feed-card attachment contracts are available to the native app.
