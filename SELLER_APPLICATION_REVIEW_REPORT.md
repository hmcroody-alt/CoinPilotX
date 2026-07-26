# PulseSoc Native — Seller Application and Admin Review System

Branch `release/undx-nexus-core-v4`. No build was produced, no build number was
changed, and nothing was committed or pushed.

---

## 1. Foundations reused, and what was deliberately not built

The mission's hardest constraint was the seventh: no duplicate seller,
Marketplace, identity, verification or admin systems where canonical
foundations already exist. The inventory found that the foundations existed but
were not being used as foundations.

`marketplace_merchant_applications`, `marketplace_merchant_documents` and
`marketplace_sellers` were already the canonical tables, and
`save_private_verification_document` was already the canonical way a
verification file reaches disk. All three tables and that helper are reused
unchanged. No new seller table, no second document store, no parallel identity
record.

What was missing was not storage but a single place where the *rules* lived. So
the one new module, `services/seller_lifecycle.py` (1,191 lines), holds the
status vocabulary, the transition table, the field schema, the applicant view,
the scoring and the queue — and both front doors now call it rather than
each implementing the lifecycle themselves.

## 2. The native application flow

`mobile-native/src/screens/SellerApplicationScreen.tsx` (1,147 lines) is a
fully native multi-step flow. There is no web view and no redirect anywhere in
it. It opens on an introduction that says what will be asked for and what
happens after submission, then walks the applicant through seller type,
identity or business details, storefront, what they intend to sell, fulfilment,
payout and tax, documents, and consent, ending on a review-and-submit step.

Two decisions are worth naming. The step schema is served by the API rather
than hardcoded in the client, so the steps the applicant sees are generated
from the same definition the server validates against and cannot drift out of
agreement with the rule that gates them. And the client never decides an answer
is good enough — it renders the errors the server returned. A client that
validated independently would eventually disagree with the server, and the
applicant would be the one who found out.

Answers autosave as the applicant moves between steps, guarded by a dirty flag
so leaving a step they only read does not fire a write. The autosave endpoint
is structurally incapable of changing status (section 6), which is why it is
safe to fire without a confirmation.

## 3. The state machine

Ten statuses, exactly as specified: `draft`, `submitted`, `under_review`,
`information_requested`, `resubmitted`, `approved`, `rejected`, `withdrawn`,
`expired`, `suspended`.

The security property is expressed as data rather than as control flow:

```python
TRANSITIONS: Dict[Tuple[str, str], Tuple[str, ...]]
```

Each key is a (from, to) pair and each value is the set of actor types allowed
to make that move. Making it a table means the property "no applicant can reach
approved" is checkable by reading one structure, and testable by enumerating it
rather than by spot-checking the paths someone happened to think of.

Legacy rows are handled by aliasing on read (`pending_review` → `submitted`)
rather than by migrating them, consistent with the repo's schema rule of
`add_columns_if_missing` and never `ALTER`/`DROP`.

## 4. The admin entry point

`/admin/command-center` carries a dedicated `Seller Applications` control
(bot.py:87608) rather than burying the queue inside a department room, because
this is the one queue where a person is waiting and nothing happens until an
administrator acts. It shows the live pending count and a breakdown of new,
resubmitted, in-review and info-requested.

It glows only when the count is non-zero. A permanently glowing badge teaches
administrators to ignore it, which would defeat the point of making it
prominent. The whole control is gated on `monetization.manage`, so an admin
without that permission does not see a count they cannot act on.

## 5. The review queue and workspace

`/admin/merchant-applications` (bot.py:85451) is the queue. Status chips with
live counts, free-text search across name, email, business and username, a
"mine" filter for the reviewer's own assignments, and per-row expansion into
the workspace: full answers, the private documents, the status timeline, the
internal notes, the assignment control and the decision actions.

`search_queue` fetches a bounded 500 rows and then filters in Python. That is
deliberate rather than lazy: a `WHERE status IN (...)` would silently hide every
application written before this module existed, whose status strings are the
legacy vocabulary. Filtering after normalisation means old rows appear in the
queue instead of vanishing from it.

## 6. Decisions and authorisation

Every status change goes through `apply_transition`, which calls
`assert_transition` first. Approval is restricted to `ADMIN` by the transition
table, and then restricted a second time inside `assert_transition`: an admin
transition carrying no admin id is refused rather than recorded as actor 0.
The guard is stated twice on purpose — the table controls *who*, but the
identity of the admin is what makes the decision auditable.

