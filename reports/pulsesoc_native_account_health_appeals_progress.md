# PulseSoc Native Account Health + Appeals Center Foundation

Date: 2026-07-05

## Result

Built the native Account Health + Appeals Center in the parallel `mobile-native` app without changing production WebView routes or backend business logic.

The native implementation reuses existing PulseSoc account health, trust, verification, support, security, and notification routing behavior. The backend remains authoritative for enforcement state, strikes, restrictions, reports, verification decisions, appeals, account recovery, and moderation outcomes.

## Reused Existing PulseSoc Logic

- `GET /api/dashboard/account/state`
- Existing `/dashboard/account/health` protected web route
- Existing account health subsystem from `services/dashboard_account_command_center.py`
- Existing account health metrics: warnings, strikes, restrictions, security alerts, appeals available
- Existing verification appeal API through the native verification wrapper
- Existing support ticket API through the native support wrapper
- Existing security events API through the native account wrapper
- Existing Trust/Safety and Account/Security navigation
- Existing notification/deep-link routing patterns
- Existing cache/loading/error state patterns

## Implemented Natively

- `mobile-native/src/api/accountHealth.ts`
- `mobile-native/src/screens/AccountHealthAppealsScreen.tsx`
- `AccountHealth` and `AccountHealthWeb` stack routes
- `/pulse/account-health` deep link
- `/dashboard/account/health` native route alias
- Notification routing for account-health links
- Settings entry: `Account Health and Appeals`
- Trust/Safety entry: `Account Health`
- Account standing summary
- Health score and risk display
- Warning/strike/restriction counters
- Enforcement summary rows
- Appeal readiness list
- Verification appeal native submission where the existing API supports it
- Linked support cases
- Recent security signals
- Trust/Safety, Security Center, Verification Center, and protected web fallback actions
- Loading, refresh, offline-cache, validation, success, and error states

## Server-Authoritative Boundaries

The native app does not decide:

- Whether a warning, strike, or restriction exists
- Whether an appeal is valid
- Whether an appeal should be approved
- Whether account access is restricted
- Whether content moderation is correct
- Whether verification should be approved, rejected, suspended, or restored
- Whether a report/case should be escalated

Unsupported advanced enforcement history and appeal flows are routed to protected web fallback through `/dashboard/account/health`.

## Practical QA Status

Static verification passed for the implementation. Practical built-in QA browser checks verified:

- `/pulse/account-health` renders the native Account Health screen while authenticated.
- `/dashboard/account/health` routes to the native Account Health screen while authenticated.
- Settings shows `Account Health and Appeals`.
- Trust Center shows `Account Health`.
- Account standing summary, warning/strike/restriction counters, enforcement history, appeals, linked reports/cases, recent security signals, and recommendations render.
- `Submit supported appeal` safely shows `This appeal path needs the protected Account Health or Verification Center flow.` when the selected appeal is not supported by a native API.
- No browser console errors were captured during the final route/guard checks.

No physical-device-only behavior is required for this first foundation because the surface is account/API driven.

## Known Gaps

- Detailed strike/restriction row history is not exposed through a native JSON API today; the native app shows server-owned summary counts and uses protected web fallback for detailed enforcement history.
- Account-health strike/restriction appeal submission is not exposed as a native JSON endpoint today; the native app supports verification appeals via the existing verification API and routes other appeal types to protected web fallback.
- Seeded warning/strike/restriction QA fixtures are still needed for deeper appeal-state validation.
- Admin/provider review outcomes remain backend/admin QA.

## Production Safety

- Production WebView routes were not modified.
- No production app identity, push credential, entitlement, moderation, verification, or enforcement logic was changed.
- The current WebView app remains live and usable.

## Next Recommendation

Recommended next highest-value action: Native Blocks, Mutes, and Report Management Foundation.

Reason: Account Health, Trust/Safety, Messenger, Groups, Marketplace, Search, Profile, and Feed now expose safety/reporting entry points, but the native app does not yet have a central place to review blocks, mutes, report status, or safety actions. The production codebase already includes block/report/moderation and network-governance logic, so the native app can reuse backend authority while improving trust and recovery UX.
