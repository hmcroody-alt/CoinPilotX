# Page search and admin inspection

## Search — one index, two entity kinds

Pages are surfaced through the **existing** `/api/pulse/search` handler, in a
`presences` result group alongside `posts`, `creators`, and the rest. There is no
second search endpoint and no change to how any other group is ranked or queried.

Every presence row carries `type: "presence"` and `presence_type` (the page type).
That label is what stops a visitor reading an artist or a restaurant as a personal
account. The native client (`SearchScreen.openResult`) branches on it first and routes
to the `Page` surface; a presence is never handed to the personal-profile navigator,
which would try to resolve it against a `users` row that does not exist.

The population block is wrapped in try/except and logs on failure: a search backend
that cannot reach pages still returns posts, people and everything else.

`search_pages()` matches name, handle and category, and returns `public_view` rows —
so a search result cannot carry phone numbers, owner ids, members or links.

## Visibility

Public search returns `status='ACTIVE'` pages only. A paused, unpublished or
deactivated presence disappears from discovery while its row and audit history stay
intact (Page OS has no hard delete).

`search_pages(..., include_inactive=True)` lifts that filter. It exists for one
caller: the admin console, whose job is to find the paused page.

## Admin inspection

Two routes, both behind `require_admin_api("users.view")` and both audited via
`log_admin_audit`:

- `GET /api/admin/pages?q=` — search including inactive pages.
- `GET /api/admin/pages/<id>` — `admin_overview()`: type, handle, status,
  verification status, owner, measured follower/post counts, the member roster, the
  links, and the last 20 audit entries.

`admin_overview` **reads**. It has no branch that writes: no seizing a page, no
granting verification, no changing a role. Admin authority to act on a page runs
through the same role-checked functions as everyone else, so the audit trail records
the same shape of event. A test asserts the page row and its audit count are byte-for
-byte unchanged after an overview call.
