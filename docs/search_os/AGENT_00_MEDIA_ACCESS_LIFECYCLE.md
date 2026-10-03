# Agent 0 — Canonical Media Access Lifecycle

**Owner:** Agent 0 (integration authority)
**Branch:** `search-os/agent-00-media-access-lifecycle`
**Status:** DESIGN FROZEN / NOT DEPLOYED / NO PROVIDER MUTATION PERFORMED
**Classification:** P0 privacy control

This document freezes one contract for the question *"can someone who saved a media
URL still retrieve the bytes after PulseSoc stopped showing the content?"* Today the
answer is yes, in every case, for every asset in the account. Nothing in this
document has been applied to production.

Every number below came from a read-only measurement against production Postgres
(`set_session(readonly=True)`) and the live Mux account (`GET` only, redirects not
followed). Where a number is a snapshot rather than a constant, it says so.

---

## §0 — Authority and scope

Agent 0 owns the integration. This document is the single place the following are
defined; no other agent may restate them:

| Concept | Defined here | Consumed by |
|---|---|---|
| Canonical content lifecycle states | §4 | 2, 6, 9, 11, 12 |
| Media access decision | §5, §9 | 2, 4, 9 |
| Revocation mechanics | §6 | 4 |
| Retention / legal hold precedence | §12 | — |
| Search eligibility precondition | §18 | 2, 9 |
| Fault pattern names | §16 | 11 |
| Adversarial cases | §20 | 12 |

What Agent 0 does **not** own: the public/indexability boolean (Agent 2), edge cache
behaviour (Agent 4), the material-change emission contract (Agent 6), video SEO
schema (Agent 9, still BLOCKED — see §18), truth observation (Agent 11, observer
only), protection gates (Agent 12).

---

## §1 — Corrections to the record

Three claims in circulation are wrong. They are corrected here rather than edited
away, because Agent 9's findings at `e0ca5aa54` are frozen and must not be rewritten.

### §1.1 — `412` on HLS does NOT mean "signed playback, token missing"

Agent 9 measured HTTP 412 on 23 archived videos and read it as *signed playback,
already revoked*. That reading is false.

A probe that pinned each response code to a provider state independently known from
`GET /video/v1/assets/{id}`:

| Provider state (from the API) | HLS `.m3u8` | Poster `thumbnail.jpg` | Mux `errors` |
|---|---|---|---|
| `status: errored` | **412** | **400** | `{"type":"invalid_input","messages":["live stream disconnected before sufficient video data received"]}` |
| `status: errored` | **412** | **400** | `{"type":"download_failed", …}` |
| `status: ready`, `public` | 200 | 200 | — |
| orphan, `ready`, `public` | 200 | 200 | — |
| playback id that never existed | 404 | 404 | — |
| well-formed random playback id | 404 | — | — |

`412` means **the asset is broken**. It does not mean the asset is protected. The
consequence: Agent 9's archived-video exposure count treated broken assets as
privacy-safe. The true archived-video exposure is higher than reported, not lower.

This is exactly the trap the mission named — *never interpret provider errors as
privacy success without understanding their semantics*. §16 therefore requires the
reconciliation job to classify on **provider state first, wire code second**, and
to refuse to emit `PRIVATE+INACCESSIBLE = healthy` on the strength of a status code
alone.

### §1.2 — Signed playback is NOT live anywhere in production

The derived claim "signed playback is already live on 25 production assets, so
revocation is a wiring problem rather than a provider-capability problem" was built
on §1.1's misreading. The account listing disproves it:

```
=== PROVIDER TRUTH: 375 assets in the Mux account ===
  policies=['public']   assets=375
  statuses: {'ready': 371, 'errored': 4}
  mp4_support: {'standard': 240, 'none': 135}
  assets with NO playback id at all: 0
```

**375 of 375 assets are `playback_policy: ["public"]`. Zero are signed.** Signing
keys are configured, and a signer exists in code (§8), but no asset has ever been
created signed and no code path can change an asset's policy. Signed playback is
unproven in this system, not proven.

### §1.3 — The "UNKNOWN MP4 DEFECT" now has a measured cause

The mission listed `medium.mp4` returning 404 across archived video as cause-unknown
and forbade inventing an explanation. It is not invented; it is read off the
provider's own asset records and confirmed by paired probes:

```
mp4_support=standard   medium.mp4 -> 200   hls -> 200
mp4_support=none       medium.mp4 -> 404   hls -> 200
```

`mp4_support: "none"` on **135 of 375** assets. `medium.mp4` 404 is the provider
correctly refusing a derivative that was never enabled. It is a *feature-flag*
distribution fact, not a defect, and above all **not a privacy control** — HLS still
returns 200 on every one of those 135 assets. Do not count an mp4 404 as protection.

Remaining genuinely unknown: *why* mp4_support varies. Two creation paths exist and
the split is not aligned to any single one. Tracked in §23, not blocking.

---

## §2 — Measured ground truth

### §2.1 — Provider side

375 assets, all public, 371 ready. Every asset has at least one playback id.

### §2.2 — Application side

Mux pointers PulseSoc stores:

| Table | rows | `*_asset_id` set | `*_playback_id` set |
|---|---|---|---|
| `pulse_reels` | 141 | 18 | 18 |
| `pulse_videos` | 293 | 236 | 236 |
| `pulse_media_assets` | 192 | 81 | 81 |
| `pulse_live_sessions` | 284 | 143 | 143 |
| `comm_v2_attachments` | 106 | **0** | **0** |
| `chat_media_uploads` | 821 | — | 186 |
| `pulse_live_streams` | 284 | — | 222 |

### §2.3 — Reconciliation

```
provider assets ...................... 375
referenced by an asset_id column ..... 334
reachable incl. playback_id columns .. 334
ORPHANS (no DB pointer of any kind) .. 41     <-- all public, 40 ready
DB points at an asset Mux no longer has: 0
```

**41 assets are already unreachable by any future revocation job.** They are public,
they serve HLS 200 and poster 200 right now, and no row in PulseSoc points at them.
Several are identifiable only by their Mux `passthrough` string —
`pulsedrop-reel-110`, `pulsedrop-render-34`, `pulsedrop-reel-79`, `pulsedrop-reel-85`,
`pulsedrop-reel-92`. Those names tell us which PulseSoc object *used to* own them.

