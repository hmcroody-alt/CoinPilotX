# Agent 9 — Media Privacy & Lifecycle Contract

Continuation of `AGENT_09_MEDIA_SEARCH.md` (commit `0cc757279`). That document
established what is **crawlable**. This one establishes what is **authorized**,
and it supersedes nothing in it.

Everything below was traced in code and measured against production
(`5bdf4e431`, deployed, confirmed at `/api/service/health`) on 2026-10-03.
Nothing here is inferred from a table name, a status string, or a provider's
documentation. Where I could not measure something, it is marked **UNKNOWN** and
left unresolved rather than closed with a plausible answer.

**This document does not activate video search, and no code in it is deployed.**

---

## 0. What carries over unchanged

The product-image finding from `0cc757279` stands and is **not** reopened:
product images are reachable, real image bytes, un-`noindex`ed, unsigned,
unexpiring, alt-texted, geometry-reserved. Both retractions stay retracted —
`"Image unavailable"` is unconditional markup, and the CLS finding was false.

Two things from that document are **narrowed** by new evidence, in the
conservative direction:

1. I wrote that first-party poster persistence after deletion was untested
   because no deleted Reel has a first-party poster. True, and still true (0 of
   10). But two **non-public** Reels do, and they are publicly retrievable right
   now (§5.3). The question was not untestable — I had tested the wrong state.
2. I treated `cdn.coinpilotx.app` as the solution to Mux's `noindex`. It is, for
   indexability. It is also the only host in this system that will serve
   unauthorized media to a crawler **and let it be indexed**, because it is
   header-clean (§5.3). The fix and the hazard are the same property.

---

## 1. The headline

Production has three categories of video media that the application considers
not-public. Every URL in all 438 probed was measured, not sampled:

| Application state | Entities | HLS retrievable | Poster retrievable | Of those, indexable |
|---|---|---|---|---|
| Reel `status='deleted'` | 10 | **10 / 10** | **10 / 10** | 0 |
| Reel `visibility='reel_only'` (non-public) | 16 | **16 / 16** | **16 / 16** | **2** |
| Video `status='archived'` (user-deleted) | 56 | **13 / 56** | 4 (52 have no poster URL) | 0 |

**39 playback URLs and 30 poster URLs currently serve media the application
considers not-public.** Two of those posters are additionally indexable (§5.3).

The archived-video row is the one I had wrong before measuring. From the code I
expected all 56 to be exposed, because nothing in the delete path touches the
provider. In fact 23 return `412` and 12 return `404` — so only 13 are
retrievable. The code-level reasoning was sound and the conclusion was still
too strong by 4×. That gap is the entire argument for probing rather than
reading (§4.2).

Reels, by contrast, are at **100%** in both non-public categories. The deletion
case was already flagged in `0cc757279` as the highest-priority lifecycle issue.
The **non-public** case is new, and it is worse in kind: those Reels were never
public at all. A deleted Reel was once published, so its URL could legitimately
have been seen. A `reel_only` Reel's media URL is reachable without the Reel
ever having been shown to anyone.

The mechanism is identical in all three cases and has one sentence:
**application state is stored only in PulseSoc's database, and nothing in this
repository can change a Mux asset's state.**

---

## 2. (A) The exact Reel deletion flow

Transport — `bot.py:97874` `api_pulse_reel_manage`, `DELETE
/api/pulse/reels/<id>`. Owner-gated through `can_manage`; delegates to the
engine with `surface="web_reels"`; emits a `pulse_reel_deleted` event.

Engine — `services/pulse_feed_engine.py:2066-2156` `delete_owned_reel`. The
entire effect of deleting a Reel:

```python
now = _now()
cur.execute(
    "UPDATE pulse_reels SET status='deleted', updated_at=? WHERE id=? AND user_id=?",
    (now, reel_id, requester_id),
)
if post_id:
    cur.execute(
        "UPDATE pulse_posts SET status='deleted', deleted_at=?, updated_at=? WHERE id=? AND user_id=?",
        (now, now, post_id, requester_id),
    )
```

followed by `pulse_mutation_audit.record(..., operation="reels.delete",
outcome="applied")` (`:2099`), `conn.commit()`, and a `PULSE_REEL_DELETED` log
line (`:2120`).

That is the whole flow. Two `UPDATE`s, one audit row, one log line.

- **No Mux API call.** Not to delete the asset, not to delete the playback id,
  not to change the playback policy.
- **No R2 call.** The poster object, when first-party, is untouched.
- **No CDN purge.**
- **No revocation of anything.**

Properties worth keeping in any remediation: the already-deleted branch
(`:2067-2086`) is terminal and returns `ok=True, changed=False`, so a retry is a
no-op rather than an error; and another account's reel returns `not_found`
rather than `forbidden`, so the response does not confirm the row exists.

**Videos use a different verb for the same thing.** `bot.py:89940`
`api_pulse_video_manage` `DELETE` runs `UPDATE pulse_videos SET
status='archived'` (`:89959`) and soft-deletes the source post. So a
user-initiated video deletion is stored as `archived`, not `deleted`. Any
eligibility predicate written against `status='deleted'` will treat all 56
user-deleted videos as live. This is the same class of trap as
`project_marketplace_status_predicate_drift` and must be stated explicitly in
every downstream contract.

### 2.1 Answers to the 15 deletion-flow questions

