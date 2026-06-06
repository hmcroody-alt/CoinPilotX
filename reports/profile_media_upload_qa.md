# Profile Media Upload QA

## Covered Flows

- Avatar upload endpoint: `/api/pulse/profile/avatar`
- Cover upload endpoint: `/api/pulse/profile/cover`
- Profile save endpoint: `/api/pulse/profile/update`
- Current profile endpoint: `/api/pulse/profile/me`
- Profile edit page: `/pulse/profile/edit`

## Expected QA Result

- Upload avatar, navigate away, return, and refresh: avatar remains.
- Upload cover, navigate away, return, and refresh: cover remains.
- Save profile details after media upload: saved media URLs are not overwritten.
- Pulse header/profile shortcut uses the same saved avatar field.
- Feed, comments, messages, notifications, and creator cards continue to read `users.avatar_url` through existing identity/feed serializers.

## Validation Added

- `scripts/profile_avatar_cover_persistence_audit.py`
- `scripts/profile_media_upload_audit.py`
- `scripts/pulse_profile_cache_audit.py`