Nothing approves automatically. There is no timer, no score threshold and no
code path that reaches `approved` without an identified administrator.

**A real bug was found here by a test.** `assert_transition` normalised the
requested target before validating it, and `normalize_status` falls back to
`draft` for anything it does not recognise. The consequence was that the
"Unknown application status" guard was unreachable dead code, and a misspelled
or injected target became a *silent move to draft* rather than a refusal. The
fix validates the raw target first and normalises second — normalise-on-read is
right when reading a stored row and wrong when validating a requested write.

**A second, larger defect was found in the web front door.**
`pulse_merchant_apply_page` was a complete second application system: it
INSERTed a fresh row on every submit, wrote `status` directly into the column,
and upserted `marketplace_sellers` itself. Three consequences followed. A user
who submitted twice had two applications in the queue and a reviewer with no
way to tell which was current. No status change left a history row, so a web
application arrived in the workspace with an empty timeline. And the seller
record received the *application's* status vocabulary (`pending_review`) while
every capability gate compares against the *seller's* (`approved`) — the gate
held, but only by the luck of neither string matching. It now routes through
`seller_lifecycle` exactly as the native flow does. This is the mission's
constraint 7 enforced rather than merely respected.

## 7. Documents and privacy

Six document types, uploaded as multipart so a passport photo never has to be
held in memory as a base64 string on either side. Files go to
`instance/private_uploads/merchant_verification` outside the web root via the
existing helper. The native module holds only the server's metadata for a file:
nothing is written to the application cache, and there is no `console` call
anywhere in the seller API module or screen.

Serving is admin-only (`monetization.manage`) with a path-containment check
against the private root, so a crafted `stored_path` cannot escape the
directory. Applicant-side document endpoints derive the application from the
session user — the upload route never accepts an application id, and the remove
route is scoped by `user_id` as well as document id, because an application id
in a URL is not proof of ownership.

**Constraint 6 fix.** `MERCHANT_DOCUMENT_MISSING` logged the document's stored
path. A verification document's path encodes the applicant's user id and the
document type, so logging it copies identity metadata out of a private uploads
directory and into a log file with a different retention policy and a wider
audience. The path argument was removed; the id is enough to find the row.

A scan of the whole file for logging of `stored_path`, `original_filename`, or
any identity field now returns exactly one hit: `TEACHER_DOCUMENT_MISSING` at
bot.py:85763, which is the identical defect in the adjacent teacher application
flow. It was left alone under constraint 10 and is flagged here instead — it is
a one-argument fix whenever the teacher flow is next in scope.

## 8. Disclosure: what an applicant can never see

`applicant_view` and `applicant_document_view` are whitelists, not redaction
passes. `internal_notes`, `risk_score`, `reviewer_id` and `stored_path` are not
stripped from the output — they are never put into it. The distinction matters
because a redaction pass has to be updated every time a column is added, and a
whitelist does not.

`_seller_application_response` is the single choke point through which every
applicant-facing seller response passes, so there is one function to audit
rather than six routes.

`notes_for` takes `visibility` explicitly at every call site and has no default
that would return internal notes to a non-admin, and an unrecognised visibility
is treated as internal — failing closed, so a typo cannot publish a reviewer's
note to the person being reviewed.

## 9. Notifications

Administrators are nudged on every arrival, from both doors:
`notify_seller_review_admins` fires on submit and resubmit. Without it a web
submission would sit in the queue with nothing on the admin board pointing at
it — which was the behaviour before this work.

Applicants are notified on decision, and the notification carries the
applicant-facing message and next action only, drawn from the same
`applicant_view` whitelist as the screen.

## 10. Capability gating and appeals

Gating was audited across all ten call sites (bot.py:46892, 46987, 47317,
47393, 47437, 81136, 81283, 81655, 81672, 81718) plus
`approved_marketplace_seller_for_user` at 80994. Every one compares
`marketplace_sellers.status == "approved"`. No gate reads the *application*
status, so buyer account state and seller approval state remain the separate
concerns constraint 9 requires. `mirror_seller_record` is now the sole writer
of that column.