| # | Question | Answer | Evidence |
|---|---|---|---|
| 1 | What happens on Reel delete? | Two DB `UPDATE`s + audit row | `pulse_feed_engine.py:2089-2109` |
| 2 | Is the app row soft-deleted? | Yes, `status='deleted'`; row and media URLs retained | `:2090` |
| 3 | Is the Mux asset deleted? | **No** | no `DELETE` to Mux anywhere in repo (§3.2) |
| 4 | Is playback disabled? | **No** | wire: HLS 200 (§4) |
| 5 | Is the playback id revoked? | **No** | no playback-id mutation exists |
| 6 | Is a signed playback policy available / used? | **Available and already in use elsewhere**; not used for Reels | `media_service.py:413-420`, `mux_live_service.py:237` |
| 7 | Is the poster independently cached? | Yes, and separately from the manifest | Mux poster `max-age=604800`; first-party `max-age=31536000, immutable` (§6) |
| 8 | Can HLS still be fetched? | **Yes** | §4 |
| 9 | How long can caches retain it? | Manifest: not cached (`no-store`). Mux poster: 7 days. First-party poster: **1 year, `immutable`** | §6 |
| 10 | Is provider deletion intentionally delayed by a retention requirement? | **No such requirement exists** in code or policy (§9) | §9 |
| 11 | Can restoration / undo occur? | **No.** No restore path exists; deleted is terminal | grep: no `restore_reel`/`undelete` call site |
| 12 | Are moderation / legal-hold requirements relevant? | A takedown path exists and has the **same** durability failure; no legal-hold mechanism exists at all (§9) | `media_service.py:1590-1634` |
| 13 | Does account deletion follow a different media lifecycle? | Yes — a **weaker** one. It never touches media at all (§8) | `bot.py:7595-7705` |
| 14 | Do private / unpublished videos use the same provider access model? | **Yes, identically** — and this is the new finding (§4) | §4 |
| 15 | Can someone with an old playback URL retrieve media after deletion? | **Yes** | §4 |

Question 11 deserves one note. "No undo exists" is not an argument for immediate
provider deletion — it is an argument that nothing in the product currently
*depends* on the asset surviving, which narrows the remediation question
considerably (§10).

---

## 3. (B) (D) Provider asset lifecycle and playback-policy configuration

### 3.1 How assets are created

Every Mux asset PulseSoc creates is minted with an explicit playback policy, and
the default is public:

```python
# services/media_service.py:413-420
requested_policy = str(playback_policy or "public").strip().lower()
if requested_policy not in {"public", "signed"}:
    requested_policy = "public"
payload = {
    "input": input_url,
    "playback_policy": [requested_policy],
    "mp4_support": "standard",
}
```

Note the fallback: an unrecognised policy value becomes `public`. That is the
correct failure direction for availability and the wrong one for privacy, and it
is worth knowing before anyone threads a new policy string through this call.

Other creation sites, all public:

- `media_service.py:268` — Mux direct uploads: `"playback_policy": ["public"]`.
- `mux_live_service.py:137-138` — live streams, and `new_asset_settings` for the
  recording: both `["public"]`. So a live replay's asset is public from birth.

### 3.2 What can be changed afterwards: nothing

Every Mux HTTP verb in this repository is `GET`, `POST`, or `PUT`. There is
exactly one `PUT`:

- `mux_live_service.py:191` — `/live-streams/{live_stream_id}/disable`

There is no `DELETE /video/v1/assets/{id}`, no playback-id delete, no
playback-policy update, anywhere — not in the Reel delete path, not in the
account delete path, not in the moderation path, not in a worker, not in a
script. `services/media_service.py:422` is the only
`https://api.mux.com/video/v1/assets` call site and it is a `POST`.

**Therefore provider state is write-once in this architecture.** Application
state is mutable; provider state is not. Every finding in this document is a
consequence of that one asymmetry.

### 3.3 The codebase already documented the threat

This is not an oversight that nobody saw. `services/media_service.py:331-345`:

> `playback_policy` defaults to `"public"` because that is what every caller of
> this function has always got, and reels/live/replay are content whose whole
> purpose is to be reachable by a bare URL. Messenger is the one caller that
> passes `"signed"`: a conversation video is private, and a public playback id
> is an unguessable URL rather than an access check — anyone it leaks to can
> watch it forever, with no membership test and no expiry.

That reasoning is correct for a Reel that is public. It silently stops being
correct the moment the Reel is deleted or was never public — and nothing was
added to cover either case. The reasoning was never wrong; its premise just
expired.

### 3.4 Signed playback is already in the architecture

Remediation does not require inventing a new provider capability:

- Messenger already requests `"signed"` and the webhook already respects it
  (`bot.py:51524-51527` suppresses the HLS flip for `mux_playback_policy='signed'`
  rows, because a bare manifest URL is a 403 for those assets).
- `mux_live_service.py:237` already parameterises it:
  `"playback_policies": ["signed" if private else "public"]`.

And production carries a working demonstration at scale, not just in theory:
**25 assets outside Messenger — 23 archived videos and 2 public reels — already
return `412` for the manifest and `400` for the poster without a token** (§4).
A signed Mux asset is observably unreachable by bare URL on this very
deployment. Remediation therefore has a working reference implementation in its
own production data; it is a wiring problem, not a provider-capability problem.

---

## 4. (C) The public / private / deleted access matrix

Measured on the wire against production. Googlebot UA for manifests,
`Googlebot-Image/1.0` for posters. Redirects not followed. A `200` is only
counted as retrievable if real bytes came back.