Orphan creation is not historical accident; two live code paths manufacture orphans
on every execution. See §14.3. **Any revocation design that keys only off application
rows inherits a permanent, growing leak.** This is why §10 exists.

### §2.4 — Application state × provider policy

Every cell is `public`:

| Table | app state | assets |
|---|---|---|
| `pulse_live_sessions` | `ended` | 117 |
| `pulse_live_sessions` | `idle` | 26 |
| `pulse_media_assets` | (no status column) | 81 |
| `pulse_reels` | `active` | 17 |
| `pulse_reels` | **`deleted`** | **1** |
| `pulse_videos` | `active` | 224 |
| `pulse_videos` | **`archived`** | **12** |

### §2.5 — The representational gap

`mux_playback_policy` exists on **exactly one table** — `comm_v2_attachments` — which
has 106 rows, all blank, and zero playback ids. `pulse_reels`, `pulse_videos`,
`pulse_media_assets`, `pulse_live_sessions`, `pulse_live_streams`,
`chat_media_uploads` and `pulsedrop_renders` have **no policy column at all**.

There is also no column anywhere named like `legal_hold`, `retention`, `revok*`,
`purge*`, `preserv*` or `litigat*` outside the audio subsystem.

The application cannot currently record an asset's *present* policy, let alone a
revoked one. Before anything can be revoked, something must be able to say it was.

---

## §3 — Three states that are not the same thing

The system today collapses all three into one boolean and then only writes the first.

### STATE A — APPLICATION VISIBILITY
*Can PulseSoc show this content to this viewer?*
Authority: existing per-surface columns (`status`, `visibility`, moderation flags).
Owner: Agent 2 for the public/indexable projection.
**Writing A alone is what produced this P0.**

### STATE B — MEDIA ACCESS
*Can a party holding the URL retrieve bytes?*
Authority: the provider's playback-ID set, plus R2 object existence, plus whatever a
CDN edge still holds. **Not** the database.
Owner: Agent 0 (§5). Enforcement surface: §6, §7.

### STATE C — MEDIA RETENTION
*Should these bytes continue to exist?*
Authority: retention schedule, legal hold, appeal and account-recovery windows.
Owner: Agent 0 (§12). **Strictly separate from B.**

The invariants between them:

```
A says private/removed      ⇒ B MUST become non-retrievable      (the P0)
C says retain               ⇒ says NOTHING about B                (retention ≠ access)
C says legal hold           ⇒ B may be fully revoked              (hold ≠ public access)
C says destroy              ⇒ requires B already revoked          (revoke before destroy)
B retrievable               ⇒ does NOT imply A public             (orphans prove it)
```

The last line is the one the 41 orphans violate the spirit of: they are retrievable
with no A at all.

---

## §4 — Canonical lifecycle states

One vocabulary, media-type agnostic, modelled on the audio authority that already
works in production (`services/music_authority.py`) rather than invented fresh.

| State | A: shown? | B: retrievable? | C: bytes exist? | Reachable from |
|---|---|---|---|---|
| `ACTIVE` | yes, per audience | yes, per audience policy | yes | — |
| `AUDIENCE_RESTRICTED` | to permitted viewers | **authorized only** | yes | `ACTIVE` |
| `WITHDRAWN` | no | **no** | yes | `ACTIVE`, `AUDIENCE_RESTRICTED` |
| `MODERATION_REMOVED` | no | **no** | yes | any non-terminal |
| `ACCOUNT_DEACTIVATED` | no | **no** | yes | any non-terminal |
| `HELD` | no | **no** | yes, destruction refused | any non-terminal |
| `PURGE_PENDING` | no | **no** | yes, scheduled | `WITHDRAWN`, `MODERATION_REMOVED`, `ACCOUNT_DEACTIVATED` |
| `PURGED` | no | no | **no** | `PURGE_PENDING` |

Rules that are part of the contract, not implementation detail:

1. **Servability is an allowlist.** `SERVABLE_STATES = {ACTIVE, AUDIENCE_RESTRICTED}`.
   A state added later is unavailable until explicitly admitted. This is the pattern
   `music_authority.SERVABLE_STATES` uses and the reason it has never leaked a state.
2. **Transitions are a lookup table.** A pair absent from the table is *refused*, not
   defaulted. Never a denylist.
3. **`HELD` is absorbing with respect to destruction only.** It blocks `PURGE`. It
   does not block revocation, and entering `HELD` *requires* B already non-retrievable.
   Retained evidence stays inaccessible to ordinary users.
4. **`PURGED` is terminal.** Nothing leaves it. Restore from `PURGED` is impossible
   and the UI must say so rather than failing at the provider.
5. **Every other state is restorable** (§13) without recreating provider assets.
6. **Reason codes are a closed enum** on every non-`ACTIVE` transition, carried
   through to audit and to the user-facing unavailability payload.

Mapping of today's drifted vocabulary onto this (legacy writers must keep working —
`music_authority.is_servable` does precisely this dual read):

| Today | Canonical |
|---|---|
| `pulse_reels.status='deleted'` | `WITHDRAWN` |
| `pulse_videos.status='archived'` | `WITHDRAWN` |
| `pulse_posts.visibility='private'` / `'followers'` / `'reel_only'` | `AUDIENCE_RESTRICTED` |
| moderation flag set | `MODERATION_REMOVED` |
| row hard-deleted | `WITHDRAWN` **via the §10 ledger only** |

---

## §5 — Media Access Authority

A new module `services/media_access_authority.py`, deliberately shaped as the
media-agnostic generalisation of `services/music_authority.py`. That module already
implements, in production, every mechanism this mission asks for:

- closed permission set, action→permission map
- `SERVABLE_STATES` as an allowlist
- `TRANSITIONS` table where absence means refusal
- `plan_transition(action, current, *, expected_state=None, legal_hold=False)` which
  raises `409 music_state_conflict` on `expected_state` mismatch — **optimistic
  concurrency, which is the answer to adversarial case 11**
- legal hold checked *before* destructive actions and *only* against destructive
  actions — exactly the precedence the mission specifies
- `available_actions` derived from `TRANSITIONS` so the UI can never offer a
  transition the authority would refuse
- step-up re-authentication (`STEP_UP_TTL_SECONDS = 300`) required for destruction
- an unavailability payload that blanks *metadata too*, because "a removed track's
  metadata is part of what a copyright or privacy takedown is removing" — the same is
  true of a Reel's caption and poster

