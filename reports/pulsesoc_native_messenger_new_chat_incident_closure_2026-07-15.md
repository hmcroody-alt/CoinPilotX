# PulseSoc Native Messenger New Chat incident closure — 2026-07-15

Status: **IMPLEMENTED; RELEASE ACCEPTANCE BLOCKED ON PHYSICAL IPHONE INTERACTION**

Active subsystem: PulseSoc Native Messenger / Pulse Command  
Workspace: `mobile-native/ios/PulseSocNative.xcworkspace`  
Scheme: `PulseSocNative`  
Xcode: 26.6 (17F113)  
Development bundle: `com.pulsesoc.nativeapp.dev`  
Development display name: PulseSoc Native Dev

## Outcome

The real native New Chat path now has one shared entry coordinator, production-contract user search, canonical direct-thread open/create, concurrent-create protection, first-message reconciliation, immediate inbox cache notification, and explicit authentication/load/retry states.

The controlled iPhone 16 Pro simulator XCTest passed one real UI test with zero failures. It opened Messages, opened New Chat, searched the controlled backend user, opened the canonical conversation, sent the first message, received the reconciled message, and retained it after keyboard dismissal.

The signed Release build also built and installed on the connected cable-paired iPhone 16 Pro. iOS repeatedly refused launch because the phone was locked. The required physical interaction, WebView comparison, background/relaunch check, and physical screenshots therefore remain blocked and this report does not declare release closure.

## Root-cause table

| Step | Expected | Actual before this follow-up | Root cause | Fix |
|---|---|---|---|---|
| New Chat entry | Both visible entries open one real flow | Physical screenshot showed controls but no proven completion | Visible controls were insufficient evidence and entry behavior was not tied to observable recovery state | Both visible entries now call the same `openNewChat` coordinator |
| Inbox bootstrap | Distinguish genuine empty from failure | Empty shell could mask auth or request failure | Cache/load/auth states collapsed into an empty presentation | Added signed-in gate, cached-reconnect notice, service failure state, and Retry |
| Inbox people search | Search canonical users as well as loaded conversations | User results were discarded by inbox search | `searchMessenger` users were untyped and not consumed by the inbox | Normalize/filter public users and render actionable People rows |
| New Chat search | Latest query wins and can be cleared | A slow earlier query could replace newer results | No stale-request sequence guard | Added monotonic search sequence and accessible Clear control |
| Direct open/create | One canonical thread for a pair | Existing implementation required stronger incident proof | A UI route alone could not prove uniqueness or concurrency | Reused canonical `POST /api/pulse/messages/direct/open`, client singleflight, and backend uniqueness/idempotency tests |
| First send and inbox | Sent message updates inbox immediately | Inbox could lag until a later reload | Send reconciliation did not notify the cached inbox row | Update cached preview/timestamp after canonical send and publish cache updates to subscribers |
| Physical acceptance | Launch and execute on iPhone 16 Pro | Build installed, but launch was denied | Connected phone remained locked | User must unlock; then execute the 30-point physical checklist |

## Production-to-native contract

| Capability | Production authority | Native behavior | Result |
|---|---|---|---|
| User search | `GET /api/pulse/users/search?q=...` for dedicated New Chat; `/api/pulse/messages/search?q=...` for inbox search | Uses canonical endpoints and public result normalization; excludes self | Passed in controlled backend and simulator |
| Direct lookup/create | `POST /api/pulse/messages/direct/open` | One shared `openDirectConversation` coordinator returns canonical conversation ID | Passed in controlled backend and simulator |
| Pair uniqueness | Backend participant-pair uniqueness and idempotent direct-open behavior | Client singleflight plus server authority | Passed for repeated, reverse-order, and four concurrent opens |
| First message | Existing canonical conversation-message endpoint with `client_message_id` | Optimistic message reconciles to server message; cache preview updates after success | Passed in controlled backend and simulator |
| Inbox refresh | Canonical list plus local cache/realtime reconciliation | Cache subscribers insert/update one row by canonical conversation ID | Static/runtime audit passed; physical reload remains blocked |
| WebView continuity | Same production conversation and message records | No native-only backend, user, conversation, or message model introduced | Contract audit passed; signed-in WebView comparison remains blocked |

## Controlled backend evidence

`scripts/pulsesoc_native_messenger_new_chat_audit.py` passed and proved:

- Existing controlled user search succeeds without exposing email.
- Self is excluded.
- Four concurrent direct-open requests return one canonical conversation ID.
- Reversed participant order reuses the same conversation.
- Repeated `client_message_id` returns one canonical server message ID.
- Storage contains one direct thread, two unique memberships, and one message.

Additional passing audits:

