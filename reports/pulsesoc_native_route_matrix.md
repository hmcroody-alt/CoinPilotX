# PulseSoc Native Route Matrix

Date: 2026-07-09

## Authenticated Representative Route Matrix

| Surface | Requested route | Result | Classification | Notes |
| --- | --- | --- | --- | --- |
| Home | `/pulse` | Passed | Native complete | Home hero, status, composer, categories, and feed shell rendered. |
| Activity Inbox | `/pulse/activity` | Passed | Native complete | Activity categories and unread state rendered. |
| Notifications legacy | `/pulse/notifications` | Passed | Native complete | Routed to Activity Inbox. |
| Search | `/pulse/search` | Passed | Native complete | Search categories and suggestions rendered. |
| Profile | `/pulse/profile` | Passed | Native complete | Profile summary, tabs, edit/settings actions visible. |
| Profile Edit | `/pulse/profile/edit` | Passed | Native complete | Edit form rendered. |
| Reels | `/pulse/reels` | Passed | Native complete | Empty-safe Reels state rendered. |
| Status | `/pulse/status` | Passed | Native complete | Status rail and create entry rendered. |
| Status Creator | `/pulse/status/create` | Passed | Native route | Routed to Status surface with create controls. |
| Camera Studio | `/pulse/camera/photo?target=feed` | Passed | Native complete with device boundary | Browser-safe camera fallback rendered. |
| Messenger | `/pulse/messages` | Passed | Native complete | Empty-safe conversation list rendered. |
| Calls | `/pulse/calls/qa-call-1` | Passed | Native complete with safe error | Call screen rendered safe `Call not found`. |
| Marketplace | `/pulse/marketplace` | Passed | Native complete | Marketplace browse surface rendered. |
| Seller Store | `/pulse/seller-store` | Passed | Native complete | Seller command layer rendered. |
| Buyer Orders | `/pulse/orders` | Passed | Native complete | Purchase timeline rendered. |
| Premium | `/pulse/premium` | Passed | Native complete | Server-authoritative premium state rendered. |
| Creator shorthand alias | `/pulse/creator` | Fixed and passed | Native alias | Now opens Creator Studio via `CreatorStudioAlias`. |
| Creator canonical | `/pulse/creator-studio` | Passed | Native complete | Creator Studio rendered. |
| Content Planner | `/dashboard/creator/content-planner` | Passed | Native shell | Legacy dashboard URL opens module shell. |
| Growth | `/pulse/growth` | Passed | Native complete | Growth Center rendered. |
| Intelligence | `/pulse/intelligence` | Passed | Native complete | Intelligence Center rendered. |
| Alerts | `/pulse/alerts` | Passed | Native complete | Alert Management rendered. |
| Settings | `/pulse/settings` | Passed | Native complete | Settings, support, legal, and account actions visible. |
| Security | `/pulse/settings/security` | Passed | Native complete | Security Center rendered. |
| Privacy | `/pulse/settings/privacy` | Passed | Native complete | Privacy Center rendered. |
| Support alias | `/pulse/support` | Fixed and passed | Native alias | Now opens Trust & Safety support shell. |
| Terms | Settings action | Verified as boundary | Provider fallback boundary | Terms remain safe web fallback, not native legal doc. |
| Privacy Policy | Settings action | Verified as boundary | Provider fallback boundary | Privacy Policy remains safe web fallback, not native legal doc. |
| Verification | `/pulse/verification` | Passed | Native complete | Verification Center rendered. |
| Account Health | `/pulse/account-health` | Passed | Native complete | Account Health/Appeals rendered. |
| Safety Hub | `/pulse/safety` | Passed | Native complete | Safety Hub rendered. |
| Courses | `/pulse/courses` | Passed | Native complete | Courses/Learning gateway rendered. |
| Dashboard | `/pulse/dashboard` | Passed | Native complete | User Dashboard rendered. |
| Dashboard Creator Shell | `/dashboard/creator/draft-studio` | Passed | Native shell | Legacy dashboard alias opens module shell. |
| Dashboard Economy Shell | `/dashboard/economy/marketplace` | Passed | Native shell | Legacy dashboard alias opens module shell. |
| Dashboard Crypto Shell | `/dashboard/crypto/alerts` | Passed | Native shell | Legacy dashboard alias opens module shell. |
| Dashboard System Shell | `/dashboard/system/feed-intelligence` | Passed | Native shell | Legacy dashboard alias opens `/pulse/dashboard/module/system-status/feed_status`. |
| Pulse AI | `/pulse/ai` | Passed | Native complete | Pulse AI rendered. |

## Back Navigation Matrix

| Flow | Result | Notes |
| --- | --- | --- |
| Home -> Activity -> Back | Passed | Returned to `/pulse`; auth state preserved. |
| Profile -> Edit Profile -> Back | Passed | Returned to `/pulse/profile`; auth state preserved. |
| Marketplace -> Seller Store -> Back | Passed | Returned to `/pulse/marketplace`; auth state preserved. |
| Settings -> Security -> Back | Passed | Returned to `/pulse/settings`; auth state preserved. |
| Dashboard -> System module shell -> Back | Passed | Returned to `/pulse/dashboard`; auth state preserved. |

## Deferred Release QA

- Physical iPhone push/tap routing.
- Provider-backed notification delivery.
- Camera and microphone hardware flows.
- Two-device calls/media sessions.
- Final legal-document native polish, if the product decides Terms/Privacy should become native screens instead of provider fallbacks.