The authority is **pure**: it decides, it does not perform I/O. That is what makes it
unit-testable against all 16 adversarial cases without touching a provider.

Its one new responsibility beyond the audio version is the **access decision** (§9),
which audio did not need because audio has no per-viewer token.

---

## §6 — Revocation mechanics: revoke, do not delete

### §6.1 — What Mux actually permits

- An existing playback ID's policy is **not mutable**. You cannot "flip an asset to
  signed."
- You **can** add a playback ID with a different policy to an existing asset:
  `POST /video/v1/assets/{asset_id}/playback-ids`
- You **can** delete a playback ID:
  `DELETE /video/v1/assets/{asset_id}/playback-ids/{playback_id}`
- Deleting a playback ID **does not delete the asset.** The media survives; only the
  public address dies.

Therefore:

> **REVOCATION = delete the public playback ID. Optionally mint a signed one.
> The asset is never touched.**

This satisfies *DO NOT DELETE FIRST, REVOKE FIRST* at the provider level, not just at
the policy level. A revoked Reel's bytes remain at Mux for moderation review, appeal,
legal hold and account recovery, while every saved HLS URL and every saved poster URL
begins returning 404 — the same 404 a never-existing playback id returns (§1.1), so
revocation is indistinguishable from nonexistence to an attacker.

### §6.2 — No new HTTP plumbing is required

Both existing Mux clients already accept any verb:

- `services/media_service.py:216` — `_mux_json_request(path, *, method="GET", payload=None, timeout=None)`
- `services/mux_live_service.py:39` — `_request(path, *, method="GET", payload=None, timeout=10)`

Revocation is two call sites, not a new integration.

### §6.3 — Current state: no such call exists

A sweep of all 35 deletion paths found that the only Mux-mutating call in the entire
repository is `PUT /live-streams/{id}/disable`, on 4 paths. That stops *future
ingest*. The already-recorded asset and its public playback id are untouched.

**There is no asset-delete, no playback-id-delete, and no policy-update call anywhere
in PulseSoc.** That is the whole P0 in one sentence: the application has never had
the ability to affect media access.

### §6.4 — Ordering

Revocation must be **post-commit and durable**, borrowing the rationale already
written into the music purge path at `bot.py:61721`:

> the database is the record of what was decided; deleting bytes for a transaction
> that then rolled back would be unrecoverable, while a commit whose delete fails is
> merely retryable

Same logic, one step less destructive: revoke after the state transition commits,
retry forever, and never report success for an unperformed revocation (§17).

Target identifiers come from the **stored** ledger row, never from the request — the
`music_storage_keys_for_track` discipline at `bot.py:61743`, "so a purge cannot be
steered into deleting another track's audio." Here: so a revocation cannot be steered
into revoking another member's Reel.

---

## §7 — The poster is part of the private media surface

Two distinct poster surfaces, with two different problems.

### §7.1 — Mux-hosted posters (`image.mux.com`)

Built by plain string concatenation in three places, none of them authorized:

- `bot.py:55138` — client JS `reelPoster()` → `https://image.mux.com/${mux_playback_id}/thumbnail.jpg?time=1&width=720&fit_mode=smartcrop`
- `bot.py:60952` — server-side `poster_url = f"https://image.mux.com/{playback_id}/thumbnail.jpg"`
- `mobile-native/src/sharing/reelShare.ts:43` — carries the assumption in a comment:
  Mux playback ids are not secret, assets are minted public, and the thumbnail URL is
  public by design

The good news: deleting the public playback ID (§6.1) kills the poster and the HLS
stream **in one action**, because both are addressed by the same playback id. One
lifecycle, one enforcement point, for stream + poster + thumbnail + preview + GIF +
storyboard. This is the strongest argument for playback-ID deletion over any
policy-flip scheme.

The bad news is §8: if instead you mint a *signed* replacement, the poster needs an
`aud: "t"` token that no code in this repository can produce.

Worth recording: `mobile-native/src/sharing/reelShare.ts:34` already reasons about
this correctly for the share surface — for a non-public Reel the poster "is the
creator's frame published to a surface the audience setting existed to keep it off.
It drops with the caption, the title and the author name — all four or none." That
instinct is right and generalises; §5's unavailability payload adopts it.

### §7.2 — R2 / `cdn.coinpilotx.app` posters

A different and harder problem:

- objects are served from a public bucket with
  `cache-control: public, max-age=31536000, immutable`
- **there is no edge-purge capability anywhere in the repo** — zero Cloudflare API
  references
- `services/media_storage.py:237 delete_object_key(key)` can delete the *origin*
  object (path-traversal guarded; a missing object is not an error, which is what
  lets a partly-finished purge be retried)

So for R2-hosted media, revoking access at the origin does **not** reach a client or
an edge that already has the object, for up to a year. The audio authority states
this plainly in its own docstring and solves it by having a state (`QUARANTINED`)
whose entire purpose is deleting the bytes, because that is the only thing that
actually reaches the edge.

**Consequence for this contract:** for R2-hosted media, `WITHDRAWN` cannot promise
non-retrievability. Only a byte-deleting state can. That is an honest limitation and
it must be declared in the state table per storage backend, not papered over.

Two follow-ups, both escalated:

- **Agent 4:** an edge-purge capability is a prerequisite for honest R2 revocation.
  Until it exists, public pages' `Cache-Control: public, max-age=300` with no ETag
  means a public→private transition serves stale for up to 5 minutes even for
  Mux-hosted media. That window is a known, bounded, declared gap — not a silent one.
- **Agent 9:** the first-party poster rehosting idea remains **BLOCKED**. Rehosting a
  Mux poster into R2 would move it from a surface revocable in one API call to a
  surface with no purge capability and a one-year immutable TTL. It would make this
  P0 strictly worse. Adversarial case 14 exists to keep that true.

Also note the inverted mitigation that is currently load-bearing: Mux stamps
`noindex` on every asset it serves, which has been functioning as an accidental
privacy control. Reels 71 and 72 (active, `reel_only`) already have *indexable*
first-party posters with `max-age=31536000, immutable`. Any rehost removes the
accident and keeps nothing in its place.

---

## §8 — Signed playback: verdict

**Verdict: signed playback is the right long-term direction for `AUDIENCE_RESTRICTED`
content and is NOT the mechanism for `WITHDRAWN` content. It also cannot be adopted
today.** Playback-ID deletion (§6) is the correct first mechanism.

