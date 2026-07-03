# PulseSoc Intelligence App Store CTA

## Summary

Selected PulseSoc Intelligence Pulses can now render futuristic action buttons for download, share, invite, and feature exploration without showing raw URLs in user-facing cards.

## Central URL

The official App Store URL is configured once through `PULSESOC_APP_STORE_URL`, with the default:

`https://apps.apple.com/us/app/pulsesoc/id6777591572`

## Where CTA Buttons Were Added

Default action helpers apply buttons only to relevant streams:

- PulseSoc Pulse: `Explore Feature`, `Share PulseSoc`.
- PulseSoc Discoveries: `Try It`, `Invite Friends`.
- System Pulse app/version/update events: `Open PulseSoc`, `Download PulseSoc`.
- Music Pulse: `Explore Music`, `Share PulseSoc`.
- Creator Pulse: `Open Creator Tools`, `Invite Friends`.

CTAs are intentionally not attached by default to Crypto, Market, World, Security, payment, account, private messages, emergency, or critical alerts.

## Rendering Behavior

- User-facing signal cards render compact glass buttons.
- Raw URLs are never inserted into signal body text.
- Internal links must start with `/`.
- External links must use HTTPS and the allowlist: `apps.apple.com` or `pulsesoc.com`.
- External opens use a safe new context.

## Share Behavior

- The UI uses the Web Share API when available.
- If native sharing is unavailable, it copies the official PulseSoc link and shows `PulseSoc link copied`.

## Security Validation

Validation is done in the backend helper and again in the browser:

- rejects `javascript:` style URLs,
- rejects protocol-relative URLs,
- rejects untrusted external domains,
- limits labels and metadata length.

## Pulse AI Integration

Pulse AI knowledge and feature registry now explain that PulseSoc should be downloaded or shared through official buttons or the App Store page, without dumping the raw link unless explicitly asked.

## Mobile QA

CTA rows are compact, wrapped, and mobile-safe. Buttons use 40px minimum height and do not create horizontal overflow.

## Desktop QA

Desktop CTA rows remain inside signal cards and use the same validated action handlers.

## Verification Result

- `pulsesoc_discoveries` live internal run stored a real accepted event through `ingest_signal`.
- `center_state(1)` returned action objects for `Try It` and `Invite Friends`.
- The signal body did not include a raw App Store URL; the URL is held only inside validated action metadata for button/share behavior.
- Client handler supports internal deep links, App Store opens, native share, and clipboard fallback.
- Local browser check reached `/pulse/intelligence` with no console errors and no horizontal overflow. The already-running Flask process still served the prior static asset version until restart, so CTA visual rendering should be rechecked after process reload/deploy.

## Known Limitations

- No App Store metadata is fetched during rendering. This is intentional for speed.
- Native share support depends on browser/device support; clipboard fallback is used where possible.
