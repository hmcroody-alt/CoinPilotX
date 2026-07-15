# PulseSoc Native Messenger New Chat Incident Closure

Date: 2026-07-14

## Outcome

The native Messenger New Chat dead entry is repaired. Both visible inbox entry points now open one native flow that searches the canonical PulseSoc user directory, opens or creates the canonical direct conversation, enters the existing native chat, sends the first message with a client message ID, and restores the conversation from the server-backed inbox/cache path.

The implementation is installed on the connected iPhone 16 Pro as the side-by-side development app. The simulator interaction is complete. Private production-account interaction on the physical phone remains explicitly user-gated; no personal contact, temporary production identity, credential, or private message was created by automation.

## Root Cause

- The top `New chat` action and the `New Chat` quick action both routed to the generic Search screen.
- The native client had no wrapper for the existing production user-search or direct-open routes.
- Direct thread and first-message idempotency relied primarily on application checks; legacy duplicate rows and concurrent requests could still race before storage uniqueness was enforced.

## Repair

- Added the dedicated `NewChat` route and `NewChatScreen`.
- Reused `GET /api/pulse/users/search` and `POST /api/pulse/messages/direct/open`.
- Added a client single-flight guard keyed by target user ID.
- Upserted the canonical opened thread into the existing inbox cache, then refreshed on inbox focus.
- Routed the Profile Message action through the same New Chat surface.
- Preserved authentication re-entry, rate-limit, server-error, retry, loading, empty, and no-result states.
- Added a `pulsesoc://pulse/messages/new` deep-link mapping.
- Reconciled legacy duplicate direct threads, memberships, and non-empty client message IDs before adding storage uniqueness indexes.
- Made concurrent direct-open and repeated first-message inserts return their canonical existing records after uniqueness conflicts.
- Narrowed two legacy native Messenger audits so unrelated authentication copy mentioning the WebView app cannot cause a false Messenger-coupling failure.

## Canonical Contract Proof

Controlled local users were used only in the local QA database.

- Four concurrent direct-open calls: all HTTP 200.
- Conversation IDs returned: one unique canonical ID.
- Reversed participant order: same canonical ID.
- Repeated first-message send with the same `client_message_id`: same server message ID.
- Stored direct threads for the pair: 1.
- Stored memberships: exactly one per participant.
- Stored first messages for the client ID: 1.
- Search excluded the signed-in user and did not expose email.
- Two-user delivery/realtime audit passed when run serially.

No duplicate user or profile logic was introduced.

## Xcode Simulator QA

Workspace: `mobile-native/ios/PulseSocNative.xcworkspace`

Scheme: `PulseSocNative`

Configuration: Debug

Xcode: 26.6

Runtime: iOS 26.5

The XCTest `testMessengerNewChatCreatesCanonicalConversationAndSendsFirstMessage` passed with zero failures on:

- iPhone 17e (compact)
- iPhone 17 Pro (standard/Pro)
- iPhone 17 Pro Max

The test proved inbox entry, native New Chat rendering, controlled user search, canonical recipient selection, first-message send, visible server reconciliation, and keyboard-dismissed message rendering.

Evidence directory: `reports/screenshots/native-messenger-new-chat-incident-closure-2026-07-14/`

Key evidence:

- `iphone17pro-01-inbox.png`
- `iphone17pro-02-empty.png`
- `iphone17pro-03-results.png`
- `iphone17pro-04-conversation.png`
- `iphone17pro-06-first-message-keyboard-dismissed.png`
- `iphone17e-06-first-message-keyboard-dismissed.png`
- `iphone17promax-06-first-message-keyboard-dismissed.png`

## Physical iPhone 16 Pro

- Model: iPhone 16 Pro
- iOS: 18.7.3
- Connection: available and paired through Apple CoreDevice tooling
- Developer Mode: enabled
- Build: Release, arm64, Apple Development signing
- Bundle identifier: `com.pulsesoc.nativeapp.dev`
- Display name: `PulseSoc Native Dev`
- API environment: `https://pulsesoc.com`
- Build: passed
- Install: passed
- Launch: passed
- Production App Store bundle targeted: no

Apple's installed-app inventory showed the development app but did not show a separate PulseSoc App Store bundle at verification time. Therefore this run proves the safe distinct identity and that production was not replaced, but it does not claim preservation of a production install that was not present in the device inventory.

Physical login, search, recipient selection, first-message send, and updated inbox require the user to use an existing private PulseSoc account on the phone. This is the only release-blocking acceptance item still open for this focused mission.

## Verification

- `npm ci --prefix mobile-native --no-audit --no-fund --progress=false`: passed
- `npm run --prefix mobile-native typecheck`: passed
- `EXPO_DOCTOR_ENABLE_DIRECTORY_CHECK=0 npx expo-doctor --verbose`: 17/17 passed
- Debug simulator build: passed
- Signed iPhone Release build: passed
- `pulsesoc_native_messenger_new_chat_audit.py`: passed
- `pulsesoc_native_messenger_audit.py`: passed
- `pulsesoc_native_messenger_device_qa_audit.py`: passed
- `messenger_core_audit.py`: passed
- `messenger_api_audit.py`: passed
- `pulse_messages_two_user_delivery_audit.py`: passed serially
- Compact/Pro/Pro Max XCTest: passed

## Known Limitations

- No controlled production QA identity was available, so physical private-account interaction was not fabricated.
- No WebView/native private-message screenshot comparison was captured; canonical route and storage reuse are proven in controlled contract tests.
- Offline/reconnect UI screenshots and restricted/deleted real-account cases remain for a dedicated controlled-account device matrix.
- A Metro development warning banner appears in simulator Debug evidence; it is absent from the installed Release bundle.

## Exact User Check

On the iPhone, open **PulseSoc Native Dev**, sign in with an existing PulseSoc account if needed, open **Messages**, tap **New chat**, search for a safe controlled contact, open the result, and send a harmless test message. Confirm the new conversation appears in the inbox after returning. Do not use personal content.

Messenger must remain the active subsystem until this physical check passes. The next focused mission should be Messenger physical-account and offline/reconnect closure, not a different subsystem.