### §8.1 — What exists

`services/mux_live_service.py:84-117` is the only signing code in the system:

```python
def signed_playback_url(playback_id: str) -> str:
    """Mint a short-lived viewer token; never return an unsigned fallback."""
    key_id = os.getenv("MUX_SIGNING_KEY_ID", "")
    private_key = os.getenv("MUX_SIGNING_PRIVATE_KEY", "")
    if not key_id or not private_key:
        return ""
    header = encoded(json.dumps({"alg": "RS256", "typ": "JWT", "kid": key_id}).encode())
    # Stable within an hour so status refreshes do not restart the player.
    body = encoded(json.dumps({"sub": playback_id, "aud": "v",
                               "exp": (int(time.time()) // 3600 + 6) * 3600}).encode())
    ...
```

### §8.2 — Six findings against adopting it as-is

1. **`aud: "v"` only.** Video. There is no `aud: "t"` (thumbnail), `"g"` (gif),
   `"s"` (storyboard) or `"d"` (DRM) issuer anywhere in the repo. Flipping an asset
   to signed makes its poster **permanently 400 with no code able to fix it.**
   Adversarial case 15 is not hypothetical today — it is *guaranteed* to fire.
2. **TTL is wrong by two orders of magnitude for a privacy window.** Hour-bucketed
   `+6`, i.e. 5–6 hours of validity. A token minted moments before revocation stays
   good for most of a working day. (The hour-bucketing itself is sound — it keeps a
   status refresh from restarting the player — but the horizon must shrink and Mux
   guidance only requires `exp` ≥ asset duration.)
3. **It performs no authorization.** It signs whatever playback id it is handed. It is
   a *URL signer*, not an authorization service. The mission's WHO/WHAT/WHICH/
   WHICH-STATE question has no implementation. See §9.
4. **It fails to `""`.** Silent. A caller that does not check gets an empty URL and
   renders a broken player; a caller that falls back to the unsigned URL defeats the
   entire scheme. Only one caller exists today, so this is cheap to fix now and
   expensive to fix later.
5. **`_preferred_playback_id` at `mux_live_service.py:64` prefers `public`** when an
   asset carries both policies. During any migration, the dual-policy window would
   resolve *toward the public address*. This default must inverts before migration,
   not during.
6. **The one place that asks for `signed` has never produced one.**
   `services/messenger_media_foundation.py:1586` sets
   `MUX_INGEST_PLAYBACK_POLICY = "signed"` and gates on
   `_mux_signed_playback_configured()` — correctly asked *before* creating the asset.
   Yet `comm_v2_attachments` holds 106 rows with **zero** playback ids and zero
   policies, and the account has zero signed assets. The signed path has never
   executed end to end in production.

### §8.3 — Preconditions before any asset is migrated to signed

In order, each independently certifiable:

1. A thumbnail/`aud:"t"` issuer exists, with the same authorization gate as video.
2. The issuer calls the §5 authority and refuses on non-servable states.
3. TTL is short (minutes, bucketed) and configurable, with the bucket chosen so a
   revocation's worst-case residual exposure is a declared number.
4. `_preferred_playback_id` prefers `signed`.
5. Signer failure is loud, never a fallback to the unsigned URL.
6. Mobile proves playback (including Reels autoplay and preload) with tokens.
7. Web proves playback with tokens.
8. Offline/retry proves a token refresh does not restart or black-frame the player.
9. Rollback proven: a signed asset can be returned to public without re-ingest.

Until all nine hold, **`AUDIENCE_RESTRICTED` is enforced at the application layer
only and that limitation is declared**, while `WITHDRAWN` and below are enforced by
playback-ID deletion, which needs none of the nine.

---

## §9 — The authorization question

A single server-authoritative entry point. No surface may construct a media URL by
concatenation again.

```
decide_media_access(
    viewer,                 # WHO   — authenticated identity, or anonymous
    media_ref,              # WHAT  — provider ref from the §10 ledger, not a raw id
    content_ref,            # WHICH content the media belongs to
    lifecycle_state,        # WHICH current state, read in the same transaction
) -> GRANT(policy, ttl) | REFUSE(reason_code)
```

Refusal is the default. The following are explicitly **not** sufficient grounds to
grant, each because it is a mistake the current system makes:

