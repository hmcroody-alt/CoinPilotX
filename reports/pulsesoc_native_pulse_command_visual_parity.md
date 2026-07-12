# PulseSoc Native Pulse Command Visual Parity

Status: first visual-density pass completed.

## Production visual parity

- Current estimate: 80%.
- Inbox row density: 90%.
- Message bubble geometry: 86%.
- Composer geometry: 86%.
- Shared primitives: 88%.

## Refinements completed

- Reused `PulseCommand` primitives and reduced their visual weight:
  - avatar from 52 to 48
  - search height from 52 to 46
  - segment min-height from 40 to 36
  - panel padding reduced for Messenger density
- Reused `ConversationRow` and aligned it closer to production:
  - smaller title/preview/time text
  - tighter row padding/gaps
  - production-like translucent row background and subtle border
  - production-sized unread badge
- Reused `MessageBubble` and aligned it closer to production:
  - 17px base bubble radius
  - 6px tail corner on sender side
  - smaller metadata
  - production-like outgoing green surface
  - production-like incoming translucent surface
- Reused `ChatScreen` composer and made core controls closer to production:
  - round input/send/tool controls
  - 46px input/send height
  - smaller composer padding

## Remaining differences

- Production WebView uses CSS backdrop-filter and box-shadow; native uses alpha surfaces and lightweight borders.
- Production hover states are represented as native press states.
- Production voice waveform UI is not yet fully rebuilt natively.
- Production attachment sheet is deeper than current native attachment actions.
- Production action sheet has additional copy/forward/edit/message-detail affordances not yet fully parity-complete.
