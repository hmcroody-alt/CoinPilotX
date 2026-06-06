# Profile Avatar and Cover Persistence Fix

## Root Cause

Avatar uploads had a durable save path, but cover uploads did not fully match that protection. The cover endpoint saved and returned success without rereading the row and comparing the saved URL. The shared Pulse shell also rendered the profile shortcut as a static placeholder, so navigation could make a freshly saved avatar appear to disappear.

## Fix

- Cover saves now verify the database update affected exactly one signed-in user row.
- Cover saves reread `cover_url`, `banner_url`, and `cover_position` after commit.
- Cover saves return `cover_url_cache_busted`, `banner_url`, and thumbnail cache-busted URLs.
- Profile update responses now include the current avatar and cover URLs.
- `/api/pulse/profile/me` now returns cover and banner URLs.
- The Pulse shell refreshes the mobile profile shortcut from the persisted avatar URL.
- Profile edit hydrates avatar and cover previews with cache-busted persisted URLs.

## Safety

- Avatar and cover updates remain scoped to the signed-in user.
- Profile media still accepts only JPG, PNG, and WebP image uploads.
- Existing durable CDN validation remains in place before saving profile media URLs.
- The UI no longer reports success when the DB update fails.
