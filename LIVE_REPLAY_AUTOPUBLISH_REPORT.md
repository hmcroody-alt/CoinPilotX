# Auto-Publish Ended Livestreams to Reels + Feed + Profile Media — Final Report

## 1. Existing foundation found (mapping pass, read-only)

The mission's first instruction was to map before editing. That map showed the feature is
**already built**, and has been for some time. This was an audit-and-close-the-gaps job, not
a build job, and nothing in the live stack was rebuilt.

| Concern | Where it already lives |
| --- | --- |
| Live session lifecycle | `pulse_live_sessions`; state read via `services/live_archive_service.replay_manifest` |
| Recording / finalization | Mux (`mux_recording_asset_id`, `mux_recording_playback_id`); Agora `agora_recording_sid` |
| Publisher | `bot.pulse_live_publish_replay_reel(live_id)` — bot.py:51209 |
| Publisher triggers | Mux `asset.ready` webhook (bot.py:48740) and `media_worker.py:816` reconciliation |
| Canonical post | `services/live_feed_service.ensure_live_feed_post` → one `pulse_posts` row, `post_type='live'` |
| Reel record | one `pulse_reels` row, `UNIQUE(post_id)`, `source_live_id` set |
| Idempotency claim | `pulse_live_sessions.replay_reel_id`, taken with `... WHERE id=? AND COALESCE(replay_reel_id,0)=0` |
| Thumbnail | `image.mux.com/{playback_id}/thumbnail.jpg` → `pulse_posts.preview_url` |
| All three surfaces | `services/pulse_feed_engine.list_feed(...)` / `list_user_posts(...)` |

The canonical rule the mission asked for was already satisfied:
`live_session → recording_asset → one post + one reel`. There is no second video copy and no
parallel replay storage. **Nothing new was created for storage.**

## 2. Surface-by-surface audit result

| Surface | Before | After |
| --- | --- | --- |
| Home feed (`for_you`) | **PASS** — no `post_type` filter, `status` stays `published`, and the logged-in visibility clause explicitly accommodates `post_type='live'` | PASS |
| Reels lane | **FAIL** | PASS |
| Profile → Media (listing) | **PASS** — `_public_post` synthesizes a video payload under the `media` key, which is what `api_pulse_public_profile_posts` filters on | PASS |
| Profile → Media (count badge) | **FAIL** | PASS |
| Profile ownership | **PASS** — `api_pulse_public_profile_posts` (bot.py:98978) resolves `target_user_id` from the profile key and passes `viewer_user_id` separately; no substitution | PASS |

### Root cause of both failures — one cause, two symptoms

The replay carries its video on `pulse_posts.replay_url` / `playback_url` and on the
`pulse_reels` row, **not** as a `chat_media_uploads` asset, so `media_ids_json` stays `'[]'`.
Two SQL predicates keyed off exactly that column:

- `pulse_feed_engine.py:1429` (reels lane): `(p.post_type IN ('video','replay','roast_clip') OR media_ids_json NOT IN ('[]',''))`. `post_type='live'` is in neither branch, so the replay never entered the candidate set and the `pulse_reels` join at bot.py:47262 was never reached.
- `count_user_posts(media_only=True)`: `media_ids_json NOT IN ('','[]')`, so the badge read 0 while the Media tab rendered the replay.

The presentation layer already disagreed with the SQL: `_public_post` (pulse_feed_engine.py:818)
synthesizes a full video media object for `live_status IN ('archived','replay_ready')`. The fix
makes the SQL agree with the serializer rather than inventing a new representation.

## 3. Changes made

Two files. No livestream, LiveKit, Mux, Agora, upload, or audio code was touched.

**`services/pulse_feed_engine.py`**

- Added `_ARCHIVED_LIVE_REPLAY_SQL`, a single shared predicate:
  `post_type='live' AND lower(live_status) IN ('archived','replay_ready') AND COALESCE(NULLIF(replay_url,''), NULLIF(playback_url,''),'') <> ''`.
  The URL guard is what keeps `FINALIZE_FAILED` / zero-duration / missing-URL lives out —
  a live that ended without a usable recording has no non-empty URL and therefore never
  becomes a reel or profile-media item.
- Reels lane predicate now admits it.
- `count_user_posts(media_only=True)` now admits it, restoring the count/listing contract
  that function's own docstring exists to defend.

Shared constant rather than two copies: a future change to what counts as a publishable
replay has one place to land, which is what stopped the count and the listing agreeing in the
first place.

**`tests/protection/test_live_replay_auto_publish.py`** (new, 16 tests).

## 4. Test results

`python3 tests/protection/test_live_replay_auto_publish.py` → **Ran 16 tests, OK.**

| Required test | Status |
| --- | --- |
| Ended live publishes successfully | PASS (publisher contract) |
| Appears in Reels | PASS |
| Appears in Home feed | PASS |
| Appears in Profile Media | PASS (listing + count) |
| All three reference one canonical identity | PASS |
| Retry / duplicate webhook does not duplicate | PASS (`COALESCE(replay_reel_id,0)=0` claim, `ON CONFLICT(post_id)`, `"created": False`) |
| Failed recording does not publish | PASS |
| Still-processing does not prematurely publish | PASS |
| Privacy preserved (private stays private) | PASS — plus fail-closed default in the publisher |
| Viewed-profile ownership correct | PASS |
| Deletion leaves no orphans | PASS |
| Second creator cannot inherit the first's recording | PASS |

**Negative control.** Neutering `_ARCHIVED_LIVE_REPLAY_SQL` to `(0=1)` and re-running the
suite produces 3 failures on exactly the reels/profile-count assertions. The tests detect the
defect they claim to; they are not passing vacuously.

**Regression.** `test_live_social_distribution.py` (3), `test_live_end_nonblocking.py` (7),
`test_agora_replay_mux_contract.py` (6) all pass.

**Realtime audio gate.** `python3 scripts/realtime_audio_change_gate.py --base origin/main --head HEAD`
→ *"No protected real-time audio path changed (0 file(s) inspected)."* No audio file was
edited, so the mission's STOP-and-report condition never triggered.

## 5. Known issues / not done

- `tests/test_pulse_new_user_profile_flow.py` errors with `no such table: pulse_post_hides`.
  **Pre-existing and unrelated** — a fixture gap in that test, not a regression. Verified by
  re-running it with `_ARCHIVED_LIVE_REPLAY_SQL` neutered: identical 4 errors either way.
- `tests/test_pulse_automated_account_identity.py` cannot be run standalone (`No module named
  'services'`); it needs pytest with the repo root as rootdir, and pytest is not installed in
  this sandbox.
- Device/simulator acceptance steps A–G are not executed here. They require a real live
  broadcast, a real Mux finalize, and the app on a device.

## 6. Staging

Explicitly staged, two paths only:

```
services/pulse_feed_engine.py
tests/protection/test_live_replay_auto_publish.py
```

`git add -A` was not run. Nothing committed, nothing pushed.