```
ACCESS MATRIX -- application state vs public retrievability
(438 distinct URLs probed)

reel   deleted              playback  {200-RETRIEVABLE/noindex: 10}
                                      hosts {stream.mux.com: 10}
reel   deleted              poster    {200-RETRIEVABLE/noindex: 10}
                                      hosts {image.mux.com: 10}
reel   non-public:reel_only playback  {200-RETRIEVABLE/noindex: 16}
                                      hosts {stream.mux.com: 16}
reel   non-public:reel_only poster    {200-RETRIEVABLE/INDEXABLE: 2, 200-RETRIEVABLE/noindex: 14}
                                      hosts {cdn.coinpilotx.app: 2, image.mux.com: 14}
reel   public               playback  {200-RETRIEVABLE/noindex: 108, 412: 2, no-url: 5}
                                      hosts {stream.mux.com: 110}
reel   public               poster    {200-RETRIEVABLE/INDEXABLE: 70, 200-RETRIEVABLE/noindex: 38, 400: 2, no-url: 5}
                                      hosts {cdn.coinpilotx.app: 70, image.mux.com: 40}
video  archived(deleted)    playback  {200-RETRIEVABLE/noindex: 13, 404: 12, 412: 23, URLError: 8}
                                      hosts {other: 8, stream.mux.com: 48}
video  archived(deleted)    poster    {200-RETRIEVABLE/noindex: 4, no-url: 52}
                                      hosts {image.mux.com: 4}
video  public               playback  {200-RETRIEVABLE/noindex: 237}
                                      hosts {stream.mux.com: 237}
video  public               poster    {200-RETRIEVABLE/INDEXABLE: 13, 200-RETRIEVABLE/noindex: 133, URLError: 1, no-url: 90}
                                      hosts {cdn.coinpilotx.app: 14, image.mux.com: 133}
```

`INDEXABLE` means 200 with real bytes and **no** `X-Robots-Tag`. `noindex`
means 200 with real bytes carrying Mux's `noindex, nofollow`. Both are
retrievable; only the first can also be indexed.

Four things in that output are worth stating explicitly.

**1. Non-public and deleted Reels are at 100% exposure.** 26 of 26 playback
URLs and 26 of 26 posters. There is no partial protection to credit.

**2. `412` is signed playback, and it already exists outside Messenger.** 23
archived videos and 2 public reels return `412` for the manifest (and `400` for
the poster). That is Mux's response to a signed playback id presented without a
token. So **25 production assets outside Messenger are already behaving exactly
as remediation would require** — unreachable by bare URL. I did not establish
*why* those particular assets are signed; `media_service.py:413-420` would only
produce it for a caller explicitly passing `"signed"`. Cause **UNKNOWN**;
behaviour measured.

**3. Two public reels are not playable, and two public posters 400.** The same
`412`/`400` that is correct for private media is an availability defect when the
Reel is public. This is outside the privacy mission and is reported, not
pursued: **2 public Reels appear to have unreachable media.** Someone should own
that; it is not a search finding.

**4. Eight archived videos have playback URLs on neither Mux nor the CDN** and
failed to resolve at all (`URLError`, host `other`). Legacy or malformed rows.
Not an exposure; recorded for completeness.

The 12 archived videos returning `404` are assets Mux does not serve. **I am not
claiming PulseSoc deleted them** — no code can (§3.2). Most likely they never
finished processing or Mux removed them independently. Cause **UNKNOWN**.

### 4.1 Representative probes

```
reel 14 (reel_only)   HLS     200 application/vnd.apple.mpegurl   x-robots-tag: noindex, nofollow
reel 14 (reel_only)   poster  200 image/jpeg  ffd8ffdb            x-robots-tag: noindex, nofollow
reel 72 (reel_only)   poster  200 image/jpeg  ffd8fffe            x-robots-tag: (none)   cache-control: public, max-age=31536000, immutable
video 37 (archived)   HLS     200 application/vnd.apple.mpegurl   x-robots-tag: noindex, nofollow
video 37 (archived)   poster  200 image/jpeg  ffd8ffdb            x-robots-tag: noindex, nofollow
video 27 (archived)   HLS     412 application/json                <- signed policy, no token
video 27 (archived)   poster  400 application/json                <- signed policy, no token
control: never-existed playback id             404 application/json
```

One narrowing worth recording: `media_service.py:413-420` requests
`mp4_support: "standard"`, but the progressive URL
`stream.mux.com/{playback_id}/medium.mp4` returned **404** on every archived
video probed, while the HLS manifest for the same playback id returned 200. So
the exposure measured here is via HLS and the poster, not via a downloadable
mp4. I did not establish *why* the mp4 is absent — whether the rendition was
never produced, or the path convention differs — and it does not change the
finding either way. Recorded so nobody reads "mp4_support: standard" in the
source and assumes a second retrievable surface exists.

The control matters. A Mux playback URL does **not** return 200 for everything —
a playback id that never existed is a clean `404`. So the `200`s above are a
real statement about those specific assets, not an artifact of probing a
permissive CDN.

### 4.2 Two measurement traps, recorded so nobody repeats them

**`pulse_reels.mux_playback_id` is populated for only 18 of 141 rows** — the
live-sourced ones. For every other Reel the playback id exists only embedded in
`video_url`. My first access-matrix query filtered on `mux_playback_id <> ''`
and returned **zero** non-public Reels with a playback id, which reads exactly
like "no privacy exposure." The correct predicate is `video_url LIKE
'%stream.mux.com%'`, which matches 126 active + 10 deleted. **A column-based
Mux-exposure audit under-reports this population by roughly 7×.**

**Reel visibility is not on `pulse_reels`.** It lives on `pulse_posts.visibility`
and requires the join. A query against `pulse_reels` alone cannot see that 16
Reels are non-public.

---

## 5. (E) Poster lifecycle

### 5.1 Where posters live

From `0cc757279`, re-confirmed: `pulse_reels.poster_url` = 64 Mux / 72
`cdn.coinpilotx.app` / 5 NULL. `pulse_videos.thumbnail_url` = 137 Mux / 14 CDN /
142 NULL.

Broken down by the states that matter:

| State | Mux poster | First-party poster | None |
|---|---|---|---|
| Reel `deleted` | 10 | 0 | 0 |
| Reel non-public (`reel_only`) | 14 | **2** | 0 |

### 5.2 First-party posters are never deleted by anything

The only R2 object deletion in the entire repository is
`services/media_storage.py:234`, inside `discard_public_file`, whose docstring
states its scope exactly:

> For the window between `save_public_file` and the caller accepting the upload.

Its only caller is `services/media_service.py:1240` — the duration-ceiling
upload refusal. So R2 deletion exists **solely** as upload-rejection cleanup.
There is no object deletion on Reel delete, video delete, account delete, or
moderation takedown.

