# PulseSoc Member #000 Avatar Update

## Summary

`PULSESOC MEMBER #000` is the system/member fallback used by legacy public feed posts with `pulse_posts.user_id = 0`. It was not a normal `users` row, so the fix needed to live in the feed author/profile path instead of hardcoding a global frontend avatar.

## Where Member #000 Is Defined

- Backend author mapper: `services/pulse_feed_engine.py`
- Source condition: feed rows with no real user identity (`user_id = 0`) and no joined author fields
- Profile record: `arena_profiles` row with:
  - `user_id = 0`
  - `public_player_id = pulsesoc-member-000`
  - `display_name = PULSESOC MEMBER #000`

## Avatar Storage

PulseSoc feed author payloads use:

- `users.avatar_url`
- `users.avatar_thumbnail_url`
- `arena_profiles.avatar_url`
- rendered frontend fallback only when no real image exists

The official Member #000 asset was saved at:

```text
static/brand/pulsesoc-member-000-avatar.png
```

Technical details:

- PNG
- 1024x1024
- Square source
- Safe circular crop
- Dark futuristic city / cyan-purple galaxy identity
- No text clutter or random initials

## Database/Profile Field Updated

The backend now seeds/verifies the `arena_profiles.avatar_url` value for the specific Member #000 profile only:

```text
/static/brand/pulsesoc-member-000-avatar.png
```

The seeding code does not change normal users. If a real user has a profile image, their own avatar still wins. If a normal user has no image, the frontend shows the branded PulseSoc orb fallback instead of a random initial.

## UI Areas Covered

The shared feed author/avatar path feeds:

- feed post header
- post cards
- comment composer avatar
- notification actor payloads that reuse feed author data
- mobile/desktop feed surfaces using `pulse_home_core.js`

## Other Users

Other users are unaffected:

- Existing user avatars still render from their profile fields.
- Missing-avatar users no longer receive random initials in the shared feed renderer.
- The generic fallback is now a branded PulseSoc orb, not the Member #000 asset.

## Verification

Added:

```bash
venv/bin/python scripts/pulsesoc_member_000_avatar_audit.py
```

The audit verifies the PNG asset, the Member #000 profile row, backend author payload, and the removal of random initial fallback from the shared feed avatar renderer.
