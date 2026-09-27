# PulseDrop Reels — the multi-format extension

Mission report. This is the second half of PulseDrop: the part that turns a
chosen product into a 9:16 video and publishes it to the Reels surface.

It is an **extension**, not a parallel system. Eligibility, ranking, editorial
labels, diversity, leasing, idempotency and the account are all the same code
that publishes Signals. What is new is a renderer, a decision engine that picks
between formats, a second set of pacing controls, and an audio layer.

Read `docs/pulsedrop.md` first; this document only covers what Reels adds.

---

## 1. The shape of the extension

Three new modules and one new table, plus additions to existing ones:

```
distribution.py       NEW   which format(s), or neither
reel_composer.py      NEW   the renderer
audio.py              NEW   music beds
pulsedrop_renders           NEW table
publisher.publish_reel      new path alongside publish_signal
diversity                   Reel-specific cooldowns and caps
config                      9 Reel settings
```

`publish_signal` was not touched. If Reels are disabled, the code path through
`distribution.decide()` collapses to what it always did.

## 2. The decision engine

`distribution.decide(ranked_item, history, now)` → `(decision, reason, source)`.

| Decision | Reasons |
|---|---|
| `SIGNAL_ONLY` | `signal_is_the_right_format` |
| `REEL_ONLY` | `seller_video_leads`, `signal_unavailable` |
| `SIGNAL_AND_REEL` | `earned_both_formats` |
| `DEFER` | `min_publish_interval`, `daily_cap`, or a diversity reason |
| `SKIP` | `both_surfaces_disabled`, `no_publishable_format`, `no_enabled_format` |

The engine never picks a format it cannot execute. A Reel is only considered when
a source exists — either the seller's own video (Path A) or enough images to
compose one (Path B) *and* ffmpeg is present. `SKIP/no_publishable_format` is the
honest answer when a product has an image, Reels are on, and Signals are off.

## 3. Two gates, and the difference between them

```python
def _prefers_reel(source):        # Reel instead of Signal
    return source == SOURCE_SELLER_VIDEO

def _earns_both(item, source):    # Reel as well as Signal
    return (source == SOURCE_SELLER_VIDEO
            and item.score.total >= DUAL_FORMAT_MIN_SCORE   # 0.62
            and config.cross_format_cooldown_hours() == 0)  # default 48
```

Worth being precise about what this means in practice, because the names suggest
more than the defaults deliver:

- **A composed Reel never "leads".** `_prefers_reel` is seller-video-only. Path B
  output does not displace a Signal.
- **`SIGNAL_AND_REEL` is unreachable on defaults.** `cross_format_cooldown_hours()`
  defaults to 48, so the `== 0` clause is false. Dual-format publishing is an
  opt-in that requires an operator to deliberately zero that cooldown.
- **So a composed Reel reaches the surface by exactly one route:**
  `REEL_ONLY / signal_unavailable` — the Signal is blocked by a cooldown and the
  Reel surface is not. This is a real path and it does fire; it is just narrow.

That narrowness is the intended posture for a new surface. The system prefers to
publish one good thing than two mediocre ones, and the operator can widen it with
a setting change and no deploy.

## 4. Path A — the seller's own video

If the listing has a video, that is the Reel. It is transcoded to the platform's
frame spec but not re-authored: no overlays, no captions burned in, no cuts.

This is the better Reel by a wide margin, and the ranker already rewards it
(+0.35 on the media component). A seller who filmed their product gets a Reel
that looks like they made it, because they did.

## 5. Path B — composing from stills

When there is no video, PulseDrop builds one from the listing's photos: a slow
push-in on each image, crossfaded, held to the target duration.

`PULSEDROP_REEL_MIN_IMAGES` defaults to **1**, not 2. One still with a slow
push-in is a legitimate Reel; zero is a blank screen. The temptation during
rollout was to raise it to 2 so enabling Reels would be inert — that would have
made "Reels are live" mean nothing, so the considered default stands.

`PULSEDROP_REEL_MAX_IMAGES` caps at 4. Beyond that each image gets too little
screen time to read.

## 6. Frame spec and safe areas