### 5.3 The first-party poster leak, which is already real

Reels 71 and 72 are `status='active'` with `pulse_posts.visibility='reel_only'`
— non-public. Their posters are on PulseSoc's own CDN, and right now both return:

```
200 image/jpeg   ffd8fffe   (real JPEG bytes)
cache-control: public, max-age=31536000, immutable
x-robots-tag: (absent)
```

So a non-public Reel's poster frame is, today, on a first-party origin,
publicly retrievable, **indexable** (no `noindex`), and declared cacheable as
`immutable` for a year. Two rows is small. The architecture is not.

**This inverts the recommendation in `0cc757279`.** I identified
`cdn.coinpilotx.app` as the only viable host for search-visible posters
*because* it sets no `X-Robots-Tag`. That is the same property that makes it the
only host here that will let unauthorized media be indexed. Mux's blanket
`noindex` has been functioning as an accidental privacy mitigation — not a
designed one, and not one anybody chose, but the reason no unauthorized Mux
poster is currently indexable.

**Any poster re-hosting project that does not carry an authorization check and a
deletion-propagation path will convert an accidental protection into an active
leak, and will do so on PulseSoc's own domain.** That is the single most
important sentence in this document for Agent 0.

### 5.4 Poster prerequisites: status

The continuation brief listed eleven things to prove before re-hosting posters.
Honest status:

| Prerequisite | Status |
|---|---|
| Rights to persist a generated poster | **UNKNOWN** — not evaluated; contractual, not technical |
| Stable asset identity | Partly — `mux_asset_id` on 236/293 videos, 18/141 reels |
| Cache behaviour | Measured: `immutable`, 1 year (§6) — too long for revocable content |
| Deletion propagation | **Does not exist** (§5.2) |
| Privacy propagation | **Does not exist** — §5.3 is the proof |
| Deduplication | Not evaluated |
| Image format strategy | Not evaluated |
| Dimensions | `pulse_videos` width/height present 293/293; poster dimensions not stored |
| First-party CDN crawler accessibility | **Verified** — 200 under `Googlebot-Image/1.0`, no `noindex` |
| No bot challenge | **Verified** — no `cf-mitigated`, under both UAs |
| No signing / expiry for public posters | **Verified** — plain concatenation, `media_storage.py:67-72` |

Four of eleven verified, two non-existent, five unevaluated. That is not a green
light.

---

## 6. (F) Cache and retention implications

Measured, not assumed:

| Asset | `cache-control` | Revocation lag |
|---|---|---|
| Mux HLS manifest | `no-cache, no-store, must-revalidate` | None — revocation is immediate at the manifest |
| Mux poster / `animated.gif` | `max-age=604800` | Up to 7 days in intermediary caches |
| First-party poster (R2/CDN) | `public, max-age=31536000, immutable` | Up to **1 year**; `immutable` tells caches not to revalidate at all |

Two consequences.

**Revoking Mux playback is effective quickly.** Because the manifest is
`no-store`, a policy change or asset deletion takes effect on the next request
rather than after a cache window. This is the cheap half of remediation.

**First-party posters are the expensive half.** `immutable` is a promise that
the bytes at that URL will never change — which is a correct and useful promise
for a supplier product photo and an actively harmful one for media whose
authorization can be withdrawn. A content-addressed path cannot be
"un-published" by changing the object; it has to be deleted at the origin and
purged, and neither capability is wired to any lifecycle event (§5.2).

No cache-purge call exists anywhere in any deletion path.

---

## 7. Authoritative duration: it exists, and it is already on the wire

Mux sends asset duration in the `video.asset.ready` webhook, and PulseSoc
already parses it:

```python
# bot.py:51403
mux_duration_seconds = max(0.0, float(data.get("duration") or 0))
```

It is spent on exactly two things:

1. `bot.py:51548-51549` — `media_service.enforce_measured_video_duration(...)`,
   the upload ceiling check. The comment there says it plainly: *"Mux has
   already told us how long the video really is."*
2. `bot.py:51555-51559` — `pulse_live_sessions.mux_recording_duration_seconds`.

The webhook writes `chat_media_uploads`, `pulse_media_assets`,
`comm_v2_attachments`, `pulse_live_sessions` and `pulse_live_streams`. It does
**not** write `pulse_videos` or `pulse_reels`. Hence `duration_seconds` = 0 on
293/293 videos while 236/293 of those rows hold a `mux_asset_id`.

Production availability of a provider-authoritative duration:

| Source | Coverage |
|---|---|
| `pulse_live_sessions.mux_recording_duration_seconds` | 31 of 284 (143 have an asset id) |
| `chat_media_uploads.duration_seconds` (video) | 11 of 201 (186 have a Mux asset) |
| `pulse_reels.duration_seconds` | **88 of 141 already populated** |
| `pulse_videos.duration_seconds` | **0 of 293** |

**Conclusion: duration does not need to be fabricated, estimated, or derived.**
It is authoritative at the provider, retrievable via the `GET /assets/{id}` call
`media_service.py:322` already implements, and for 236 of 293 videos the asset
id needed to ask is already stored. The gap is a missing persistence step, not
missing information. Nobody should compute duration from file size, caption
length, frame count assumptions, or UI timing — and nobody has to.

---

## 8. (G) Account deletion

`bot.py:7595-7705` `permanently_delete_account`, reached from `bot.py:9530`
`api_account_delete` (requires `confirm_delete is True` plus the password). It:

- scrubs `users` PII — username to `deleted-user-{id}-{hex}`, display name,
  email, password hash, avatar/banner/cover URLs; sets `account_status='deleted'`
  and `deleted_at`;
- hard-`DELETE`s seven token/push tables (`push_subscriptions`,
  `pulse_notification_devices`, `user_device_tokens`,
  `notification_device_tokens`, `password_reset_tokens`,
  `email_verification_tokens`, `telegram_link_codes`);