- `scripts/pulsesoc_native_messenger_audit.py`
- `scripts/pulsesoc_native_messenger_device_qa_audit.py`
- `scripts/messenger_core_audit.py`
- `scripts/messenger_api_audit.py`
- `scripts/pulse_messages_two_user_delivery_audit.py`
- `scripts/pulsesoc_native_auth_continuity_audit.py`
- `scripts/pulsesoc_native_global_navigation_audit.py`
- `scripts/pulsesoc_native_realtime_sync_readiness_audit.py`
- `scripts/pulsesoc_native_mission_standard_audit.py`

## Simulator QA

### iPhone 16 Pro — PASSED

Result bundle:

`/tmp/PulseSocMessengerP0Derived/Logs/Test/Test-PulseSocNative-2026.07.15_22-38-20--0700.xcresult`

Result: 1 test, 1 passed, 0 failed, 0 skipped. iOS Simulator 26.5.

Verified through visible UI:

- Inbox opened.
- Top New chat opened the dedicated native New Chat screen.
- Keyboard-safe user search returned the controlled canonical user.
- Selecting the user opened the canonical direct conversation.
- First text message sent and reconciled.
- Sent message remained after keyboard dismissal.

### Compact simulator — BLOCKED BY QA HARNESS

A clean Compact iPhone simulator built successfully. The first command used an incorrect XCTest class selector and executed zero tests; it is not counted as a pass. The corrected selector ran one test, but the fresh development client had not attached to the local Metro bundle, so authenticated navigation never bootstrapped. This is recorded as a harness connection failure, not a product pass or a product assertion failure.

### Standard and Pro Max simulators — BLOCKED

The product flow is proven on the iPhone 16 Pro simulator, but the complete four-width matrix is not accepted yet. Standard, compact, and Pro Max require repeatable local-bundle attachment followed by the same nonzero XCTest. No zero-test run is counted.

## Physical iPhone 16 Pro

| Item | Status | Evidence |
|---|---|---|
| USB detection and pairing | PASSED | iPhone 16 Pro available and paired |
| iOS version | PASSED | 18.7.3 |
| Developer Mode | PASSED | Enabled |
| Signed Release build | PASSED | `PulseSocNative`, Release, arm64 |
| Side-by-side development identity | PASSED | `com.pulsesoc.nativeapp.dev`, PulseSoc Native Dev |
| Installation | PASSED | Updated development app installed |
| Launch | BLOCKED | iOS denied launch because device was locked |
| Top New Chat | BLOCKED | Requires unlocked physical interaction |
| Quick-action New Chat | BLOCKED | Requires unlocked physical interaction |
| Display-name and username search | BLOCKED | Requires controlled physical account |
| Existing/new canonical conversation | BLOCKED | Requires controlled physical account |
| First message and inbox update | BLOCKED | Requires harmless physical send |
| Background/foreground/relaunch | BLOCKED | Requires physical interaction |
| WebView/native canonical match | BLOCKED | Requires signed-in production WebView comparison |
| Network interruption/Retry | BLOCKED | Requires physical interaction |
| Dynamic Island/keyboard/navigation | BLOCKED | Requires physical interaction |

The production App Store application was not uninstalled, overwritten, renamed, or modified by this mission. The development bundle remains independently identified. Presence of the production app on the device must still be visually confirmed during the physical checklist.

## Required package and build gates

| Gate | Result |
|---|---|
| `npm ci --prefix mobile-native --no-audit --no-fund --progress=false` | Passed |
| `npm run --prefix mobile-native typecheck` | Passed |
| `EXPO_DOCTOR_ENABLE_DIRECTORY_CHECK=0 npx expo-doctor --verbose` | Passed, 17/17 |
| iPhone 16 Pro simulator XCTest | Passed, 1/1 |
| Signed physical Release build | Passed |
| Physical install | Passed |
| Physical launch and interaction | Blocked by locked device |
| `git diff --check` | Passed at report time |

## Evidence

- Screenshot index: `reports/screenshots/native-messenger-new-chat-incident-closure-2026-07-15/README.md`
- XCTest attachment manifest: `reports/screenshots/native-messenger-new-chat-incident-closure-2026-07-15/manifest.json`
- This incident report: `reports/pulsesoc_native_messenger_new_chat_incident_closure_2026-07-15.md`

## Release decision

Messenger New Chat is **not release-ready**. Implementation, canonical backend behavior, iPhone 16 Pro simulator UI, signed physical build, and installation are proven. The mandatory physical flow, WebView/native canonical comparison, relaunch restoration, network recovery, full simulator width matrix, and physical evidence are not yet proven.

No commit or push should be made until the physical acceptance flow passes, in accordance with the mission's explicit stop condition.

## Next exact action

Unlock the cable-connected iPhone 16 Pro and keep it awake. Launch PulseSoc Native Dev, then perform the top New chat and quick-action New Chat flow with a controlled existing account, send harmless text, verify the inbox after force-close/relaunch, and compare the canonical conversation in WebView. Messenger remains the active subsystem until that evidence passes.
