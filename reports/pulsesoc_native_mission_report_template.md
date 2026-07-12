# PulseSoc Native Mission Report: <Feature or Page>

Date: YYYY-MM-DD

Mission slug: `<mission-slug>`

Production WebView route/surface: `<route or surface>`

Native route/screen: `<route and component>`

## Production source-of-truth map

- Production UI files inspected:
- Production backend/business-logic files inspected:
- Existing native files inspected:
- Reused contracts, logic, assets, tokens, and components:
- Native-only platform layers rebuilt:

## Production comparison

| Major state or interaction | Production observation | Native observation | Remaining difference | Classification | Evidence path |
| --- | --- | --- | --- | --- | --- |
| Default |  |  |  | Simulator verified | `reports/screenshots/<mission-slug>/...png` |

## Simulator device matrix

Record the output date of `xcrun simctl list devices available` and the exact devices used.

| Layout class | Device | OS | States inspected | Result | Evidence paths |
| --- | --- | --- | --- | --- | --- |
| Compact |  |  |  |  |  |
| Standard |  |  |  |  |  |
| Pro |  |  |  |  |  |
| Pro Max |  |  |  |  |  |

## Required state evidence

Use `Simulator verified`, `Code-path verified`, `Mock-state verified`, `Physical-device-only`, or `Not applicable` with a reason.

| State | Classification | Exact screenshot path or reason |
| --- | --- | --- |
| Default |  |  |
| Populated |  |  |
| Empty |  |  |
| Loading |  |  |
| Error |  |  |
| Offline |  |  |
| Reconnecting |  |  |
| Permission denied |  |  |
| Modal |  |  |
| Sheet |  |  |
| Keyboard open |  |  |
| Long content |  |  |
| Small screen |  |  |
| Large screen |  |  |
| Feature-specific interactions |  |  |

## Wiring and recovery

- Authentication and authorization:
- API and database-backed behavior:
- Realtime/socket behavior:
- Upload/download/media behavior:
- Notification and deep-link behavior:
- Permissions:
- Offline/retry/reconnect/error recovery:
- Accessibility, safe areas, keyboard, gestures, and motion:

## QA results

- Typecheck:
- Expo Doctor:
- Applicable audits/tests:
- Simulator QA percentage and calculation:
- Production layout parity:
- Production visual parity:
- Feature parity:
- Interaction parity:
- Remaining visible differences:

## Physical-device release checklist

- [ ] Real camera/microphone behavior, if applicable
- [ ] Bluetooth/speaker routing, if applicable
- [ ] Lock-screen/background/app-killed behavior, if applicable
- [ ] Cellular transitions and real push delivery, if applicable
- [ ] Large real-world media upload, if applicable
- [ ] Hardware permission edge cases, if applicable

## Git and handoff

- Intended files:
- `git diff --check`:
- Commit:
- Push:
- Working-tree status and unrelated pre-existing changes:
- Next feature supported by production/native repository evidence:
