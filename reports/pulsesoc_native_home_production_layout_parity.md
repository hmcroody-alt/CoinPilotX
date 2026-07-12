# PulseSoc Native Home Production Layout Parity

Date: 2026-07-11

## Completed

- Preserved production section order: header, Pulse Network hero, Status rail, Pulse Composer, Pulse Radio layer, feed categories, feed cards, bottom navigation.
- Preserved wide structure: left command rail, center feed, right intelligence rail.
- Preserved native server-authoritative feed/status/composer data flows.
- Removed the non-production Home subtitle from the native Home command strip.
- Restored production-facing status empty copy.
- Added the production composer title and full production mode rail visibility to the existing native composer.

## Remaining Differences

- Native iPhone feed card comments still route to Post Detail instead of always rendering the production inline comment composer.
- Some right-rail metrics remain lightweight feed/status-derived values until dedicated production APIs are available to native.
- Production desktop nav pills are represented through native tabs/drawer/command rail rather than a duplicated desktop browser nav inside the app shell.
