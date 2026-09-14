# Media Delivery Foundation — Final Report

**Mission:** PULSESOC MEDIA DELIVERY FOUNDATION (63 sections)
**Product:** PulseSoc · **Company:** CoinPlotXAI Inc.
**Branch:** `main` @ `7a838621` (local only — nothing pushed)
**Date:** 2026-09-13

Not to be confused with `MEDIA_FOUNDATION_FINAL_REPORT.md` (2026-09-03), which covers the
earlier *reliability* mission — native cache, download, save-to-gallery, share. That engine is
reused here rather than re-implemented (§36); this mission is about **delivery**: whether the
bytes arrive, whether a document opens, how long a stored video may be, and how much a thread
downloads to paint itself.

---

## The four problems, and what each one actually was

| # | Reported as | Root cause | Fixed in |
| --- | --- | --- | --- |
| 1 | Messenger rejects valid videos | Type resolution keyed on a spelling table that omitted `video/quicktime`, so a `.mov` was classified unsupported before any size or duration check ran. Separately, a single-POST upload could not carry a long file at all. | `73db3d46`, `a9af014d` |
| 2 | PDFs send but will not open | The bubble's `onPress` evaluated to `undefined` for every non-video attachment: the card drew, said "Sent", and the tap landed on a `Pressable` wired to nothing. | `73db3d46` |
| 3 | Media loads too slowly | The thumbnail slot resolved to the *original*. A video bubble handed an entire movie to `<Image>`, so opening a thread downloaded the videos in it — and because `/download` is a protected path, thumbnail loads ran server session refresh and signed users out. | `73db3d46` |
| 4 | Allow stored video up to 90 minutes | There was no per-surface ceiling and no *measured* duration — only the client's claim, which a client can simply not send. | `8d633f07`, `7cacdcd9`, `c01616db` |

Problem 3 is the one worth stating carefully: it was never a caching or CDN problem. It was two
call sites asking one grant hook for the same identity, both resolving to the same URL.

---

## Measured, not asserted (§48–49)

`scripts/measure_messenger_media_delivery.py`, six real attachments through the real pipeline:

```
attachment       class      original    preview    ratio  status
clip-15s.mp4     video       4.96 MB    11.8 KB     432x  ready
clip-90s.mov     video      29.88 MB    11.8 KB    2601x  ready
photo-1.jpg      photo      623.1 KB    13.9 KB      45x  ready
... (photo-2..4 identical)

bytes to paint 6 bubbles
  before (thumbnail slot resolved to the original) : 37.27 MB
  after  (derived preview, original on tap only)   :  79.3 KB
  reduction                                        : 99.8%  (481x less)

access grants per bubble: 2 before -> 1 after
```

No "faster" claim appears anywhere in this mission's commits or tests without a number behind
it. The thread-paint test (`8ee27426`) measures the cost rather than asserting an improvement.

---

## Verification, all on pinned SHA `7a838621` in an isolated worktree

The main checkout is shared with another active session and had ten unrelated dirty files
throughout. Gate and suite results from it would be worthless, so every number below comes
from a clean worktree checked out at the exact commit.

| Check | Result |
| --- | --- |
| `npm run verify` (tsc + i18n + jest) | **exit 0** — `tsc --noEmit` clean; `OK — 11 locales, catalog version 1.0.0`; **403 suites / 6994 tests passed** |
| Mission mobile tests | **12 suites / 145 tests passed** (`src/media`, resumable upload, both ChatScreen attachment files) |
| Mission backend tests (10 files, one process each) | **248 passed, 0 failed** |
| Mutation harness | **39/39 caught, 0 survivors**, each by a *named* test (§61) |
| Real-time audio gate | **PASS on all 10 mission commits**, run per-commit against its own parent |
| Protected paths touched | **0 of 74** |
| RTC changes | **0** (see below) |
| Protection suite | 1 failure, foreign and pre-existing (see below) |
| Pushed | **Nothing.** Every mission commit verified *not* an ancestor of `origin/main` |

Backend files can't share a pytest process in this repo, so each was run alone. The mutation
harness was bracketed by `git diff | shasum` and per-file digests before and after: HEAD,
`git status`, the diff digest and all seven target files were byte-identical afterwards,
proving the harness only ever wrote its APFS clone.

### RTC changes = 0, demonstrated rather than claimed

7,390 added lines across the ten commits were grepped for every forbidden API. Two hits, both
accounted for:

- `agora` — the two Agora names were **already on that `media_worker.py` import line**; the
  diff only appended `messenger_media_foundation` to it. A whole-line rewrite shows as `+`
  under `-U0`. No Agora behaviour changed, and `services/agora_*` was never opened.
- `setAudioModeAsync` — an inert `jest.fn()` stub in the new test's `expo-av` mock, which
  exists *so that* the screen never reaches the real audio module.

Zero hits for `AVAudioSession`, `setCategory`, `LiveKit`, `RtcEngine`, `publishTrack`,
`localAudioTrack`, `microphone`, `getUserMedia`.

