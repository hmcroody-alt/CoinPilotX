# PulseSoc Native Messenger Screenshot-Target Control Center Closure

Date: 2026-07-12

Active subsystem: Pulse Command / Messenger

Freeze decision: **not simulator-parity frozen**

Replacement decision: **not ready to replace the WebView Messenger**

## Outcome

This mission rebuilt the native Conversation Control Center around the supplied production hierarchy instead of the previous compact accordion. The active-chat Gear opens the sheet directly; the inbox search Gear reopens the last real conversation, falling back to a real loaded conversation. It never creates a generic fake settings context.

## Screenshot mapping

- Native baseline/inbox: retained real cached/server conversations, filters, quick actions and offline state; added a production-positioned Gear next to search and the exact search copy.
- Direct conversation: the header action is now named Gear and opens the complete Control Center over the active conversation.
- Control Center dashboard: added production title/subtitle, close control, contact context, quick actions, action grid, and real conversation-derived member/media/storage/unread/connection metrics.
- Conversation: all seven production rows are present. Export uses the native share sheet. Counts are derived from loaded messages. Unsupported navigation is explicitly locked.
- Notifications: all production rows are present and honestly locked because no inspected conversation-preference contract exists.
- Appearance: production rows are present. Dynamic Type and system motion remain authoritative; device-local Reduce Particles and High Contrast persist through AsyncStorage.
- Privacy: production rows are present but locked rather than pretending backend persistence.
- Media: production rows are present. Clear Cache confirms and removes local cache only; it does not delete server messages or remote media.
- Security: TLS/session protection is stated without an E2EE claim. Report and Block route to the existing Safety Hub.
- Productivity: all production rows are visible and locked pending production contracts.
- Storage: photo/video/voice/file counts and known attachment bytes derive from the loaded conversation.
- Accessibility: Large Text, Reduce Motion, High Contrast and Haptic Feedback persist as device-local native preferences.
- Danger Zone: the complete production row set is present. Unsupported destructive server actions remain unavailable; Report and Block use existing safety routing.

## Files changed

- `mobile-native/src/components/ConversationControlCenter.tsx`
- `mobile-native/src/screens/ChatScreen.tsx`
- `mobile-native/src/screens/MessengerScreen.tsx`
- `mobile-native/src/navigation/types.ts`
- `scripts/pulsesoc_native_messenger_control_center_closure_audit.py`
- `reports/pulsesoc_native_messenger_screenshot_target_control_center_closure_2026-07-12.md`
- `reports/pulsesoc_native_progress.md`

## Reuse and compatibility

The work reuses the existing Messenger APIs, message cache, active conversation route, native share sheet, Safety Hub, Pulse Command UI primitives, and existing chat/media/call stack. No backend, WebView, message model, realtime event, database, notification, group, room, AI, or production signing contract changed.

## Verification

- TypeScript: passed.
- Expo Doctor: 17/17 passed.
- Focused Control Center closure audit: passed.
- Existing WebView-target audit: passed.
- Existing Pulse Command exact-parity audit: passed after this change.
- iPhone 17 Pro simulator Debug build: passed (`PulseSocNative.xcworkspace`, `PulseSocNative`).
- Signed iPhone 16 Pro Release development build: passed with the side-by-side `com.pulsesoc.nativeapp.dev` identity.
- Physical install and command-line launch: passed. Device inventory showed production `PulseSoc` (`com.pulsesoc.app`) still installed beside `PulseSoc Native Dev`.
- Controlled Messenger core, API, and media-composer audits: passed. Push delivery was not configured because the controlled recipients had no device tokens; no delivery claim is made.
- `git diff --check`: passed for mission files.

## Honest completion

- Overall Messenger capability: 78%
- WebView UI parity: 72%
- Control Center visual/structural parity: 86%
- Control Center deep wiring: 48%
- Simulator QA: 58%
- Physical iPhone QA: 32%

The supplied screenshots are now recognizable in hierarchy and copy, but full parity is not claimed. Conversation-scoped notifications, appearance, privacy, productivity, security sessions, pin/archive/mute, remote deletion, full search/media/member destinations, and controlled persistence need backend contracts. Calls, Bluetooth, lock-screen push, real keyboard/media permissions, and long-session physical testing remain required.

Evidence directory: `reports/screenshots/native-messenger-screenshot-target-control-center-closure-2026-07-12/`. It records the build/install boundary; no automated physical screenshot capture was available, so interactive physical evidence is not claimed.

## Next exact Messenger mission

Add production-backed conversation preference and productivity contracts (mute, pin, archive, mark unread, notification/privacy overrides), then wire the currently locked rows and run the complete authenticated device matrix. Messenger remains the active subsystem.