The appeal path is rejected → draft → submitted on the *same row*, so the
history survives the reappraisal and a reviewer seeing a second submission can
see the first decision and its reason. Verified from both doors.

## 11. Accessibility

`SellerApplicationScreen.tsx` is in the guarded list of
`accessibilityBaseline.test.ts`, which parses the source and fails if any
`Pressable`/`Touchable` lacks both an accessible name and an explicit removal
from the accessibility tree, or lacks an `accessibilityRole`. It parses rather
than regexes because a JSX opening tag routinely spans several lines and
line-oriented matching attributes props to the wrong element.

The completeness percentage is defended at the parse boundary: `Number("later")`
is `NaN`, and `NaN` survives both `Math.min` and `Math.max` unchanged, so a
single non-numeric value would flow straight into the progress bar's width and
into the label a screen reader announces. Anything not a real number reads as
zero.

## 12. Performance

Every lifecycle query is bounded. `search_queue` caps at 500 rows;
`get_application` and `get_application_by_id` are `LIMIT 1`; documents are at
most one row per type.

**One N+1 was found and fixed.** The queue page called `history_for` and
`notes_for` once per application inside a loop — two queries per row, up to a
thousand queries on the "all" filter, which is precisely the page a busy
reviewer opens. Documents were already batched with an `IN` clause; history and
notes now are too, via new `history_for_many` and `notes_for_many`, with five
tests pinning that the batched result says exactly what the per-row function
says and that the visibility filter survived being moved into the `IN` clause.

On the native side the parser is total — a missing or malformed key becomes a
safe default rather than an exception — because a half-parsed response must not
be able to strand an applicant on a blank screen mid-application. Autosave is
guarded by a dirty ref so reading a step does not write.

## 13. Validation evidence

```
bot.py — ast.parse                                    OK
services/seller_lifecycle.py — ast.parse              OK
tests/test_seller_lifecycle.py                        87 tests, OK
tests/test_presence_service.py                        18 tests, OK
tsc --noEmit (whole native tree)                      EXIT=0
jest — 5 seller/a11y suites                           55 tests, PASS, EXIT=0
mobile-native/app.json buildNumber                    "4", unchanged
git status                                            no commit, no push
```

The Python suite is 87 tests across ten classes, organised around the three ways
this decision can go wrong rather than around the functions that implement it:
authorisation (the transition table is enumerated exhaustively, because a
property stated as "no path exists" is only worth asserting if every path is
tried), disclosure (the views are fed rows deliberately contaminated with
reviewer notes and risk scores, and the output's key set is asserted to be
exactly the documented one), and auditability (every status change leaves a
history row, and no history row carries a filename or a field value).

Four pre-existing Python suites fail — `test_pulse_settings_routes`,
`test_pulse_repost_routes`, `test_pulse_repost_toggle`,
`test_pulse_comment_pagination` — all with `ModuleNotFoundError: No module named
'flask'` / `'werkzeug'`. This is environmental: the sandbox's PyPI registry
returns 403, so Flask cannot be installed. These four are unrelated to this work
and fail identically before it. `test_seller_lifecycle.py` deliberately avoids
Flask entirely, which is why it runs: it builds a real in-memory SQLite from the
production table definitions and never imports `bot`, because importing a
hundred thousand lines that need a live config would test the import rather than
the lifecycle.

**Not verified: runtime QA.** Simulator, admin browser and physical-device
passes could not be performed. The sandbox has no iOS simulator and no browser
session against a running instance, and the mission forbids producing a build.
Everything below is static, and everything static is green.

---

## Verdict: PARTIAL

Every implementable phase is complete and validated. The system is
server-authoritative, auditable, and gated: approval is unreachable except by an
identified administrator, applicant payloads are whitelists rather than
redactions, and both front doors now share one lifecycle instead of two. Three
real defects were found and fixed rather than merely documented — the
unreachable unknown-status guard, the parallel web application system, and the
document path in the logs — plus one N+1 in the reviewer's busiest page.

It is PARTIAL rather than PASS for one reason, and it is a real one: phases 36,
37 and 38 are runtime QA, and no code was run against a simulator, a browser or
a device. Static analysis cannot tell you whether the flow *feels* simple, which
is the mission's actual product goal. That verification is outstanding and needs
a human at a screen.

The teacher-flow log line at bot.py:85763 is a known identical defect left
outside scope under constraint 10.