- a `playback_id` exists (every surface's current logic)
- a poster URL exists (§7.1, three concatenation sites)
- the viewer once saw it (no revocation on audience narrowing)
- the URL is syntactically valid (`reelShare.ts:43`'s assumption)
- the asset exists at Mux (the 41 orphans, §2.3)

Grant requires all of: lifecycle state in `SERVABLE_STATES`; the viewer satisfying
the content's audience; the media ref actually belonging to the named content (so a
ref cannot be pointed at someone else's asset); and the ledger row not in a pending
or failed revocation state (§17 — a failed revocation must not be re-granted).

The state read and the token mint must be in **one transaction with the state read
first**, and the issued TTL clamped by it. That is adversarial case 11.

---

## §10 — `media_provider_refs`: the ledger

**Why it is mandatory and not an optimisation:** 41 assets are already orphaned, two
live code paths manufacture more on every execution (§14.3), and a revocation job
that reads application rows can never reach any of them. The provider reference must
outlive the application row.

Written **at creation time**, before any row that points at it can be destroyed:

| Column | Purpose |
|---|---|
| `provider` | `mux` / `r2` |
| `provider_asset_id` | Mux asset id, or R2 object key |
| `provider_playback_id` | the public address to revoke |
| `provider_policy` | `public` / `signed` / `revoked` — the column §2.5 proves does not exist today |
| `owner_user_id` | survives content deletion; enables account-deletion enumeration (§15) |
| `content_type`, `content_id` | best-effort back-pointer, nullable by design |
| `lifecycle_state` | §4 |
| `legal_hold` | §12 |
| `revocation_state` | §17 |
| `revocation_attempts`, `revocation_error` | §17 |
| `retention_class`, `purge_scheduled_at`, `purged_at` | §12 |
| `last_transition_at`, `reason_code` | audit + §16 |
| `environment` | Agent 6 RULE D3 |

A one-time backfill can seed it from the 334 reachable assets plus the 41 orphans'
`passthrough` strings, which encode their former owners (`pulsedrop-reel-110` →
reel 110). That recovers application identity for most orphans without a single
mutation.

**Note the ordering constraint:** the ledger is useless unless it is written *before*
the destructive paths in §14.3 run. Ledger-at-creation is therefore the first thing
to ship, ahead of any revocation capability, and it is non-destructive — pure insert,
no behaviour change, independently certifiable.

---

## §11 — Durable revocation on `pulse_jobs` — no new bus

The mission forbids a new event bus without proving the existing primitive cannot
satisfy the requirement. It can. `pulse_jobs` is a real durable queue: CAS claim at
`media_worker.py:1068`, backoff `min(900, 30 * attempts)` in `_fail_or_retry_job`,
`_reschedule(cur, job, *, seconds=30)` for provider polling, and `media_worker` is in
`Procfile:6` on a 5s loop. Schema at `bot.py:128096`.

No `media_privacy_events_v2`, no `media_event_bus`, no `mux_outbox_v2`.

Three real constraints to respect, each measured rather than assumed:

1. **Lease recovery is hardcoded to one job type.** `media_worker.py:1111` recovers
   stuck `processing` rows only `WHERE job_type='finalize_live_replay'`. A new job
   type gets **no lease recovery** — a crash mid-revocation would park the row
   forever, which for a privacy control is the worst possible failure. Lease recovery
   must be generalised (or explicitly extended to the revocation type) as part of the
   change, with a test.
2. **`target_id` is `INTEGER`.** Revocation targets a ledger row id (§10), which is
   an integer. Do not attempt to carry a Mux asset id through `target_id`.
3. **`enqueue_job` has no dedupe** (`services/pulse_feed_engine.py:1292`, and it
   commits its own connection). For state transitions, use the transactional pattern
   already proven at `messenger_media_foundation.py:1850` (`enqueue_mux_ingest(cur,
   …)`) so the enqueue shares the transaction that wrote the state. Idempotency comes
   from the operation: deleting an already-deleted playback id is a no-op, the same
   property `delete_object_key` relies on.

Job types must be added to `MEDIA_JOB_TYPES` (`media_worker.py:79`) — set membership,
and an unknown type is silently completed at `:794`, so a missing registration would
*look* like success. Another fail-open to close with a test.

---

## §12 — Retention and legal hold

Precedence, stated so it cannot be re-litigated per surface:

```
LEGAL HOLD blocks DESTRUCTION.
LEGAL HOLD does NOT grant ACCESS.
Entering HELD requires access ALREADY REVOKED.
Destruction requires access ALREADY REVOKED.
Retention class sets the EARLIEST destruction time, never an access right.
```

Implementation precedent, already shipped for audio:

```python
if legal_hold and action in DESTRUCTIVE_ACTIONS:
    raise AuthorityError("music_legal_hold",
        "This track is under legal hold and cannot be purged. Release the hold first.",
        409)
```

`DESTRUCTIVE_ACTIONS` is a frozenset containing only `PURGE`. Revocation is not in
it, which is precisely why hold does not imply retrievability.

Retention classes must cover the windows the mission enumerates — undo, moderation
review, appeal, account recovery, abuse investigation — and each is a *destruction*
delay, never an *access* grant. `PURGE_PENDING` carries `purge_scheduled_at`;
`CANCEL_PURGE` exists and is reachable from it; destruction requires step-up
re-authentication (`STEP_UP_TTL_SECONDS = 300`).

Physical destruction is a separately governed retention action with its own
permission (`media.purge`), not a consequence of a user pressing delete.

---

## §13 — Restore and undo

Restore must reactivate access **through the canonical state**, and must not require
recreating provider assets merely because public access was revoked.

With playback-ID deletion as the revocation mechanism, restore is
`POST /video/v1/assets/{asset_id}/playback-ids` with the target policy. The asset
never left. The *playback id changes*, which has two consequences that must be
designed for rather than discovered:

1. Every stored URL built from the old playback id is now permanently dead. Since
   those stored URLs are exactly the saved-URL attack surface this P0 is about, that
   is a feature: **restore does not re-arm previously leaked URLs.**
2. Therefore nothing may persist a playback id outside the §10 ledger, and every
   surface must resolve the current id through §9 at render time. Any cached or
   embedded playback id becomes a stale-404 bug on restore. This is the single
   largest refactor implied by this document and it is also the thing that makes the
   invariant enforceable at all.

`PURGED` is not restorable and must say so up front. Restore from every other state
is a normal transition, audited, with a reason code.

---

## §14 — Deletion paths and their obligations

35 paths reach media. Every one of them writes STATE A only.

### §14.1 — The universal obligation

Each path must, after its state write commits: transition the §10 ledger rows for the
content, enqueue revocation (§11), and record `REVOCATION_PENDING` (§17). No path may
report success to a user on the strength of the database write alone.

### §14.2 — Per-path matrix

| Path class | A | search | B today | B required | C | restore | audit |
|---|---|---|---|---|---|---|---|
| Reel delete | `deleted` | Agent 2 | **public** | revoke | retain | yes | partial |
| Video archive | `archived` | Agent 2 | **public** | revoke | retain | yes | partial |
| Post delete | hidden | Agent 2 | **public** | revoke | retain | yes | partial |
| Account deletion request | queued, **unconsumed** | — | **public** | revoke all owned | retain | window | row only |
| Seller/account termination | varies | Agent 2 | **public** | revoke | retain | case-by-case | partial |
| Moderation takedown | flagged | Agent 2 | **public** | revoke | retain for appeal | on appeal | yes |
| Admin removal | varies | Agent 2 | **public** | revoke | retain | yes | `log_admin_audit` |
| Legal removal | — | — | **public** | revoke | `HELD` | no | required |
| Group hard-delete | **row destroyed** | — | **public, orphaned** | ledger-only | retain | **impossible** | none |
| Replay retry | **pointers blanked** | — | **public, orphaned** | ledger-only | retain | n/a | none |

### §14.3 — The two paths that are worse than soft-delete

- **Group hard-delete** (`bot.py:53471`, `bot.py:53506`) row-destroys the media-URL
  rows. The Mux asset stays public and becomes unreachable by any job.
- **Replay retry** (`bot.py:61211`, `media_worker.py:960`) blanks
  `mux_recording_asset_id`, `mux_recording_playback_id` and `replay_url`. The
  previous recording stays public and becomes an orphan.

These are the measured source of the 41. They cannot be fixed by adding a revocation
call — by the time they run, the identifier is already gone. **They can only be fixed
by the ledger being written earlier (§10).** That is the proof that §10 is a
prerequisite and not a convenience.

### §14.4 — Two dead integration points found

- `services/search_visibility.py:629` reads `takedown_at` / `is_takedown`. **No
  writer exists anywhere in the repository.** A dead gate — and a ready-made,
  already-consumed hook for `MODERATION_REMOVED`.
- `pulse_account_data_requests` receives deletion and export request INSERTs that
  **nothing consumes.** Account deletion is currently a row in a table nobody reads.

---

## §15 — Account deletion

Must enumerate media ownership and apply the canonical policy — and must not destroy
everything until retention, legal hold and restoration semantics are frozen (they are
frozen above, which is what unblocks this).

1. Enumerate via `media_provider_refs.owner_user_id` (§10) — the only enumeration
   that survives content-row destruction and covers orphans.
2. Transition every owned ref to `ACCOUNT_DEACTIVATED`, which is non-servable.
3. Enqueue revocation for each (§11).
4. Apply the account-recovery retention window as a *destruction delay* (§12). During
   it, media is retained and non-retrievable. Recovery restores through §13.
5. Refs under `legal_hold` go to `HELD`: revoked, retained, destruction refused.
6. Only after the window, and only via the separately-permissioned purge action, does
   anything reach `PURGE_PENDING`.
7. Consume `pulse_account_data_requests` (§14.4) rather than adding a ninth queue.

---

## §16 — Observability

A privacy-safe media lifecycle view. One row per provider ref:

content id · content type · application state · provider asset state · playback
policy · poster policy · retention state · legal hold · last lifecycle transition ·
revocation state · revocation failure · retry state · environment

**Never exposed:** private playback credentials, signing keys, raw access tokens,
signed URLs. The view shows *policy* and *state*, never an artifact that grants
access. A lifecycle dashboard that leaks a token is a privacy incident in itself.

### §16.1 — The reconciliation classifier

Deterministic, read-only, and it classifies on **provider state first, wire code
second** — the §1.1 lesson encoded:

| Application | Media | Class |
|---|---|---|
| public | accessible | `HEALTHY` |
| public | inaccessible | `AVAILABILITY_DEFECT` (§21.1 — separate defect class) |
| private | accessible | **`PRIVACY_DEFECT`** |
| private | inaccessible | `HEALTHY` — **only if** provider state confirms revocation |
| deleted | accessible | **`PRIVACY_DEFECT`** |
| deleted | retained private | `POTENTIALLY_HEALTHY` |
| anything | provider `errored` | `INVESTIGATE` — never `HEALTHY` |
| no application row | accessible | **`ORPHAN_DEFECT`** (41 today) |
| otherwise | — | `UNKNOWN` → investigate |

A `412` or a `400` or an mp4 `404` must never be counted as privacy success. Negative
controls (a never-existing playback id, a well-formed random id) must be probed in
every run so a change in provider semantics shows up as a control failure rather than
as a fleet-wide false "healthy."

### §16.2 — Agent 11

`services/search_truth.py` is implemented, observer-only, and already carries an
`AVAILABILITY_WIRE_VS_DB` fault pattern. `MEDIA_WIRE_VS_DB` fits that shape exactly.
Agent 11 **observes and does not write, and is not an authority** — a fault it reports
is evidence for Agent 0, never a trigger for a mutation. It refuses a new table and
cannot write; keep it that way.

---

## §17 — Failure semantics: fail safe

The mission's hard line: if the DB says private but provider revocation fails, the
system must **not** quietly report success.

```
REVOCATION_NOT_REQUIRED   state is servable
REVOCATION_PENDING        transition committed, provider call not yet confirmed
REVOCATION_CONFIRMED      provider confirms the playback id is gone
REVOCATION_FAILED         attempts exhausted — media is PRESUMED STILL RETRIEVABLE
```

Rules:

1. A state transition commits `REVOCATION_PENDING` in the **same transaction**. There
   is no window in which the state is private and nothing is owed.
2. The user-facing confirmation must not claim media is inaccessible while the state
   is `PENDING` or `FAILED`. Say the content is hidden and removal is in progress.
   Never overclaim a privacy guarantee.
3. `REVOCATION_FAILED` alerts, and the ref stays non-servable and non-grantable (§9)
   forever. Failure never re-opens access.
4. Retry is durable with backoff (§11) and exhaustion is a *reported* state, not a
   dropped job. §11's hardcoded lease recovery is the specific way this could silently
   break, which is why it is a required fix and not a nice-to-have.
5. Agent 6 **RULE D1**: a failed universe read must never emit removals. A
   reconciliation run that cannot read the provider emits **nothing**, not a fleet of
   false defects.

---

## §18 — Search eligibility

```
SEARCH INDEXABLE  ⇒  APPLICATION PUBLIC  ⇒  MEDIA PUBLICLY RETRIEVABLE
                     UNDER THE INTENDED POLICY

The reverse does not automatically follow.
```

Access control comes before indexability, never after. Until the §16.1 reconciliation
proves no known private or deleted media is publicly retrievable outside explicit
policy, **video search stays BLOCKED.** Publish no `VideoObject`, no video sitemap
entries, no searchable first-party posters, no IndexNow video events, no
Merchant/video relationships.

`services/search_visibility.py` remains the one eligibility authority. This contract
adds a precondition to it; it does not create a second authority. Agent 2's gap is
noted: there is no `410`/`HELD`/`REDIRECT` state today, only a boolean with prose
reason strings, and `HELD` in particular has no representation on that side at all.

---

## §19 — Agent contracts

**Agent 2 — public/indexability lifecycle.** PRIVATE / DELETED / HELD / 404 / 410 /
REDIRECT must propagate into the media access policy. Needs real states, not a
boolean with prose reasons. Must not become a second canonical authority.

**Agent 4 — delivery and cache.** A revoked poster must not remain accessible
indefinitely because an edge cache retains the public object. No purge capability
exists (§7.2); `max-age=300` with no ETag on public pages means a bounded 5-minute
stale window even after a correct revocation. Edge purge is a prerequisite for honest
R2 revocation. Declare the window; do not hide it.

**Agent 6 — material change.** Consume the existing contract; **do not create another
event bus** (§11). Class 11 `MEDIA_ELIGIBILITY_CHANGED` is the carrier. RULE D1 (a
failed universe read emits no removals) and RULE D3 (`environment` on every row) bind
this work. Agent 6's contract is currently design-only — 804 lines of document, zero
code — so this document depends on an unimplemented sibling and says so (§23).

**Agent 9 — evidence owner and future consumer.** Findings frozen at `e0ca5aa54`; not
rewritten. §1 corrects two of them additively, under Agent 0's ownership. Agent 9 was
not sent back to invent the shared architecture. Poster rehosting stays BLOCKED (§7.2).

**Agent 11 — observer only.** §16.2. Not an authority.

**Agent 12 — adversarial.** §20. Gates under `tests/protection/test_*.py` are
glob-discovered, so a new gate is enforced the moment it lands; other test files must
join `config/ci_test_manifest.json` (default-deny). Corpora must be discovered, never
hardcoded, and asserted non-empty — a suite that loops over zero collected items
passes vacuously.

---

## §20 — The 16 adversarial cases

Verdicts are against production **today**. `MUST FAIL` = the attack must stop working
once this contract is implemented.

| # | Attack | Today | Mechanism that stops it |
|---|---|---|---|
| 1 | Save HLS URL → delete Reel → replay | **SUCCEEDS** | §6 playback-id deletion |
| 2 | Save poster → delete Reel → fetch | **SUCCEEDS** | §6 (same id serves both) |
| 3 | Private Reel, old playback URL still usable | **SUCCEEDS** | §6 + §8 for restricted |
| 4 | Archive video, public playback persists | **SUCCEEDS** (12 rows, §2.4) | §6 |
| 5 | Delete account, media still retrievable | **SUCCEEDS** (requests unconsumed, §14.4) | §15 |
| 6 | Moderation takedown, cached poster survives | **SUCCEEDS** | §6 + Agent 4 purge |
| 7 | Legal hold, asset accidentally public | **SUCCEEDS** (no hold column, §2.5) | §12 — hold requires revoked |
| 8 | Restore, authorization never returns | n/a | §13 — restore is a normal transition |
| 9 | Token expires, stale token still works | n/a (no tokens) | §8.3 short TTL |
| 10 | User A obtains auth for User B's private media | **SUCCEEDS** (signer takes any id, §8.2.3) | §9 ownership check |
| 11 | Content goes private between auth check and token issue | **SUCCEEDS** | §9 single transaction + `expected_state` 409 |
| 12 | CDN cache ignores the authorization boundary | **SUCCEEDS** | Agent 4; declared window meanwhile |
| 13 | Crawler retrieves poster for private content | **SUCCEEDS** (only Mux `noindex` prevents it, §7.2) | §6 + §18 |
| 14 | First-party rehosting strips privacy controls | **WOULD** | §7.2 — rehost BLOCKED |
| 15 | Public Reel gets signed-only config, becomes unavailable | **GUARANTEED** (no `aud:"t"`, §8.2.1) | §8.3 preconditions 1–2 |
| 16 | Provider failure → app claims revocation succeeded | **WOULD** | §17 `PENDING`/`FAILED` |

Thirteen of sixteen succeed against production right now. None of the three that do
not are defended — they are simply unreachable because the mechanisms they attack do
not exist yet.

---

## §21 — Separate defect classes

Kept out of the privacy diagnosis deliberately.

### §21.1 — PUBLIC AVAILABILITY DEFECT

2 public Reels return 412, 2 public posters return 400. Per §1.1 these are
`status: errored` assets — ingest that never produced enough video
(`invalid_input: live stream disconnected before sufficient video data received`) and
a fetch that failed (`download_failed`). Public content that cannot be played is a
*quality* bug. It is not privacy protection and must never be counted as such.

### §21.2 — MP4 DEFECT — resolved as a distribution fact

§1.3. `mp4_support: "none"` on 135/375 → `medium.mp4` 404 while HLS returns 200. Not
a privacy control. Why the flag varies remains open (§23).

---

## §22 — Rollout

Each step non-destructive and independently certifiable. No step may begin before the
previous is proven.

1. ✅ **Inventory** — 375 assets, 9 pointer tables (§2).
2. ✅ **Classify** — all public, 41 orphans, 0 stale (§2.3).
3. ✅ **Shadow-evaluate** — response codes pinned to provider states (§1.1).
4. ✅ **Compare application to provider** — §2.4.
5. ✅ **Measure affected assets** — 41 orphans, 12 archived, 1 deleted, 143 live
   recordings, all public.
6. **Ledger at creation** (§10) — pure insert, no behaviour change. *Must precede
   everything, because §14.3 destroys identifiers.*
7. **Backfill the ledger** from 334 reachable + 41 orphan `passthrough` strings.
8. **Shadow reconciliation** on `pulse_jobs` (§16.1), read-only, with negative
   controls and its own lease recovery (§11.1).
9. **Authority module** (§5) — pure, unit-tested against all 16 cases.
10. **Revocation capability** (§6) behind a default-off flag, dry-run first.
11. **Prove rollback** — restore a revoked asset without re-ingest (§13).
12. **Prove the poster flow** — one id serving both surfaces dies together (§7.1).
13. **Prove token flow / mobile / web** — only if §8.3's nine preconditions are met.
14. **Then** write the remediation plan for the measured backlog.
15. **Then, and only then**, consider search eligibility (§18).

Nothing past step 5 has been built. Nothing has been deployed. No provider mutation of
any kind has been performed — every Mux call made during this investigation was a
`GET`, and both database connections were opened read-only.

---

## §22a — What was actually built on this branch

Two files. Both non-destructive, both certified in isolation, neither wired into
any route — so nothing in production behaves differently.

### `services/media_access_authority.py`

The pure decision layer of §5 and §9, plus the §16.1 classifier. No I/O, no
`import bot`, no `os.getenv` (so the env-contract gate is untouched). Implements:

- the §4 state machine as a `TRANSITIONS` lookup where absence means refusal
- `SERVABLE_STATES` as a strict-subset allowlist
- `REVOCATION_REQUIRED_STATES`, and `plan_revocation` returning `PENDING` so no
  private state ever commits owing nothing at the provider
- legal hold checked against `DESTRUCTIVE_ACTIONS` only — `ACTION_WITHDRAW` is
  deliberately not in that set, which is what makes "hold blocks destruction"
  and "hold does not grant access" one rule instead of two that can drift
- `ACTION_RELEASE_HOLD` landing on `WITHDRAWN`, never `ACTIVE`, and no
  `(RESTORE, HELD)` edge at all
- `plan_transition(..., expected_state=…)` → 409, the §9 race defence
- `state_from_legacy` reading the columns production actually writes, including
  **both** `status='deleted'` and `status='archived'`, non-public `visibility`,
  and the orphaned `takedown_at` / `is_takedown` columns `search_visibility.py`
  already reads and nothing writes
- `decide_media_access` with `viewer_may_view` keyword-only and **no default**
- `REVOCATION_REACHES_EDGE`, which answers `False` for R2 and for any unknown
  provider, so the §7.2 limitation is declared in code rather than discovered
- `RESTRICTED_TOKEN_TTL_SECONDS = 300` with 60s bucketing — bucketing kept from
  the existing signer, the 5–6 hour horizon discarded
- `classify_reconciliation`, which consults provider state **before** the wire
  result and returns `INVESTIGATE` for every `errored` asset

### `tests/protection/test_media_access_authority.py`

55 tests, 188 subtests, 0.14s. `tests/protection/test_*.py` is glob-discovered
by `scripts/protection/run_protection_suite.py:54`, so it is enforced the moment
it lands and needs no `config/ci_test_manifest.json` entry. Every corpus is
derived from the module (`LIFECYCLE_STATES`, `ACTION_ORDER`, `TRANSITIONS`) and
asserted non-empty first — a suite that loops over a hardcoded list cannot see a
state someone added, and one that loops over nothing passes vacuously.

### Proof the gate can fail

Fourteen mutations applied to a sandbox copy, never to the worktree:

| Mutation | Result |
|---|---|
| `WITHDRAWN` added to `SERVABLE_STATES` (the defect itself) | **5 failed** |
| legal hold also blocks revocation | **2 failed** |
| `PROVIDER_BROKEN_STATUSES` emptied (the 412 misreading) | **17 failed** |
| `viewer_may_view=True` default added | **1 failed** |
| restricted falls back to public with no thumbnail issuer | **1 failed** |
| R2 claims to reach the edge | **1 failed** |
| failed revocation re-opens access | **1 failed** |
| `(RESTORE, HELD) → ACTIVE` edge added | **1 failed** |
| `archived` dropped from the removal vocabulary | **1 failed** |
| TTL restored to the existing 6-hour horizon | **1 failed** |
| poster survives a withdrawal | **1 failed** |
| cross-content ref accepted | **1 failed** |
| `plan_revocation` forgets the pending obligation | **5 failed** |
| `expected_state` conflict no longer refuses | **1 failed** |
| source restored | **55 passed** |

Each mutation is an adversarial case from §20 or an invariant from §3 expressed
as a code change. None of them is caught by any pre-existing test in this
repository — which is the measure of what was unguarded.

---

## §23 — Open unknowns

1. Why `mp4_support` varies across 240/135. Two creation paths exist; the split does
   not align to either. Non-blocking.
2. Whether the 41 orphans' `passthrough` strings resolve to live PulseSoc objects for
   all 41 or only some. Determines how many have no recoverable owner.
3. Agent 6's material-change contract is design-only. This document depends on an
   unimplemented sibling for emission.
4. Agent 2 has no `HELD` representation. §4's `HELD` has nowhere to land on the
   indexability side yet.
5. No edge-purge capability exists. R2-hosted media cannot be honestly revoked until
   Agent 4 provides one (§7.2).
6. `comm_v2_attachments` holds 106 rows with zero playback ids while requesting
   `signed`. Whether that is "signed path never ran" or "Messenger media never
   reached Mux at all" is undetermined and affects whether Messenger is in scope.

---

## §24 — Definition of Done

| # | Requirement | State |
|---|---|---|
| 1 | Three states separated and frozen | ✅ §3 |
| 2 | Canonical lifecycle vocabulary | ✅ §4 |
| 3 | Legacy vocabulary mapped, legacy writers honoured | ✅ §4 |
| 4 | Single access-authority design | ✅ §5 / ✅ pure layer built + gated (§22a) |
| 5 | Revocation ≠ destruction, mechanism proven available | ✅ §6 |
| 6 | Poster in the same lifecycle as video | ✅ §7 |
| 7 | Signed playback investigated, verdict recorded | ✅ §8 |
| 8 | Authorization question answered server-side | ✅ §9 / ✅ decision built, ❌ not wired to any route |
| 9 | Orphan reachability solved | ✅ §10 design / ❌ unbuilt |
| 10 | No new event bus | ✅ §11 |
| 11 | Retention and legal-hold precedence frozen | ✅ §12 |
| 12 | Restore without re-ingest | ✅ §13 |
| 13 | All deletion paths mapped with obligations | ✅ §14 |
| 14 | Account deletion covered | ✅ §15 |
| 15 | Privacy-safe observability view | ✅ §16 design / ❌ unbuilt |
| 16 | Reconciliation classifier, provider-state-first | ✅ §16.1 / ✅ classifier built + gated, ❌ job unbuilt |
| 17 | Fail-safe revocation semantics | ✅ §17 |
| 18 | Search eligibility gated behind access control | ✅ §18 |
| 19 | Agent boundaries stated, no authority duplicated | ✅ §19 |
| 20 | 16 adversarial cases published with verdicts | ✅ §20 |
| 21 | Availability and mp4 defects kept separate | ✅ §21 |
| 22 | Production reconciliation proves no private/deleted media is publicly retrievable | ❌ **FALSE TODAY** — §20 shows 13/16 attacks succeed |

Item 22 is the mission's terminal condition and it is currently false. This document
is the contract that makes it achievable; it is not evidence that it has been
achieved.

---

**DO NOT DELETE FIRST. REVOKE FIRST.
RETENTION IS NOT ACCESS. LEGAL HOLD IS NOT PUBLIC ACCESS.
A DATABASE BOOLEAN IS NOT A PRIVACY CONTROL IF THE OLD MEDIA URL STILL WORKS.
A POSTER IS PART OF THE PRIVATE MEDIA SURFACE.
A CDN CACHE IS PART OF THE PRIVATE MEDIA SURFACE.
SEARCH INDEXABILITY COMES AFTER ACCESS CONTROL, NOT BEFORE IT.**