- soft-deletes content in exactly five tables:

```python
for table, owner_column in (
    ("pulse_posts", "user_id"),
    ("pulse_status", "user_id"),
    ("pulse_comments", "user_id"),
    ("pulse_group_posts", "user_id"),
    ("pulse_group_post_comments", "user_id"),
):
```

`pulse_reels`, `pulse_videos`, Mux assets and R2 objects appear in **none** of
the three lists. Account deletion is therefore the weakest of the three
lifecycle paths: Reel delete at least marks the Reel row, and moderation at
least marks `moderation_status`; account deletion does neither.

**Classification: latent, not active.** Measured in production:

- Reels whose post is deleted but whose Reel row is still `active`: **0**
- Reels whose owner account is deleted but whose Reel row is still `active`: **0**
- `pulse_videos` whose owner account is deleted: **0**

So the divergence I predicted by reading the code has zero instances today. It
is a real architectural gap with no current victims. It must be reported that
way — not as an active leak (it isn't) and not as a non-issue (the next account
deletion by a Reel owner creates one).

This distinction is the whole reason to measure instead of reasoning. The code
says "leak"; production says "not yet."

---

## 9. (H) Moderation and legal hold

### 9.1 A takedown path exists, and it has the same failure

`services/media_service.py:1590-1634` `_block_reel_for_post` sets
`moderation_status='blocked'` on every Reel republishing a blocked post. Its
docstring already describes the class of bug this whole document is about:

> `pulse_reels.video_url` is written at creation and read back in preference to
> the post's media, so a Reel stays playable after its upload is blocked.

It fixed the surface it was written for — the Reel row now carries the block.
But it is still a DB-only write. The Mux asset behind a `blocked` Reel is as
retrievable as the asset behind a deleted one. **A moderation takedown does not
revoke media access.** That is a strictly more serious instance of the same
defect than user deletion, because a takedown exists precisely for content that
must stop being viewable.

Production: `moderation_status` is `approved` on **141 of 141** Reels; `blocked`
is 0. The path has never been exercised, so there is no evidence in the data —
only in the code. Reported as a code-level finding with no production instances.

### 9.2 There is no legal-hold mechanism

Searched for `legal_hold`, `retention_until`, `takedown`, `DMCA` across
`services/`, `bot.py` and `scripts/`. Two hits, both prose in unrelated
documents. **No legal-hold column, no hold state, no retention clock, no
preservation flag exists anywhere in this system.**

`docs/sentinel/external_retention_and_deletion.md` is about third-party
threat-intelligence records, not media. It is worth citing anyway for its
principle — "Sentinel does not pretend it can delete data from a vendor's
systems… tests assert its absence" — which is exactly the honesty standard this
contract should be held to.

`templates/privacy.html:295` tells users some operational records may be
retained "for security, legal, accounting, payment, or abuse-prevention
reasons." That is a retention *reservation*. **It is not a licence for
unauthenticated public retrievability**, and it is not media-specific. Retaining
bytes and serving them to anyone with a URL are different acts; the published
policy permits the first and says nothing that permits the second.

### 9.3 So: is provider deletion intentionally delayed?

**No.** Nothing delays it, because nothing requests it. There is no retention
requirement, no undo window, no moderation-evidence hold, no fraud-evidence
hold, no appeal flow, no backup/restore dependency on the asset, and no account
recovery path that reads it. The asset survives because no code has ever asked
Mux to change it — not as a decision, as an absence.

This is the answer to the brief's correct caution about irreversibility. There
is no retention policy to violate. But that is an argument for *revoking public
access*, which is reversible, and **not** an argument for deleting assets, which
is not. See §10.

---

## 10. (I) (L) Recommended minimal privacy-safe remediation

### 10.1 Is P0 privacy remediation required?

**Yes.** Media that the application has marked deleted, archived, or non-public
is retrievable, unauthenticated, by URL, on the open internet. That is a privacy
defect independent of search entirely — it would be a defect if PulseSoc never
pursued search at all.

Measured scope today: **39 entities** — 10 deleted Reels (100%), 16 non-public
Reels (100%), 13 of 56 archived videos — exposing 39 playback URLs and 30
posters, 2 of which are also indexable. Plus **every future deletion**, since
the defect is structural: the delete path has no provider call at all (§2).

Severity is bounded by two facts that should be stated alongside it, not
suppressed: the URLs are long unguessable playback ids, so this is exposure to
someone who **has** a URL, not to enumeration; and Mux's blanket `noindex` means
none of the Mux-hosted media is currently search-indexable. Neither fact makes
it acceptable. The first is the exact threat model
`services/media_service.py:331-345` already rejects in writing; the second is an
accident that §5.3 shows first-party hosting removes.

### 10.2 What I recommend, and what I explicitly do not

**Recommended: revoke access, do not delete assets.**

The desired behaviour the brief anticipated is the right one. Separate the two
operations:

1. **Immediate public-access revocation.** Move the asset's playback to a
   `signed` policy, or create a signed playback id and retire the public one, at
   the moment application state leaves PUBLIC. Reversible, non-destructive,
   effective immediately (the manifest is `no-store`, §6), and already
   demonstrated working in this deployment (video 27 returns 412/400, §4).
2. **Delayed physical deletion, decided separately.** Deleting the asset is
   irreversible and should be a policy decision with an owner, a window, and a
   reason — not a side effect of a privacy fix.

**Not recommended now: deleting Mux assets.** I did not delete any, and I
recommend nobody does as part of this remediation. Access revocation closes the
exposure; deletion closes nothing additional against a URL holder and cannot be
undone.

**Required alongside, or the fix is incomplete:**

3. First-party posters for non-public or deleted media must stop being
   retrievable. This needs an actual delete-or-gate capability at the origin,
   because `immutable`/1-year caching means nothing else works (§6). This is the
   one piece that does not exist in any form today (§5.2).
