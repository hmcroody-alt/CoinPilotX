# Advertising privacy model

Module: `services/business_os/ads_intelligence/privacy.py`, with the
vocabularies in `taxonomy.py`.

The constraint this layer is built around: advertising quality comes from
knowing things about people, and the way that goes wrong is never one big
decision. It is a series of individually reasonable additions, each of which
made the model slightly better, which add up to a profile nobody would have
approved as a single proposal. So the boundaries here are structural — a thing
that cannot be looked up cannot be gradually adopted.

## Subjects are hashed, not identified

`subject_ref(user_id)` returns a salted hash. No advertising table stores a
`user_id`, and the intelligence layer never has one except in the request that
derived it.

The salt comes from `ADS_INTEL_SUBJECT_SALT`. A development fallback exists so
local work runs, and it is a *different* salt — dev-derived refs cannot be
joined against production ones by accident.

`session_ref()` is the same construction for sessions.

Downstream, `api.py` derives `subject_ref` **server-side from the authenticated
viewer**. There is no `subject_ref` parameter on any endpoint. This is what
makes "show me another user's ad interests" not a permission check that could be
got wrong, but a request that cannot be expressed. The tests assert the absence
of the parameter via `inspect.signature`, so adding one fails CI.

## Purpose limitation

Every signal carries a `privacy_class`, and each class permits a fixed set of
purposes:

| Class | May be used for |
| --- | --- |
| `product_signal` | Personalisation, measurement, security |
| `measurement_only` | Measurement and security — **not** targeting |
| `security_only` | Fraud and abuse defence only |

`allows(privacy_class, purpose)` is the gate. The important case is
`measurement_only`: data collected to count things cannot drift into deciding
what somebody sees. Without an explicit gate that drift is not a decision anyone
makes — it is what happens when a query written for a report turns out to be
convenient in the ranker.

## The sensitive-data firewall

`FORBIDDEN_SIGNAL_SOURCES` names surfaces that may never produce an advertising
signal at all, checked by `is_forbidden_source()`. Separately,
`context.py` refuses ads outright on `PRIVATE_SURFACES` and treats
`SENSITIVE_CONTEXT_CATEGORIES` as non-targetable.

Two properties worth stating plainly:

- **Private communication is not an input.** Direct messages and calls do not
  produce interest signals. Not weighted down — absent.
- **Sensitive context does not become a category.** The closed
  `INTEREST_CATEGORIES` list has no health, sexuality, religion, or political
  entries, so there is no bucket for one to be inferred into. A category that
  does not exist cannot be targeted by an advertiser who asks nicely.

The firewall is the list, not a filter applied to a richer list, because a
filter is one `if` away from being bypassed and an absent vocabulary is not.

## Minimum audience

`MIN_AUDIENCE_SIZE = 1000`, enforced by `interest.meets_minimum_audience()`.
A category matching fewer than a thousand subjects cannot be targeted.

Targeting is an information channel. Narrow enough targeting plus an observed
impression identifies an individual, and the advertiser did not have to break
anything to learn it. The floor closes the channel.

## Retention

`RETENTION_DAYS` per record class, via `retention_days()`. Raw events expire
soonest; derived aggregates, which are not about an individual, live longer.

`interest.forget_subject(conn, subject_ref)` deletes a subject's derived
affinities and returns the row count. It is a real deletion, not a flag.

## What a user can see and control

Covered in full in [`ads_transparency.md`](ads_transparency.md). In summary:

- `GET /api/business-os/ads-intel/my-interests` returns the *categories* only —
  never scores, never signal counts, never source events. A score is a lever for
  probing; a category is an answer.
- Every disclosed category maps to a control that writes an explicit negative
  event, and explicit negatives are weighted decisively in `interest.py`.

## Working on this layer

- Do not add a `subject_ref` or `user_id` parameter to an endpoint.
- Do not add an interest category without checking it is not a proxy for a
  sensitive one. "Interested in a parenting brand" is not far from a medical
  inference, and the closed list is where that judgement gets made.
- Do not read from a `FORBIDDEN_SIGNAL_SOURCES` surface, including for
  measurement. Measurement is how the read gets written; targeting is how it
  gets used two quarters later.
- Do not relax `MIN_AUDIENCE_SIZE` for a specific advertiser. That is the
  request that always arrives, and the floor is worth exactly as much as the
  number of exceptions to it.