`FRAME_WIDTH = 1080`, `FRAME_HEIGHT = 1920`, `FRAME_RATE = 30` — matched to the
Reels pipeline already in the product, not chosen fresh.

`_FILL_MIN_RATIO = 0.62`. A source whose aspect ratio is at or above that is
cropped to fill; anything squarer or wider is fitted over a blurred backdrop of
itself. Cropping a 1:1 product photo to 9:16 removes the product.

The encode is `libx264 -preset veryfast -profile:v high`, 2600k target / 3200k
max / 5200k buffer, `yuv420p`, `+faststart`. These match the existing Reels
pipeline rather than maximising quality: a PulseDrop Reel that needs a different
player configuration than every other Reel is a bug waiting to happen.

## 7. Render identity

A render is keyed `(listing_id, composition_version, source_fingerprint)`.

`source_fingerprint` is a SHA-256 over the source URLs, the target duration and
the frame spec. The consequences are all the ones you want:

- Retrying a failed render reuses the row; it does not re-encode from scratch.
- A seller swapping a photo changes the fingerprint, so the Reel is rebuilt.
- Bumping `COMPOSITION_VERSION` (`"pd-reel-v1"`) invalidates every render at once,
  which is the migration story for any future composition change.

## 8. Rendering is asynchronous, and never blocks the tick

`reel_composer.run_pending(limit=1)` drains **one** render per worker cycle, and
it runs *before* the lease check. Encodes therefore make progress on cycles where
the curator is not due, and a slow ffmpeg cannot hold the curator lease.

States: `pending` → `rendering` → `ready` | `failed`, with `attempts` against
`max_attempts` (3) and a claim (`claimed_by`, `claimed_at`) so two workers do not
encode the same row.

When the curator picks a product whose Reel is not yet encoded, it returns
`RENDER_PENDING` — a distinct outcome, not a failure, meaning "come back when the
video exists".

## 9. Publishing a Reel

`publish_reel()` follows the Signal state machine — claim → revalidate → post →
settle — with `surface="reel"` in the idempotency key, so a product can hold a
Signal claim and a Reel claim on the same day without collision.

The post is `post_type="video"` and references no media rows; the Reel carries
`video_url` directly. `_attach_reel()` upserts the `pulse_reels` row
`ON CONFLICT(post_id) DO UPDATE`, so a retry after a partial failure converges
instead of duplicating.

## 10. Separate pacing, separate caps, separate kill switch

Reels get their own controls rather than sharing the Signal ones:

`PULSEDROP_REELS_ENABLED`, `PULSEDROP_REEL_MIN_INTERVAL_SECONDS` (21600),
`PULSEDROP_DAILY_REEL_CAP` (3), `PULSEDROP_REEL_PRODUCT_COOLDOWN_HOURS`,
`PULSEDROP_REEL_SELLER_COOLDOWN_HOURS`, `PULSEDROP_CROSS_FORMAT_COOLDOWN_HOURS`.

A Reel is a heavier object than a Signal — it costs an encode, it occupies more
of a member's attention, and it is more conspicuous when it is bad. Six hours
between Reels against the Signal floor reflects that.

Turning Reels off does not touch Signals, and vice versa. Both are independent of
`PULSEDROP_ENABLED`, which stops everything.

## 11. Music: why it is not in the file

The obvious way to give a composed Reel a soundtrack is an ffmpeg audio stream at
render time. It is about four lines. It is also the one thing that cannot be
undone: once a track is in the pixels, withdrawing it means re-encoding and
re-uploading every Reel that used it, and until that finishes they are all still
playing it.

The platform already has the alternative. `pulse_reels.audio_track_id` joins
`pulse_reel_audio`; `pulse_reel_payload()` serialises the URL;
`ReelPlayerCard.tsx` plays it through a separate `Audio.Sound` over a muted video
track, and web does the same via `PulseMediaRenderer`.

That resolves **per request**, which buys two things:

- Withdrawing a track silences the entire back catalogue on the next fetch.
- Swapping a track costs one row update instead of a re-render.

