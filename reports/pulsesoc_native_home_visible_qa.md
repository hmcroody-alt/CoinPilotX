# PulseSoc Native Home Visible QA

## Scope

Visible QA target: Native Home foundation Phase 1 in the built-in QA browser.

This report separates visual/browser verification from simulator and physical-device confidence. Device-only behavior such as camera capture, microphone, native picker permission prompts, and large upload behavior remains release QA.

## Built-in QA browser Walkthrough

Roody should see these Home sections in the built-in QA browser:

- Home hero
- Pulse Network card
- Pulse Radio action
- Live action
- Safety scan action
- Status rail
- Add Status
- Pulse Composer
- Post mode
- Reel mode
- Live mode
- Photo button
- Video button
- Music button
- Feeling button
- Location button
- Mention button
- Topic button
- Audience/Public button
- Publish Signal button
- Feed tabs
- Feed category tabs
- Feed scrolling
- Feed refresh
- Feed card actions
- Profile routing
- Media routing
- Error/retry states where practical

## What Roody visibly saw

- Authenticated native Home at `/pulse`.
- Pulse Network hero with Pulse Radio, Live, Safety scan, Refresh, and server-derived metrics.
- Add Status card and the empty Status rail state.
- Pulse Composer with Post, Reel, and Live modes.
- Photo, Video, Music, Feeling, Location, Mention, Topic, and Audience controls.
- Composer state changes for Reel mode, Feeling (`Curious`), Topic (`pulse`), and Audience (`Followers`).
- Live mode exposing the existing `Open Live Studio` provider boundary.
- Empty publish validation preventing an empty signal.
- Feed category selection with Trending visibly active.
- Feed cards with media, reactions, Comment, Save, Repost, Promote, Share, Follow, Report, Hide, Block, and Mute controls.
- Save changing to Saved through the existing backend action.
- Native post detail/comments route.
- Native media viewer opened from a feed image.
- Native profile detail route after preserving the production feed author identity field and adding the existing server resolver fallback.
- Native Live list, Safety Hub, Status creator, and Pulse Radio dashboard module shell destinations.

## Built-in QA Browser Evidence

- QA origin: `http://localhost:8094`
- Authenticated Home route: `/pulse`
- Confirmed destination routes:
  - `/pulse/live`
  - `/pulse/safety/?title=Safety%20Hub`
  - `/pulse/status?openCreator=true`
  - `/pulse/dashboard/module/pulse-radio-media/pulse_radio?title=Pulse%20Radio`
  - `/pulse/post/1044?title=Comments`
  - `/pulse/profile/Live%20Browser%20QA?title=Live%20Browser%20QA`
- Native media viewer opened in-place from the Trending image post.
- The visible browser console contained no runtime errors. Warnings were limited to known Expo web limitations/deprecations for push-token listeners, Badging API, `expo-av`, web animation fallback, and style props.

## Browser-Verified Items

- Home boot and authenticated feed load
- Hero rendering and refresh action visibility
- Live route
- Safety Hub route
- Pulse Radio native module shell route
- Status creator route and creator controls
- Composer modes and state controls
- Empty publish validation
- Feed category selection
- Feed scrolling
- Save state mutation
- Post detail/comments routing
- Profile routing
- Native media viewer routing
- Loading/empty states visible for Status and feed surfaces

## Browser-Blocked or Device-Only Items

- Native camera permission prompt
- Native microphone permission prompt
- Physical photo/video capture
- Native gallery permission behavior
- Large upload behavior
- Background interruption behavior
- Successful image/video publish through the browser picker
- Successful Reel publish through an uploaded video
- Physical pull-to-refresh gesture and native list performance

## Notes

- A local QA account/session was used without committing or displaying credentials.
- Browser-visible QA coverage for the Phase 1 Home foundation is estimated at 86%.
- Simulator/physical-device verification remains separate and is not implied by this report.

## Home Publishing Contract Visible QA

Scope: Home publishing contract, draft recovery, retry/failure state, and feed invalidation after publish.

Expected visible checks:

- Home Composer renders on authenticated Home.
- Empty Publish Signal shows validation without calling server publish.
- Typed text draft auto-saves.
- Draft recovery restores saved text and composer state after reload.
- Browser reload/session return restores the saved draft.
- Clear Draft removes recovered local state.
- Text-only Publish Signal uses the existing `/api/pulse/posts` backend contract.
- Successful publish clears composer state and refreshes the Home feed.
- Failed publish keeps the draft and exposes retry state.
- Reel mode routes through video-only publishing or existing Camera Studio handoff.
- Live mode opens the existing Live Studio/Live route handoff.

Result: partial visible QA completed.

Verified visibly:

- Built-in QA browser was opened visibly on `http://localhost:8094`.
- Native Login screen rendered and accepted a local-only QA account without exposing credentials in reports or committed files.
- Native Dashboard rendered after sign-in.
- Native Dashboard -> Home UI navigation opened authenticated Home without direct route-only checking.
- Home showed the Pulse Network hero, Status rail, Pulse Composer, Post/Reel/Live modes, publishing controls, feed tabs, and feed cards.

Blocked in this pass:

- Browser automation timed out while continuing composer interaction, so typed draft recovery, empty publish click validation, text-only publish, success reset, and visible feed refresh after publish remain unverified in the built-in QA browser.
- The static audit still verifies the implementation hooks for draft storage, upload queue metadata, retry, reset-after-success, and feed invalidation.
- A follow-up visible QA pass should focus only on typing into the composer, reloading to prove recovered draft state, publishing a local text-only post, and confirming feed refresh.