4. Account deletion must enter the same path. Today it is the weakest of the
   three (§8).
5. Moderation takedown must enter the same path. Today a `blocked` Reel's media
   is as reachable as a public one's (§9.1).

### 10.3 Why this stops at the contract

Per the brief's own stop conditions, this remediation requires: new signed
playback architecture, a new CDN/object lifecycle, account-deletion changes, and
a retention policy that does not currently exist. Every one of those is a stop
condition. **I have written no patch and prepared no code change.** The honest
scope of a narrow, reversible, Agent-9-owned fix here is zero — there is no
one-file change that closes any of this, and a partial one would be worse than
none because it would make the gap look addressed.

Handed to Agent 0.

---

## 11. (M) Does public video search remain blocked after remediation?

**Yes.** Privacy remediation unblocks nothing on its own. The five blockers from
`0cc757279` resolve independently:

| Blocker | Status after privacy remediation |
|---|---|
| (A) No public canonical video/Reel landing page | **Still blocked.** Agent 2's territory; social surfaces render behind `pulse_social_shell`, which emits a `noindex` robots meta, and a carve-out needs Agent 0 (§14, Agent 2) |
| (B) Mux `X-Robots-Tag: noindex, nofollow` | **Still blocked** for Mux-hosted posters. Unchanged — and not to be worked around |
| (C) `duration_seconds` NULL on 293/293 videos | **Unblocked in principle** (§7): authoritative, retrievable, not fabricated. Still needs a persistence step |
| (D) Mux-hosted posters unusable as indexable posters | **Still blocked**, and §5.3 shows the obvious fix carries a privacy precondition |
| (E) Deleted media retrievable | **This is what remediation closes** |

So remediation closes exactly one of five, and it is the one that was never
about search. That ordering is the point: it is a prerequisite, not progress.

A sixth item belongs on that list now: **16 `reel_only` Reels are non-public and
must be excluded from any future video-search projection.** A projection written
against `pulse_reels.status='active'` alone would include all 16, because
visibility lives on `pulse_posts` (§4.2).

---

## 12. The media state machine, frozen

States, and whether PulseSoc can actually represent them:

| State | Represented? | Where |
|---|---|---|
| UPLOADING | Yes | `chat_media_uploads.processing_status` |
| PROCESSING | Yes | `mux_status` ∉ {ready, asset_ready, available}; `processing_status='mux_processing'` |
| READY | Yes | `mux_status` ∈ {ready, asset_ready, available} |
| PUBLIC | Yes | `pulse_posts.visibility='public'` (reels) / `pulse_videos.visibility='public'` |
| PRIVATE | Yes | `visibility` ∈ {`followers`, `private`, `reel_only`} |
| UNLISTED | **Partially** — `reel_only` is the closest thing and is not documented as unlisted | `pulse_posts.visibility='reel_only'`, 16 rows |
| DELETED / SOFT-DELETED | Yes, **two vocabularies** | reels `status='deleted'`; videos `status='archived'` |
| PURGED / PROVIDER-REVOKED | **NOT REPRESENTABLE** | no column, no code path, no provider call |
| FAILED | Yes | `mux_status='errored'`, `processing_status='failed'` |
| UNAVAILABLE | Yes | `chat_media_uploads.is_available` |

The gap is one row: **there is no way to express, or to reach, a state in which
the provider no longer serves the media.** Every privacy finding in this
document is that missing row.

### 12.1 The truth chain

```
APPLICATION STATE      pulse_reels.status / pulse_videos.status / pulse_posts.visibility
       ↓                        (DB only — the sole layer this system can change)
MEDIA AUTHORIZATION    does the viewer have the right to see this?
       ↓                        (NOT EVALUATED for Mux/CDN media — the URL is the authorization)
PROVIDER ASSET STATE   Mux asset + playback policy
       ↓                        (write-once: public at birth, never changed — §3.2)
PUBLIC DELIVERY STATE  what stream.mux.com / image.mux.com / cdn.coinpilotx.app actually serve
       ↓                        (200 for deleted, archived and non-public media — §4)
SEARCH ELIGIBILITY     sitemap / VideoObject / IndexNow
                               (comes LAST, and is currently correctly empty)
```

The chain breaks between layers 1 and 3. Layers 4 and 5 are downstream of a
break they cannot detect. **This is why a row's status must never be used as
evidence of provider state** — the brief's instruction, now with a measurement
behind it.

---

## 13. (J) Owner boundaries

| Concern | Owner | Agent 9's role |
|---|---|---|
| Mux signed-playback migration | Agent 0 + media owner | Specified the requirement; wrote no code |
| Destructive provider deletion policy | Agent 0 (+ legal) | **Recommends against** as part of this fix (§10.2) |
| First-party poster lifecycle & purge | Agent 0 + media owner | Measured the leak (§5.3); defined the invariant |
| Account-deletion media scope | Agent 0 + privacy owner | Traced it; classified latent (§8) |
| Moderation takedown media scope | Agent 0 + trust & safety | Found the same defect (§9.1) |
| Canonical video/Reel URLs | **Agent 2** | Media requirements only; invented no URLs |
| Retention policy (does not exist) | Agent 0 + legal | Reported the absence (§9.2) |
| Storefront `opacity:0` resilience | Agent 0 / Agent 4 / marketplace frontend | Reported in `0cc757279`; not rewritten |
| Supplier image ingestion strategy | Agent 0 + Agent 3 | Reported hosting reality; recommended no migration |

Nothing in this document touched another agent's surface. No route was added, no
template changed, no schema altered, no asset mutated.

---

## 14. (K) Contracts handed to other agents