There is a third fact that settles the argument: `bot.py` blanks
`attached_audio_url` whenever `audio_baked_in` is set. The two mechanisms are
**mutually exclusive by construction.** Baking music in would not produce two
tracks — it would silently disable the one that can be withdrawn.

`COMPOSED_HAS_AUDIO = False` in `reel_composer.py` now carries that explanation,
because it is the exact spot where the next engineer will reach for ffmpeg.

## 12. Music: why no track is cleared

`pulse_audio_tracks` holds 148 rows, of which 142 are `lifecycle_state='ACTIVE'`,
`approved_by_admin=1` and `commercial_use_allowed=1`. It would be easy to read
that as a cleared library and select from it.

It is not. Audited exhaustively:

- **Every single row** has `proof_url` set to the synthesized breadcrumb
  `artist-upload:<uid>:<ts>`, and `proof_file` empty. Rows with real proof: **0**.
- All 148 share one `license_type`: `artist rights confirmed upload`.
- All 148 share one `rights_statement`, the upload form's boilerplate.
- 147 of 148 were uploaded by one account. The single exception is a test row
  that was taken down.

That is a member ticking a checkbox. It is a perfectly reasonable basis for UGC —
the uploader carries the liability — and a completely unreasonable basis for the
platform's own brand account to publish against, where the liability is PulseSoc's.

So PulseDrop does not infer clearance. It requires it.

## 13. The clearance table

`pulsedrop_audio_beds` records that a named human cleared a specific track:
`audio_track_id UNIQUE`, `active`, `cleared_by`, `clearance_note`, `cleared_at`,
`revoked_at`.

What it deliberately does **not** store: title, artist, URL, licence type, or any
safety flag. Every one of those is re-read from the live `pulse_audio_tracks` row
at selection time. Clearance is an additional gate on top of the platform's
rules, never a substitute for them — so a takedown, a legal hold, an admin
un-approval or a `safety_status` change all keep applying after clearance, not
just at the moment of it.

Selection requires, simultaneously: the bed is `active`, the track is `ACTIVE`,
not removed, not on legal hold, still admin-approved, still commercial-use, still
`safety_status='approved'`, still has a URL — **and** `music_service
.attach_music_payload()` independently reports `is_creator_safe`. That last one
is a second opinion from code PulseDrop does not own.

**The resting state is an empty table, which means silence, and it requires no
switch and no deploy to stay that way.** `PULSEDROP_REEL_AUDIO_ENABLED` defaults
false on top of that.

## 14. Bed selection

```python
digest = sha1(f"pulsedrop-bed:{listing_id}").hexdigest()
bed    = fresh[int(digest[:8], 16) % len(fresh)]
```

Deterministic on `listing_id`, so re-running attach for a product picks the same
bed and the operation is idempotent. `fresh` excludes the last `RECENT_BED_WINDOW`
(3) tracks used, so a three-bed library rotates rather than repeating — unless
exclusion would empty the pool, in which case repetition beats silence.

The write is three statements in one transaction: `pulse_reels`
(`audio_track_id`, `sound_title`, start/end, `audio_baked_in=0`), an upsert into
`pulse_reel_audio` `ON CONFLICT(reel_id, audio_track_id)`, and a licence snapshot
into `pulse_content_music` with `source='pulsedrop_cleared_bed'`,
`original_audio_muted=1` and the clearance note. That last row means PulseDrop's
music appears in the platform's own licence ledger rather than in a private one.

## 15. Music can never cost a publication

`audio.attach()` runs **last** in `publish_reel` — after the Reel is rendered,
posted and claimed — and its result is never branched on. It returns `{}` rather
than raising, on any error.

Every failure mode in the audio path produces a silent Reel. None of them can
produce a missing one.

The test asserts that ordering from the **AST**, not from the source text. The
first version matched a literal `_settle(\n            publication_id,` and would
have gone red the next time someone reflowed the call — reading as "the publisher
stopped attaching music" when nothing had changed.

## 16. Operator surface for music

