# Pages × UNDX

UNDX (the AI mission/execution layer) can operate **in the context of a page**, but
only inside the requesting member's role boundary.

## Context endpoint

`GET /api/pages/:id/undx-context` (member-only) returns a role-bounded context
object: page identity fields, the caller's role, and the capability set derived
from the `PERMISSIONS` matrix. UNDX consumes this to know what it may do on the
page's behalf — e.g. draft content only if the caller has `create_content`, discuss
ads only with `manage_ads`.

## Boundaries

- UNDX acting for a page can never exceed the human's role. An ANALYST asking UNDX
  to publish a page post is refused because the context carries no
  `create_content` capability.
- UNDX never receives OWNER-only powers (`manage_status`, `transfer_ownership`)
  as delegated actions; those require the human flows (confirm dialogs, the
  `TRANSFER` phrase).
- Page context is read-composed per request; nothing about a page grants UNDX
  standing authority.

## Sentinel (observe-only)

Sentinel's vocabulary gains a `page` entity and an `owns_page` edge (additive, in
`services/sentinel/entities.py` and `graph.py`). Emission from Page OS writes is
wrapped in try/except: Sentinel being down never fails a page operation. Sentinel
observes and may flag — it **never** auto-seizes pages, auto-verifies, or mutates
page state.
