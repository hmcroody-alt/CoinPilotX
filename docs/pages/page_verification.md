# Page Verification

Page verification is **distinct from user verification** and is never auto-granted
— not by page creation, not by owner verification status, not by Sentinel, not by
any metric threshold.

## States

`unverified → pending → verified | rejected` (`VERIFICATION_STATUSES` in
`services/pulsesoc_pages.py`).

## Flow

1. The **OWNER** submits `POST /api/pages/:id/verification` with supporting details
   (`manage_status`, alongside payments and settings). State moves to `pending` and
   the request is audited. Asking the trust team to certify that this presence is who
   it claims to be is a statement about identity, not a content task.
2. Review is a human/admin decision outside Page OS write paths. Page OS exposes the
   state; it never flips itself to `verified`.
3. `rejected` pages may re-apply; `verified` is reflected in `public_view` and in
   feed attribution (verified chip next to the page name).

## Native behavior

- `PagesHubScreen` shows **Request verification** only while the page is
  unverified, plus a note that verification is reviewed and never automatic.
- `PageScreen` renders the Verified chip only when the server says
  `verified: true`. No client-side inference.

## Guarantees

- A page's verification never transfers from its owner's personal verification.
- Ownership transfer does not carry, grant, or reset verification silently — the
  state stays with the page and the transfer is audited.