`/admin/pulsedrop` gains a "Reel music" section listing every clearance ever made
with its live state — `in rotation`, `withdrawn`, `taken down`, `legal hold` — plus
candidates. Two actions, `clear_bed` and `revoke_bed`, both through the existing
`apply_action()` path so they land in `log_admin_audit`.

`clear` (reset a setting) and `clear_bed` (approve a track) are unrelated
operations that share a verb; the code says so where they meet.

The page explains in prose why composed Reels are silent. "Nothing is cleared" and
"all four were taken down" are different incidents and must not look alike.

## 17. No fake checkout

A Reel's CTA runs through the same `hydration.py` as a Signal. Price, stock and
availability are read live on every request; the CTA is enabled only when the
product is genuinely purchasable, and otherwise the member gets a state chip and
no route.

There is no Reels-specific purchase path, no in-player checkout, and no cached
price. A video is a more immersive surface than a feed card, which makes a stale
price on it more misleading, not less.

## 18. Tests

29 test functions in `tests/pulsedrop/test_audio.py` (35 collected with
parametrization), 20 in `test_reel_composer.py`, registered in
`config/ci_test_manifest.json`. Six groups in the audio suite:

- nothing plays that nobody cleared
- withdrawal reaches what was already published (7-way parametrize over the
  takedown columns)
- selection is stable and spreads
- the write is complete and repeatable
- music never costs a publication
- the ops page can explain silence

They run against the real `bot.init_db()` schema, so the
`ON CONFLICT (reel_id, audio_track_id)` upsert is tested against the actual
unique constraint and not a stub that would accept anything.

**Verified by mutation**, not merely green: rewriting the publisher to branch on
the attach result turns the ordering guard red with the intended message.

## 19. Protected-path compliance

Real-time audio is hard-locked by `config/realtime-audio-protected-paths.json`.
None of this work touches a protected path, and `bot.py` is gated by diff content
rather than by path — `backend_diff_patterns` is `pulse_rtc_`,
`pulse_live_audio_v2_`, `AGORA_`, `LIVESTREAM_AUDIO_V2_`, `can_publish`,
`canPublish`, `audioV2Enabled`. No edit matches any of them.

`scripts/realtime_audio_change_gate.py --base origin/main --head HEAD` inspected
10 files and cleared the change; CI's "Detect protected audio changes" agrees.

Worth stating plainly: **Reel music is playback audio, not session audio.** It
does not open an `AVAudioSession`, does not create a microphone track, and does
not touch ownership arbitration. It is the same `Audio.Sound` path every other
Reel already uses.

## 20. Production status

Reels enabled `2026-09-27T14:56` UTC via `config.set_override`, no redeploy.
Live envelope: `reel_min_images=1`, `reel_max_images=4`, `reel_target_seconds=8`,
`reel_min_interval_s=21600`, `daily_reel_cap=3`, `cross_format_cooldown_h=48`.

At the time of writing `pulsedrop_renders` is empty and no Reel has published.
That is the expected state: the only run so far (13:58) predates the switch, and
the next tick is on the two-hour cadence.

Music infrastructure is merged and inert — **zero tracks cleared, and the switch
defaults off.** Both are true independently, so either alone is sufficient.

## 21. What remains

**For a human, not for me:**

- **Clear at least one track, or Reel music does nothing.** This is a rights
  judgement and needs someone who knows the provenance of the catalogue. If
  PulseSoc commissioned or owns the tracks on account 15, clearing is a one-click
  operation on `/admin/pulsedrop` and the note field is where the reason goes. If
  they are member uploads, none of them are clearable and the right answer is to
  license a small set specifically for this.

**Known limits, not defects:**

- **`SIGNAL_AND_REEL` is unreachable on defaults** (§3). Zero
  `PULSEDROP_CROSS_FORMAT_COOLDOWN_HOURS` to enable it.
- **Composed Reels reach the surface only via `signal_unavailable`.** Widening
  that means changing `_prefers_reel`, which is a product decision about whether a
  slideshow should outrank a photo post.
- **One eligible seller in production**, so every diversity rule that distinguishes
  sellers is currently inert.
- **No device QA yet.** Static checks do not replace watching a Reel play on a
  handset, and the RN side reaches no device until an EAS build ships.