### Agent 2 — URL & indexability
Agent 9 does not name video URLs. `AGENT_02_URL_INDEXABILITY.md` already states
the mechanism: social surfaces are robots-suppressed behind
`pulse_social_shell`, and making any of them public needs a carve-out above the
broad `/pulse` rule, through Agent 0. Agent 9 adds one requirement to that
carve-out: **it must exclude non-public and deleted media, which means joining
`pulse_posts.visibility` and honouring both `status='deleted'` (reels) and
`status='archived'` (videos).**

**One discrepancy to resolve, which is Agent 2's to settle, not mine.** That
document cites `bot.py:50820` and the directive `noindex,follow`. On `main`
(`b222b8130`) `pulse_social_shell` is defined at `bot.py:48352` and the robots
meta it emits is `noindex,nofollow` (`bot.py:48459`). I did not reconcile this
and am not asserting either value as the live one — the line drift is most
likely a different base SHA, but `follow` vs `nofollow` is a behavioural
difference, not a cosmetic one, and it changes whether links out of a Reel page
are crawled. Flagged rather than silently adopted.

### Agent 5 — structured data
**Do not emit `VideoObject` because a row has a video URL.** Required gates,
all of them: application state PUBLIC (via the `pulse_posts` join, §4.2); a
public landing page that exists; a `thumbnailUrl` on a host without
`X-Robots-Tag` (so not `image.mux.com`, §Mux); and a real `duration` (NULL on
293/293 videos today — §7 — never invented). A `VideoObject` pointing at a
`noindex` thumbnail earns nothing and asserts something false.

### Agent 6 — crawl & distribution
**No video sitemap entries until A–D are closed.** `sitemap-videos.xml` is
currently 404 and that is the correct state. `sitemap-live.xml` and
`sitemap-replays.xml` return 200 with zero `<loc>`s — also correct. Note the
status-vocabulary trap: a filter on `status='deleted'` lets all 56 archived
videos through.

### Agent 8 — IndexNow / Bing
**Do not submit because a media row exists.** Submission requires a crawlable
public landing page, which does not exist for any video surface. Agent 2's rule
applies unchanged: submit only `sitemap_eligible()` URLs.

### Agent 10 — social commerce graph
Two rules, verbatim:
- **PUBLIC PRODUCT + PRIVATE REEL = PRIVATE REEL.** The product's visibility
  never promotes the Reel's.
- **DELETED REEL + LIVE PRODUCT ≠ PUBLIC VIDEO SEARCH ENTITY.** A live product
  attachment does not resurrect a deleted Reel.

And the measurement trap: visibility is on `pulse_posts`, not `pulse_reels`.

### Agent 11 — search intelligence
Eleven distinct states, never collapsed: video exists / ready / public / landing
page exists / thumbnail eligible / `VideoObject` valid / sitemap eligible /
submitted / crawled / indexed / impression / click. Today every PulseSoc video
stops at **ready** — none reaches "public landing page exists."

**Never report "VIDEO INDEXED because the provider returned 200."** §4 is the
proof of why: deleted, archived and non-public media all return 200. A provider
200 is not evidence of public status, let alone of indexing. It is evidence of
retrievability, which in this system is the bug.

### Agent 12 — adversarial tests
The 15 required mutations, all of which **MUST FAIL**:

| # | Mutation | Must fail because |
|---|---|---|
| 1 | Deleted Reel's old HLS URL still retrievable | §4 — currently **PASSES on 10 of 10**, i.e. the defect reproduces |
| 2 | Deleted Reel's poster public indefinitely | §6 — 7 days (Mux) / 1 year immutable (first-party) |
| 3 | Private Reel → `VideoObject` | §4.2 — visibility is on `pulse_posts` |
| 4 | Private Reel → video sitemap | same |
| 5 | Private Reel → IndexNow candidate | same |
| 6 | Mux `noindex` poster declared crawler-safe | `x-robots-tag` on every Mux asset |
| 7 | Duration NULL → invented duration | §7 — authoritative source exists; fabrication is never needed |
| 8 | `option1` resembling a colour → variant-image relationship invented | no variant→image column exists at all |
| 9 | Successful image request + JS failure → permanently invisible | `pulse_marketplace.css:993` opacity SPOF |
| 10 | HTML 200 classified as an image | magic-byte check, not content-type |
| 11 | 404 body classified as valid media | ditto |
| 12 | `content-type: image/*` with zero/invalid bytes classified healthy | ditto |
| 13 | Product deleted/held → stale media as active commerce entity | status-vocabulary drift |
| 14 | Deleted Reel + live product attachment → Reel public again | Agent 10 rule 2 |
| 15 | First-party poster copied from private media → public permanent CDN URL | §5.3 — currently **PASSES** on reels 71 and 72 |

**One mutation Agent 9 is adding to the required 15**, because the measurement
found a state the original list did not anticipate:

| 16 | **Non-public (`reel_only`) Reel's HLS URL retrievable by an unauthenticated client** | §4 — currently **PASSES on 16 of 16**. This Reel was never public; there is no "the URL was legitimately seen once" defence |

Mutations 1, 15 and 16 currently reproduce against production. They are not
hypothetical tests; they are the live defects, and they are the pass/fail signal
for whether remediation worked. Mutation 16 is the one to watch, because a
remediation scoped only to *deletion* will leave it passing.

**Three traps for the tests themselves:**
- A test filtering on `pulse_reels.mux_playback_id` sees 18 of 141 rows and will
  report no exposure (§4.2). Use `video_url LIKE '%stream.mux.com%'`.
- A test filtering on `status='deleted'` misses all 56 `archived` videos.
- `config/ci_test_manifest.json` is default-deny; a new test file must be
  registered or it silently never runs.

**Preserve the negative controls.** `scripts/search_os/verify_media_search_readiness.py`
was mutation-proven: it can fail, not merely pass against today's production.
The probe in §4 carries one too — a never-existed playback id returns 404, which
is what makes the 200s meaningful. **A media verifier that only succeeds against
today's production is not a test.**

---

## 15. The invariants, frozen

**PRIVACY — the required invariant, currently violated:**

