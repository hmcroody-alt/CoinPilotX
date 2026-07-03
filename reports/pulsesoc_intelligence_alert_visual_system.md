# PulseSoc Intelligence Alert Visual System V2

## What Changed

PulseSoc Intelligence alerts now pass through a copy normalization layer before delivery. The normalized output gives every alert a consistent lock-screen title, short headline, short body, category label, priority badge, icon, accent color, and Pulse AI prompts.

The user-facing lock-screen title is now:

```text
PULSESOC ALERT
```

The alert headline is carried separately in push metadata and in the in-app card, so signals can read like official PulseSoc transmissions instead of generic notification text.

## Before / After Examples

Before:

```text
PulseSoc recorded a platform update from pulse_app_store_readiness.md.
```

After:

```text
PULSESOC ALERT
NEW DISCOVERY AVAILABLE
Pulse AI found a PulseSoc feature you may want to explore.
```

Before:

```text
PulseSoc Alert: BTC crossed $61,000
```

After:

```text
PULSESOC ALERT
BTC BREAKOUT DETECTED
Bitcoin crossed $61,000 with rising volume.
```

## Lock-Screen Copy Rules

- Title is always `PULSESOC ALERT` for Intelligence push notifications.
- Headline is short, uppercase, and mission-style where appropriate.
- Body is one short user-facing sentence.
- Raw URLs, filenames, route/debug terms, provider error labels, script names, and migration names are stripped.
- App Store links remain action metadata only and are not displayed as raw URLs.
- Urgent alerts use `alert`; standard Intelligence alerts use `pulse_signal`; digest alerts use softer sound behavior through existing preferences.

## In-App Card Rules

`/pulse/alerts` now renders V2 cards with:

- source label: `PULSESOC ALERT`
- category label
- priority badge
- icon and accent color
- strong headline
- short summary
- confidence
- CTA buttons
- Mark read, Save, Delete, and Ask Pulse AI actions

Cards use dark glass, cyan/violet lighting, category-specific accent borders, and compact mobile-safe spacing.

## Category Styling

- Crypto Signal: gold accent
- Market Signal: gold accent
- Security Signal: green accent
- World Event: violet accent
- PulseSoc Update: cyan accent
- Forecast / Daily Briefing: normalized priority labels
- Creator, Music, Technology, and System signals use their own icon/accent metadata

## CTA Behavior

Signal action buttons continue to render from trusted action metadata. Raw URLs are not displayed in the user-facing alert card. App Store and share actions remain represented as buttons such as `Download PulseSoc`, `Share PulseSoc`, `Explore Feature`, or `Invite Friends`.

## Pulse AI Integration

Each Intelligence card now includes `Ask Pulse AI`. It opens Messenger with the selected signal id so Pulse AI can explain:

- Explain this
- Why does it matter?
- What should I do?
- Am I affected?
- Summarize in one minute
- How do I stay safe?

## Safety Rules

Financial signal copy is filtered to avoid reckless language such as buy/sell commands, guaranteed profit, or pump/crash claims. Market and crypto signals use educational market-intelligence wording only.

Security signal copy is filtered to avoid exploit, malware, bypass, credential theft, and unauthorized hacking phrasing. User-facing security alerts stay defensive.

## QA Results

Verified:

- crypto alert copy normalization
- S&P 500 / major market copy normalization
- security alert copy normalization
- PulseSoc feature discovery copy cleanup
- locked-screen payload title/headline/body fields
- APNs subtitle support for alert headline
- Web Push service worker Intelligence formatting
- V2 in-app alert card layout
- Ask Pulse AI action
- raw URL and internal filename stripping

Commands run:

```bash
venv/bin/python -m py_compile bot.py services/*.py scripts/pulsesoc_intelligence_alert_visual_system_audit.py
node --check static/service-worker.js
node --check static/js/pulsesoc_intelligence_center.js
venv/bin/python scripts/pulsesoc_alert_delivery_activation_audit.py
venv/bin/python scripts/pulsesoc_intelligence_push_delivery_audit.py
venv/bin/python scripts/pulsesoc_intelligence_alert_visual_system_audit.py
git diff --check
curl -fsS http://127.0.0.1:5069/health
```

All commands passed.

## Remaining Limitations

Native mobile lock-screen visual layout is still controlled by iOS/Android notification surfaces. PulseSoc controls title, subtitle/headline, body, icon, sound, vibration, deep link, and metadata, while the operating system controls final physical rendering.
