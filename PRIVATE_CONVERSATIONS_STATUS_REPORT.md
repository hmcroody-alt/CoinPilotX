# Private Conversations — status report

This is the Stage 137 report, written honestly rather than triumphantly. The
mission defined 137 stages. A minority of them are done and proven, a few are
done but unproven, and the majority are not started. This document says which is
which, because a report that reads as if the work is finished is worse than no
report at all — it is the thing a future engineer trusts and should not.

The governing constraint of the mission was "no second anything". That
constraint is intact, and most of what follows is the evidence for it.

## What is done and proven

**The classification layer.** `services/private_office/conversations.py` owns
exactly two tables, `private_office_conversations` and
`private_office_conversation_links`. Neither can hold a message body, a sender or
an attachment id — asserted directly against the live schema, not by inspection.
Membership is answered by the canonical service's `_conversation_access`; there
is no second participant query anywhere on this path. Schema is created
idempotently inside the request that needs it and by `bot.init_db` at boot, never
at import and never at route-pack registration. That last detail is deliberate:
Stage 176B recorded a failure where schema born inside a route handler meant a
worker process died every cycle in a shape indistinguishable from a healthy empty
sweep.

**The HTTP surface.** `services/private_office_conversations_routes.py` mounts
eleven routes under `/api/private-office/conversations`. Every one runs the same
gate chain — session, then tier and implementation state through the feature
matrix, then the Office second lock — and the order is itself tested, because
answering the lock before the tier would leak the existence of a feature the
caller is not entitled to. `/capabilities` is gated like everything else and
reports `end_to_end_encrypted=False`.

**Fail-closed flagging.** `private_office.conversations` sits in the feature
matrix as server-enforced, `TIER_PRIVATE`, behind `PRIVATE_CONVERSATIONS_ENABLED`
with `flag_default_on=False`, matching the `private_meetings` precedent. An
absent env var means the surface does not exist. Turning the flag off removes the
Office *view* of these threads, not the threads: messages live in
`pulse_communications_v2` and attachments in the media foundation, so a disabled
surface leaves every conversation intact and reachable in ordinary Messenger.

**Document attachments.** The shared server-side allowlist in
`services/messenger_media_foundation.py` now carries PDF, the three OOXML Office
types and their three legacy counterparts, plain text and CSV — all as the
pre-existing `file` media type under `MESSENGER_FILE_MAX_MB`. The foundation was
always shaped for this (`MEDIA_TYPES` has carried `"file"` since it was written)
but no document MIME type was ever allowlisted, so the branch was unreachable.
Widening the shared allowlist was the correct fix; a private Office upload path
would have been a second attachment store. Ordinary Messenger gains the same
capability in the same change.

The security half of that change matters more than the list. Attachments are
served from the product's own origin and the download route served everything
inline. Each allowlist entry now declares a disposition and both delivery paths
read it — `send_file` via `as_attachment`, and the presigner via
`ResponseContentDisposition`. The second is the one that would have been missed:
a presigned URL never reaches our route, so fixing only `send_file` would have
left every object-storage-backed document rendering inline, which is the
production path. `disposition_for` defaults to `"attachment"`, so an entry whose
author forgets the key cannot open a rendering path. `text/html` and
`image/svg+xml` are absent from the table rather than admitted and mitigated,
because a stored-XSS defence should not depend on one header staying correct.

**Anti-vacuity (Stage 124).** Eleven mutations were run against these files. Six
against the classification layer and its routes: dropping the lock gate; trusting
the URL ref instead of resolving membership; rendering a failed list as an empty
one; letting any participant change sensitivity; reverting the error-envelope
fix; skipping the gate chain on `/capabilities`. Five against the media
foundation: defaulting `disposition_for` to inline; marking PDF inline; dropping
`ResponseContentDisposition`; admitting `text/html`; restoring
`as_attachment=False`. Ten were caught immediately. One was not — `int(ref)` in
place of the canonical ref resolution survived the whole suite, because every
test happened to address threads by numeric id while `_conversation_access` also
accepts a `public_id`. A test exercising both addressing modes now closes that
gap and the mutation is caught.

**One defect worth recording.** The canonical envelope from `service._err` puts
the *code* in `status` and the numeric HTTP code in `http_status`. Both the model
and the route layer read `status` as a number, so `int()` raised on every honest
refusal and a plain 400 — `invalid_recipient`, say — reached the client as a 503
with a stack trace, inviting a retry of a request that could never succeed. Both
layers now prefer `http_status`. The regression test drives the real envelope
rather than a hand-written one, since a hand-written envelope is exactly what hid
it.

**Counts.** 57 tests for the classification layer and its HTTP surface, 18 for
the document allowlist. The 250-test protection suite is green. The `bot.py` diff
across both commits is seventeen lines with no hit against the realtime-audio
`backend_diff_patterns`. Zero Agora, LiveKit, RTC or livestream files were
touched; there is no LiveKit reference anywhere in the new code, and that is
asserted by a test rather than claimed here.

## What is done but not proven

`tests/test_messenger_document_attachment_identity.py` — the Stage 30/114
attachment-identity regression — walks the full lifecycle against a real
database: init, upload, attach, read, download target, with a non-participant
refused at each of the five entry points separately and a participant-who-is-not-
the-sender refused at the write. It was passing when the Linux workspace failed
with `no space left on device`, but its mutation battery never ran, so it is
green-but-unproven and **uncommitted**. Treat it as unverified until the three
planned mutations (membership check always passes; upload drops
`require_sender`; download target hardcodes inline) have been run against it.

## What is not started

The native Private Conversations screens (home, thread, info), cross-domain link
surfacing inside the documents, records, facts and meetings screens, i18n across
the eleven locales, roughly thirteen of the specified test matrices, the
Messenger and Private Office regression suites, `npm run verify`, and the full
protection-suite run.

## What cannot be completed from here

The physical-device acceptance on P3R7OR (Stage 129), the three-participant group
acceptance (Stage 130), and the iPhone 17 Pro Max simulator pass (Stage 131) need
hardware. They are not blocked by anything in the code; they simply have not been
performed, and nothing in this report should be read as a substitute for them.
Static checks do not replace device QA for livestream, push, checkout or uploads.

## Carried-forward findings

There is **no cryptographic end-to-end encryption** on this path. `capability_states()`
reports `end_to_end_encrypted=False`, a test asserts that no response in a full
request cycle claims otherwise, and no surface may claim otherwise in future.

Comm-v2 is dual-mounted at `/api/pulse/communications/v2` and
`/api/pulse/comm/v2`. Do not add a third alias.

Stage 107 account-switch cache isolation still needs verifying against the
AsyncStorage keys `pulsesoc.native.messenger.v2.{conversations,messages.<id>,outbound_queue}`.

## Release state

Two local commits, neither pushed, per the mission's release policy:

* `2bd51cb0` — the classification layer and its HTTP surface (10 files).
* `10712525` — the document allowlist and the disposition fix (4 files).

A repo hazard, unrelated to this work but worth knowing: `.git` sits on a mount
that permits writes but not unlinks, so git strands lock files it cannot clear. A
pre-existing `index.lock` and two `HEAD.lock`s were parked by renaming rather
than deleting. The shared git index also carries six files staged by a concurrent
agent, so both commits were made through a private index built from HEAD, with
the shared index restored to exactly those six afterwards.