> CONTENT THAT IS NO LONGER AUTHORIZED FOR PUBLIC VIEWING MUST NOT REMAIN
> PUBLICLY RETRIEVABLE MERELY BECAUSE SOMEONE SAVED ITS OLD MEDIA URL.

Status: **VIOLATED** for 10 of 10 deleted Reels, 16 of 16 non-public Reels, 13
of 56 archived videos, and structurally for every future deletion (§4).

**POSTER PRIVACY — currently violated on first-party infrastructure:**

> PRIVATE / DELETED VIDEO → PUBLIC POSTER MUST NOT REMAIN ACCESSIBLE
> INDEFINITELY.

Status: **VIOLATED** on reels 71 and 72 — non-public, first-party,
`max-age=31536000, immutable`, no `X-Robots-Tag` (§5.3).

**RENDERING — unchanged from `0cc757279`, not Agent 9's to fix:**

> SUCCESSFUL IMAGE FETCH MUST NOT REQUIRE OPTIONAL JAVASCRIPT TO BECOME
> PERMANENTLY VISIBLE.

Status: violated by `pulse_marketplace.css:993`. Owner: Agent 0 / Agent 4 /
marketplace frontend. Not touched here.

**PROVIDER STATE:**

> A DATABASE ROW'S STATUS IS NEVER EVIDENCE OF PROVIDER STATE.

Status: proven by measurement (§4), not assumed.

### 15.1 Three problems that must not be conflated

| Problem | Owner | Current state |
|---|---|---|
| **1. Search crawlability** | Agents 2/5/6/8 | Images healthy; video blocked by A–D |
| **2. Buyer rendering** | Agent 0 / 4 / frontend | `opacity:0` SPOF, unresolved |
| **3. Privacy / authorization** | Agent 0 / privacy | **Violated — this document** |

Different owners, different fixes, different urgency. Problem 3 is the only P0,
and it is the only one that would still matter if PulseSoc abandoned search
entirely.

---

## 16. Open unknowns

Listed because UNKNOWN IS BETTER THAN FALSE:

1. **Rights to persist a Mux-generated poster frame** on first-party storage.
   Not evaluated — contractual, not technical.
2. **Why 25 assets outside Messenger are on signed playback.** Measured (§4):
   23 archived videos and 2 public reels return 412/400. `media_service.py:413-420`
   only produces `signed` for a caller that passes it explicitly, and I did not
   find the call path that did. The behaviour is certain; the cause is not.
3. **Whether Mux retains anything after an asset delete.** Not tested, because
   testing it requires deleting a production asset.
4. **Whether any third party has already cached or indexed an unauthorized
   poster.** Mux's `noindex` makes search indexing unlikely for Mux-hosted
   posters; for the two first-party ones (§5.3) there is no such protection and
   I have no crawl evidence either way.
5. **Whether `reel_only` is intended as "unlisted" or as "private."** The
   vocabulary exists in `bot.py:97934` but no document defines its intent, and
   the distinction changes whether 16 Reels are a privacy finding or a product
   decision.
6. **Segment-level (`.ts`/`.m4s`) retrievability.** I probed manifests and
   posters. Segment URLs come from the manifest and were not probed
   independently.

---

## 17. How to re-verify

Read-only, no credentials, no mutations:

```
# image crawlability + ratio-lock parity (from 0cc757279)
python3 scripts/search_os/verify_media_search_readiness.py

# the access matrix in §4 — requires a read-only prod DB session for the URL list
railway run --service Postgres python /tmp/dbq_allurls.py   # exports URL+state
python /tmp/matrix_full.py                                  # probes, buckets by state
```

The matrix script is deliberately not committed as a repo script: it needs a
production URL inventory to be meaningful, and a committed version that silently
probes nothing would be exactly the kind of vacuous pass §14 warns about. The
logic is in this document; §4.1 and §4.2 are enough to rebuild it correctly,
including both traps.

---

## 18. Definition of Done

| # | Item | Status |
|---|---|---|
| 1 | Reel deletion flow traced in code | §2 |
| 2 | Provider asset lifecycle established | §3 |
| 3 | Access matrix measured, not inferred | §4 |
| 4 | Mux playback-policy configuration documented | §3.1, §3.3 |
| 5 | Poster lifecycle documented | §5 |
| 6 | Cache/retention implications measured | §6 |
| 7 | Account-deletion interaction traced | §8 |
| 8 | Moderation/legal-hold representation established | §9 |
| 9 | Minimal privacy-safe remediation recommended | §10 |
| 10 | Owner boundaries stated | §13 |
| 11 | Agent 2/5/6/8/10/11/12 contracts written | §14 |
| 12 | P0 privacy remediation decision stated | §10.1 — **yes, required** |
| 13 | Post-remediation video-search status stated | §11 — **still blocked** |
| 14 | Media state machine frozen | §12 |
| 15 | Truth chain frozen | §12.1 |
| 16 | Privacy invariants frozen | §15 |
| 17 | Public retrieval after deletion classified correctly | §4, §8 — active for reels/videos, latent for account deletion |
| 18 | Both prior retractions remain retracted | §0 |
| 19 | Product-image finding preserved, not reopened | §0 |
| 20 | No variant→image heuristic invented | no such mapping exists; §14 mutation 8 |
| 21 | No duration fabricated | §7 — authoritative source identified instead |
| 22 | No video URLs invented | §13, §14 — deferred to Agent 2 |
| 23 | No Mux asset deleted or mutated | §10.2 — recommended against |
| 24 | No `X-Robots-Tag` workaround attempted | §11 blocker B |
| 25 | Negative controls preserved | §4.1, §14 |
| 26 | No production deployment | nothing deployed |
| 27 | Worktree clean, branch pushed | see commit |

---

*Agent 9, `search-os/agent-09-media-search`. Continuation of `0cc757279`.
Production `5bdf4e431`. Nothing deployed. Video search not activated.*
