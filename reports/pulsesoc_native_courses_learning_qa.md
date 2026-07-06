# PulseSoc Native Courses + Learning Practical QA

## Scope

Short authenticated QA hardening pass for the native Courses + Learning gateway.

This pass did not add a major feature. It verified the existing native course/lesson routes, entry points, backend-backed lesson interactions, fallback routing, and visible user feedback.

## QA Environment

- Local backend: `PORT=5107 DATABASE_URL=sqlite:////tmp/pulsesoc_events_qa.sqlite`
- Local API proxy: `http://localhost:5108`
- Native web QA: `http://localhost:8094`
- QA browser: built-in Codex browser
- QA account: disposable local QA account from `/tmp/pulsesoc_events_qa_credentials.txt`

## Routes Verified

Passed with no visible runtime error text:

- `/pulse/courses`
- `/pulse/courses?category=scam-defense`
- `/pulse/courses/1`
- `/education/lesson/crypto-basics-101`
- `/pulse/teachers`
- `/pulse/teacher-dashboard`
- `/pulse/creator-studio`
- `/pulse/settings`
- `/pulse/search`

## Functional Checks

Passed:

- Category browse rendered filtered `scam-defense` lessons.
- Course detail route rendered the safe native gateway.
- Lesson detail rendered overview, knowledge map, quiz preview, tutor input, progress control, and fallback rows.
- Tutor interaction returned a backend response for `crypto-basics-101`.
- Recent learning cache showed `Crypto Basics 101` after opening the lesson.
- Creator Studio entry showed `Courses and Learning`.
- Settings entry showed `Courses and Learning`.
- Search/Discovery `Learning` tab showed the native gateway shortcut.
- Teacher and teacher dashboard routes rendered safe fallback gateways.

Fixed during QA:

- `Mark Complete` saved progress through the existing backend but did not leave durable visible feedback in web QA. The native screen now shows an inline progress message after success/error while still using `/api/education/quiz/submit`.

Verified after fix:

- `Mark Complete` displays `Quiz progress saved.` on the lesson screen after backend save.

## Backend Checks

Local authenticated checks passed:

- `/api/mobile/auth/login` authenticated the local QA account.
- `/api/education/tutor` returned a lesson-scoped tutor response.
- `/api/education/quiz/submit` returned `ok: true` and `score: 100`.

## Console And Network Notes

- No visible runtime error text appeared on tested Courses/Learning routes.
- The browser console buffer still contained an older `ActivityInboxScreen` error: `ReferenceError: unreadCountsByCategory is not defined`. The timestamp and URL indicate it was not produced by the Courses/Learning route checks. It should be handled in an Activity Inbox hardening pass if it reproduces there.

## Offline/Cache State

Partially verified:

- Recent learning cache was visible while the API was available.
- Full offline Courses cache was not verified because when the API proxy was stopped, the app returned to the auth/login gate before the Courses screen could render cached data.

Assessment:

- This is an app-level offline-auth/session limitation, not a Courses data-loss, security, or production-breaking blocker.
- It should remain a later offline-auth hardening item.

## Fallback Routing

Verified by route rendering and visible gateway actions:

- Course catalog fallback.
- Course creation fallback.
- Teacher dashboard fallback.
- Teacher profile fallback.
- Lesson web fallback.
- Paid enrollment, checkout, teacher review, lesson authoring, and unsupported lesson media remain web/provider-owned.

## Design Quality

The native gateway preserves the PulseSoc visual direction through:

- Futuristic dark command-surface treatment.
- Glowing accent rail on lesson cards.
- Compact premium spacing.
- Clear backend-authority copy for sensitive course/payment/teacher flows.
- No user-facing internal design-system labels.

## Remaining Gaps

- Physical-device QA is not required for this foundation.
- Full native course catalog/detail requires dedicated JSON APIs.
- Paid enrollment, checkout, refunds, payouts, teacher approval, and advanced lesson media remain fallback-only.
- Offline cached Courses rendering behind the auth gate remains unverified.

## Result

No critical, security, data-loss, production-breaking, or future-development-blocking issue was found.

Next highest-value action: Native Seller/Store Management Foundation.