### The one protection-suite failure is not this mission's

`tests/protection/test_environment_contract.py` fails: `R2_MUX_INPUT_MAX_BYTES` and
`R2_MUX_INPUT_PART_BYTES` are read at runtime but absent from `.env.example`. Both are read in
`services/agora_cloud_recording_service.py`, introduced by another session's `3a5785e2` — Agora
code this mission is forbidden to touch.

Worth recording precisely, because the direction matters: at `3a5785e2` **five** variables were
undocumented. Three of them were mine (`MEDIA_UPLOAD_VIDEO_BUDGET_MBPS`,
`MEDIA_WORKER_DURATION_RECONCILE_BATCH`, `MEDIA_WORKER_DURATION_RECONCILE_MAX_AGE_DAYS`) and
`7cc447aa` documented them. The two that remain are the foreign Agora pair. This mission
*reduced* the failure from five to two and stopped where its mandate stopped.

**Owed by whoever owns `3a5785e2`:** two lines in `.env.example`. I did not write them, because
documenting Agora replay internals means asserting semantics I cannot verify and must not read.

---

## Design decisions a reviewer should not have to reverse-engineer

**Media identity is the foundation id, never the transport id.** `media_upload_id` is the
`message_attachments` row the `/api/messages/media/<id>/access` endpoint is keyed on;
`attachment_id` is a Comm-v2 transport row and asking for it is a hard 404. Comm-v2 messages
carry both, so the fixtures deliberately use *different* numbers (`3301` vs `777`) and the
assertions read against that difference.

**One grant per bubble, carrying both URLs.** The original and its preview are two objects
behind one authorization decision. This is why the mission's one surviving text-scraping
assertion exists: `messengerMediaAccess` has a module-level cache that deduplicates concurrent
requests for the same id, so a bubble with two call sites issues exactly as many network
requests as one and renders identically. The render test pins the *consequence*; that one line
pins the *cause*, and a render test provably cannot.

**`thumbnailUrl: ""` is a real state, not a missing value.** A video must draw no poster at
all. A photo may fall back to its original, because its size is bounded by the photo limit and
the viewer needs those bytes anyway. Both branches are asserted, in opposite directions.

**Deferral and failure are different answers.** In `media_worker`, a job whose inputs have not
landed goes back on the queue *without* spending its error budget. Three fast retries followed
by permanent retirement would strand an attachment at `queued` forever — the exact state the
handler exists to end.

**A per-surface override may only tighten.** `MESSENGER_VIDEO_MAX_SECONDS` and friends are
ignored if larger than the code default, so the 90-minute platform ceiling cannot be raised by
an env var without a deploy.

---

## §63 Definition of Done

- [x] **Messenger accepts valid videos** — `video/quicktime` resolves to video; 37 resumable-upload tests; `a9af014d` carries a 90-minute file in parts.
- [x] **Documents actually open (§4)** — asserted by pressing the real card in a mounted screen, not by matching source text. `openDocument` receives the granted URL, the foundation media id, mime type, expected bytes, surface and title.
- [x] **A thread does not download the videos in it (§11)** — `imageUris()` censuses every `<Image>` in the tree; the movie's URL appears in none. 37.27 MB → 79.3 KB measured.
- [x] **Stored video up to 90 minutes (5400s)** — per-surface ceilings, enforced on the *measured* duration from Mux/ffprobe, reasserted on every delivery, with a reconciler for assets no webhook reported. 90:00 accepted, 90:01 refused.
- [x] **The shared foundation is reused, not forked (§36)** — one `openDocument`, one grant hook, one upload transport; Messenger, Posts and Reels read the same policy module.
- [x] **No performance claim without a measurement (§48–49)**
- [x] **Media access is transport authorization keyed on the foundation id (§53)**
- [x] **Security tests pass** — signed-URL tampering refused; a conversation member cannot sign another member's parts; a client cannot claim a part it never sent; blocked attachments refused to every reader.
- [x] **Mutation tests pass** — 39/39, each killing a named test.
- [x] **RTC changes = 0** — 0 of 74 protected paths; gate passes on all 10 commits; the two grep hits explained above.
- [x] **No push** — nothing left this machine. `origin/main` moved during the session because
  another session pushed to it; each mission commit was then re-checked with
  `git merge-base --is-ancestor` and none is reachable from it.

### Not done, and it is not code

**Device QA.** Everything above is static, unit and integration evidence. Not verified on
hardware: a real 90-minute `.mov` uploaded over cellular with a mid-upload network change; a
PDF opening in the iOS document viewer; thumbnail paint on a cold cache in a long thread. The
protection policy is explicit that static checks do not replace device QA for uploads, and I
am not going to launder a green suite into a hardware claim. `docs/media/MEDIA_DEVICE_QA.md`
holds the checklist.

**One residual foreign failure**, stated above, owed by another session.
