# PulseSoc Native Courses + Learning Gateway Foundation

## Summary

The native app now has a Courses + Learning gateway that reuses the existing PulseSoc education backend and keeps course creation, paid access, teacher administration, checkout, and unsupported lesson media on safe web fallback.

This is a native client layer only. It does not duplicate course, teacher, entitlement, payment, moderation, compliance, or lesson-progress business logic.

## Production Codebase Inspection

Confirmed existing production surfaces:

- `/pulse/courses`
- `/pulse/courses/create`
- `/pulse/courses/<course_id>`
- `/pulse/teachers`
- `/pulse/teachers/<teacher_id>`
- `/pulse/teacher-dashboard`
- `/education`
- `/education/lesson/<lesson_slug>`

Confirmed existing JSON/backend APIs:

- `/api/education/categories`
- `/api/education/lessons`
- `/api/education/lesson/<lesson_slug>`
- `/api/education/progress`
- `/api/education/quiz/submit`
- `/api/education/tutor`
- `/api/pulse/courses/create`

Confirmed existing database/business-rule coverage:

- `education_lessons`
- `education_sections`
- `education_quiz_questions`
- `education_user_progress`
- `education_ai_tutor_logs`
- `pulse_courses`
- `pulse_lessons`
- `pulse_lesson_media`
- `pulse_student_enrollments`
- `teacher_profiles`
- `teacher_applications`
- `pulse_teacher_profiles`
- `pulse_teacher_applications`

## Native Work Completed

- Added `mobile-native/src/api/learning.ts`.
- Added `mobile-native/src/screens/CoursesLearningScreen.tsx`.
- Added native routes for:
  - `Courses`
  - `CourseDetail`
  - `LearningLessonDetail`
  - `TeacherProfileGateway`
  - `TeacherDashboardGateway`
- Added deep-link support for:
  - `/pulse/courses`
  - `/pulse/courses/<course_id>`
  - `/education/lesson/<lesson_slug>`
  - `/pulse/teachers`
  - `/pulse/teachers/<teacher_id>`
  - `/pulse/teacher-dashboard`
- Added notification routing for course, teacher, and lesson links.
- Added Creator Studio and Settings entry points.
- Added Search/Discovery learning gateway shortcut.
- Added native learning category browse, lesson list, lesson detail, knowledge map, quiz preview, tutor, and progress completion hooks.
- Added offline cache for categories, lessons, and recently opened lessons.

## Reuse-First Boundary

Native reuses existing server-authoritative behavior for:

- Lesson/category loading.
- Lesson detail and quiz payloads.
- Tutor responses and safety rules.
- Progress/quiz submission.
- Teacher profile and teacher dashboard fallback routing.
- Course creation eligibility and teacher approval.
- Paid-course readiness, checkout, and compliance fallback.

Native does not reimplement:

- Teacher approval.
- Course publishing.
- Paid enrollment.
- Checkout.
- Payouts.
- Lesson authoring.
- Course moderation/compliance review.
- Advanced video/lesson player behavior.

## QA Notes

Static checks and the audit script verify that the native route wiring, API reuse, fallback paths, and progress report are present.

Built-in QA browser route checks completed:

- `/pulse/courses`
- `/pulse/courses/1`
- `/education/lesson/crypto-basics-101`
- `/pulse/teachers`
- `/pulse/teacher-dashboard`
- Settings and Creator Studio entry points.

Observed browser behavior:

- `/pulse/courses` rendered native lesson discovery, categories, course gateway, teacher dashboard fallback, and seeded lesson cards.
- `/education/lesson/crypto-basics-101` rendered native lesson detail, overview, knowledge map, quiz preview, tutor entry, progress control, and fallback rows.
- `/pulse/courses/1` rendered the native course-detail gateway with explicit backend-authority/fallback messaging.
- `/pulse/teachers` and `/pulse/teacher-dashboard` rendered the native teacher gateway and fallback actions.
- No visible runtime error text appeared on the tested Courses/Learning routes.

Local QA backend checks completed:

- `/api/mobile/auth/login` authenticated the disposable local QA account.
- `/api/education/tutor` returned a lesson-scoped tutor response for `crypto-basics-101`.
- `/api/education/quiz/submit` returned `ok: true` and saved score `100` for `crypto-basics-101`.

Known QA note:

- The browser log buffer retained an older `ActivityInboxScreen` console error from the same session. It was not produced by the Courses/Learning routes checked here and is tracked as a separate Activity Inbox hardening concern if it reproduces during Activity Inbox QA.

Provider/device QA is not required for this foundation because the feature is primarily navigation, JSON content loading, and safe fallback routing.

## Remaining Gaps

- No dedicated JSON course catalog/detail endpoint was found; course catalog/detail remains a gateway/fallback rather than a full native course product.
- Native paid enrollment waits for backend/provider-native contracts.
- Native teacher dashboard remains fallback-only.
- Lesson video/player behavior remains fallback-only unless existing media payloads become native-safe.

## Next Recommendation

Recommended next highest-value native action: **Native Courses + Learning Practical QA Hardening**.

Reason:

- This foundation bridges several routes with mixed JSON and web-backed behavior.
- A short authenticated QA pass should validate route rendering, lesson loading, tutor/progress states, fallback behavior, and visual consistency before moving to another major module.
- This is a practical QA gate, not a long release-blocking loop.

Risk level: low to medium.

Estimated complexity: low.

Safest plan:

1. Run a short authenticated QA browser pass for course/lesson/teacher routes.
2. Verify native lesson list/detail loads from the backend or degrades to cached/offline states.
3. Verify unsupported course, payment, teacher dashboard, and media flows open safe fallback.
4. Fix only scoped blockers.
5. Then continue to the next highest-value native feature/action.
