# PulseSoc Native Pulse Composer — Complete Design and Deep Wiring

Date: 2026-07-12

Active subsystem: Pulse Composer

Reference: current physical iPhone 16 Pro Home capture supplied by the user

## Outcome

The Home Pulse Composer was rebuilt around a compact, width-safe hierarchy. The clipped mode and action rails are gone. Post, Reel, and Live remain visible at every supported width; primary actions form a responsive grid; secondary production creation paths live behind an explicit More control. The existing production post API, media upload pipeline, authentication, moderation response, draft persistence, and retry behavior remain authoritative.

No production WebView source or production bundle identity was changed by this Composer work.

## Production-to-native matrix

| Capability | Production source/contract | Native source | Result | QA boundary |
|---|---|---|---|---|
| Text post | `POST /api/pulse/posts` in `bot.py` | `api/feed.ts`, `HomePulseComposer.tsx` | Reused; empty validation, 3,000 limit, sending, success, failure, retry | Simulator + device |
| Audience | `public`, `followers`, `private` post visibility | `HomePulseComposer.tsx` | Explicit three-choice selector; saved in draft and sent unchanged | Simulator + API |
| Image/video | Pulse media upload service and media IDs | shared `useNativeMediaUpload` + `MediaUploadPreview` | Existing picker/upload/progress/cancel/retry reused | Permission prompts require device |
| Reel | Existing video post contract and Camera Studio | Reel mode + video validation + Reel Camera | Reuses video media ID and native Camera Studio | Camera/device required |
| Live | Existing native Live tab/Studio gateway | `onOpenLive` | Live mode transmit action opens the real native Live destination | Device session required |
| Music | Existing Pulse Music route | `onOpenMusic` | Real music surface opens from the primary grid | Simulator + device |
| Feeling | Production-compatible text metadata | local selection folded into the post body | Drafted and published without a second API | Simulator + API |
| Topic | Existing post `tags` payload | More > Topic | Uses existing `tags` field | Simulator + API |
| Marketplace | `/pulse/marketplace/create` | More > Marketplace | Opens the maintained production creation route | Authenticated route |
| Question | `/pulse/questions` | More > Question | Opens maintained production questions flow | Authenticated route |
| Draft | Native AsyncStorage draft | `pulsesoc.native.home.composer.draft.v1` | Debounced save, restore, clear, uploaded-media reuse | Simulator + device |
| Failure/retry | Production API errors | retained payload + retry | Text/media and audience remain preserved after failure | Network fault needed |

## Design corrections

- Replaced both horizontally clipped rails with a three-column mode selector and four-column wrapping action grid.
- Reduced visual density by separating title/readiness, identity/input/audience, modes, actions, and transmit footer.
- Replaced the decorative LIVE badge with an actual Live mode and enabled transmit gateway.
- Added focused border response, READY/DRAFT/SENDING feedback, pressed feedback, warning counter state, and a consistently placed transmit button.
- Uses authenticated avatar when available and an account-derived fallback instead of an internal placeholder label.
- Added a visible audience sheet rather than silently cycling privacy values.
- Kept advanced tools discoverable without allowing them to clip compact widths.

## Behavioral states

- Empty: transmit is visible and disabled.
- Focused/typing: restrained focus border; multiline input stays stable.
- Ready: readiness label changes and transmit enables.
- Sending: duplicate submission is disabled and copy changes to Transmitting.
- Success: server response clears the draft and refreshes Home.
- Failure: content remains, error is visible, and Retry Last Publish reuses the exact payload.
- Upload: shared preview exposes progress, retry, and cancel.
- Restored draft: persisted text, mode, audience, topic, feeling, asset/result are recovered.

## Verification

- `npm run --prefix mobile-native typecheck`: PASSED.
- `scripts/pulsesoc_native_pulse_composer_complete_audit.py`: PASSED.
- Existing production API and upload wrappers: statically verified as reused.
- Release simulator build: the first clean build was interrupted during the dependency compile and was not counted as passed.
- Physical iPhone 16 Pro Release build with development identity: PASSED; embedded `main.jsbundle` verified.
- Physical install: PASSED as `com.pulsesoc.nativeapp.dev` / `PulseSoc Native Dev`.
- Physical launch: PASSED through `devicectl`; no immediate launch-tool failure.
- App inventory after install enumerated the development app, but did not enumerate the App Store WebView app. No production app was uninstalled or overwritten by this mission, but side-by-side presence cannot be marked verified from the final device inventory.

## Physical-device and permission truth

Camera, photo-library, long-video upload, background/foreground upload, and final production publish require interactive user choices on the connected phone. The implementation routes these through the existing native permission and upload systems; no personal media was selected or uploaded automatically.

## Evidence

Evidence directory: `reports/screenshots/native-pulse-composer-complete-design-deep-wiring-2026-07-12/`

- `iphone16pro-before-reference.png`: user-supplied physical-device baseline showing the previous clipped Composer.
- A post-change device screenshot could not be captured with the installed command-line device tools. Visual post-change QA remains an interactive device check; it is not inferred from the successful launch command.

## Known limitations

- Production does not expose a standalone native location-picker contract in the inspected native layer, so no fake Location control is shown.
- Poll creation was not represented as a dedicated production route in the inspected backend; it is not presented as a dead native button.
- Marketplace and Question intentionally hand off to their maintained authenticated production routes until their own native subsystems are migrated.
- The App Store WebView app was not returned by the final `devicectl` app inventory, so its current presence must be checked visually on the phone before side-by-side preservation is marked passed.

## Next exact Composer test

On the connected iPhone 16 Pro: type a multiline post containing emoji, a hashtag, a mention, and a link; switch each audience option; background and reopen to verify draft restoration; attach safe test imagery; cancel once, retry once under a network fault, then publish and confirm the server-created post appears once at the top of Home.

Pulse Composer remains the active subsystem until that interactive publish matrix is completed. Status and other subsystems were not modified.
