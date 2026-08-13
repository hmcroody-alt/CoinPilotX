# Advertising event taxonomy

Every number the advertising system reports is a count of these events. If the
events are wrong, everything downstream is wrong in a way no amount of careful
analysis recovers, so this layer is deliberately the strictest one.

Canonical definitions: `services/business_os/ads_intelligence/taxonomy.py`.

## The vocabulary is closed

`ALL_EVENT_NAMES` is a frozenset. An event whose name is not in it is **rejected
at ingest** — not coerced to a nearby name, not stored under an `other` bucket.

The reason is that an open vocabulary makes a typo indistinguishable from a
feature. `ad_click` and `ad_clicked` arriving from two client versions produce
two metrics that are each half right, and nobody notices until a quarter-end
number is questioned. Rejecting the unknown name makes the client bug loud on
the day it ships.

### Families

| Family | Events |
| --- | --- |
| Opportunity | `ad_opportunity_created` |
| Delivery | `ad_served`, `ad_rendered`, `ad_viewable` |
| Engagement | clicks and positive interactions |
| Negative | explicit (`ad_hidden`, `ad_reported`, …) and inferred |
| Conversion | advertiser-reported outcomes |

`event_family(event_name)` is the single mapping. `ads_intel_events.event_family`
is `NOT NULL` and derived at write time — it is never supplied by a caller,
because a caller that can choose the family can put a click in the delivery
bucket.

Explicit and inferred negatives are separated (`EXPLICIT_NEGATIVE_EVENTS` vs
`INFERRED_NEGATIVE_EVENTS`) because they justify different actions. "I pressed
hide" is a statement; "you scrolled past fast" is a guess, and suppressing a
whole category on a guess is how a feed becomes useless.

## The viewability contract

An impression is not "we sent it". `viewability_met()` requires:

| | Minimum visible | Minimum duration |
| --- | --- | --- |
| Static | 50% | 1000 ms |
| Video | 50% | 2000 ms |

Both conditions, measured client-side and re-checked server-side. Thresholds are
constants (`VIEWABILITY_STATIC_MIN_PERCENT`, …) rather than literals scattered
through the code, so "what counts as seen" is one edit and one review.

`BILLABLE_CANDIDATE_EVENTS` is `{ad_viewable, ad_click}` — *candidate*, not
billable. An event must additionally survive invalid-traffic screening (see
[`ads_measurement.md`](ads_measurement.md)) before it can be charged.

## Events clients may not send

`CLIENT_FORBIDDEN_EVENTS` names events that only the server may write —
conversions and anything that directly implies a charge. `api.py` additionally
filters incoming payloads to `CLIENT_EVENT_FIELDS`, so a client cannot set
`validity`, `quality_status`, `processing_version`, or a billing flag by
including it in the JSON.

This is a trust boundary, not a convenience: the client is an untrusted reporter
of things it can observe (was it visible, was it tapped) and has no authority
over things it cannot (was it valid, was it billable, was it converted).

## Time bounds

| Bound | Value | Why |
| --- | --- | --- |
| `MAX_EVENT_AGE_HOURS` | 48 | Older is a stuck queue, not a late user |
| `MAX_EVENT_FUTURE_MINUTES` | 2 | Allows clock skew, rejects fiction |
| `MAX_PLAUSIBLE_DURATION_MS` | 300 000 | A 3-hour "impression" is a backgrounded tab |
| `MAX_BATCH_EVENTS` | 500 | Bounds one request's blast radius |

Out-of-bounds values are not silently clamped into range. Clamping produces a
plausible-looking number that is a lie; the event is marked instead, and the
share of marked events is itself a gate (see
[`ads_data_quality.md`](ads_data_quality.md)).

## Idempotency

Every event carries a client-generated idempotency key. `ingest_batch` and
`record_event` treat a repeat as a no-op that reports success.

This matters more than it sounds. Mobile clients retry on flaky networks; a
retry storm during an outage is exactly when the numbers are most scrutinised.
Without idempotency, the recovery from an incident inflates the impression
counts of that incident, and the inflated number is the one an advertiser sees.

Dedupe is at the storage layer, so it holds regardless of which entry point was
used and regardless of how many workers are running.

## No-fill reasons

`NO_FILL_REASONS` is a closed list, and an opportunity that returns no ad
records one. This is the difference between "we served 40 ads" and "we had 1000
opportunities, served 40, and here is the named reason for each of the other
960" — the second is a system you can improve.

Reasons map to an owner in [`ads_advertiser_experience.md`](ads_advertiser_experience.md);
some are the advertiser's to fix and some are ours, and saying which is the
whole value of recording them.

## Adding an event

1. Add the name to the right family tuple in `taxonomy.py`.
2. If the client may send it, confirm it is not in `CLIENT_FORBIDDEN_EVENTS`.
3. If it implies a charge, add it to `BILLABLE_CANDIDATE_EVENTS` **and** add an
   invalid-traffic rule for it. A billable event with no fraud rule is a hole.
4. Bump `EVENT_SCHEMA_VERSION` if the meaning of an existing event changes.
   Adding a new name does not change existing meanings; redefining one does, and
   `ml_readiness` needs the bump to notice.
5. Tests in `tests/business_os/test_ads_intelligence_events.py` assert the
   vocabulary is closed — they will fail until the name is registered properly.
