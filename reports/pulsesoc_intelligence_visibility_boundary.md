# PulseSoc Intelligence Visibility Boundary

## Architecture

The intelligence collector, scoring, forecast, learning, source-health, delivery, and administrative controls remain inside the admin-only **Galaxy Intelligence Center**. Regular users receive the output through focused PulseSoc features rather than an operations console.

## User Surfaces

Mission Control now exposes a plain **Intelligence** category with:

- Alerts
- Forecasts
- Watchlists
- Pulse Advisor
- Security Signals
- Crypto Signals
- Market Signals
- World Events
- Daily Briefing

The shared signal UI also provides Tech Signals, Creator Signals, and Music Signals. `/pulse/intelligence` is presented as **Pulse Alerts**; forecasts, briefing, preferences, and category routes render only the relevant user data.

## Admin Surface

`/admin/intelligence` remains the Galaxy Intelligence Center and requires an authenticated administrator. It exposes collector/source readiness, signal queues, forecasts, delivery/feedback counts, stream filters, and links to protected operational systems. The corresponding admin APIs reject non-admin requests.

## Role-Aware Search

Mission Control search indexes only module cards already returned for the current account. Regular users therefore cannot search for or discover the admin intelligence route. Administrators receive the admin card through the existing permission-aware dashboard registry and can search it normally.

## Public Language

User templates, the public homepage CTA, and Pulse AI knowledge use Pulse Alerts, Pulse Forecasts, Pulse Signals, Daily Briefing, and category-specific signal names. The Galaxy Intelligence Center name is confined to the protected admin template and internal engine metadata. The LogiNexus codename remains internal only.

## Security Checks

- User routes require an authenticated PulseSoc account.
- Admin page and API routes call the central admin-session guard.
- Regular dashboard payloads omit the admin widget and `/admin/intelligence` route.
- Search is built from role-filtered rendered cards, not a global endpoint.
- Collector and provider controls are not rendered on user pages.
- Public AI knowledge does not advertise the admin command center.

## QA

- Static visibility-boundary audit covers public/admin wording, routes, role-aware search, and report presence.
- Mission Control runtime audit verifies free, premium, and admin payload separation.
- Intelligence foundation and operating-system audits verify the existing event, forecast, notification, and user module contracts.
- Route-level audits verify the user signal surface and Mission Control search without exposing admin-only labels to regular users; responsive CSS guards cover mobile/desktop layout constraints.

## Limitations

- Watchlists and Pulse Advisor continue to use their existing mature PulseSoc routes rather than duplicating their implementations inside the signal renderer.
- Admin command-center sections link to existing protected operational pages or anchored views; this change does not create a second collector or forecast engine.
