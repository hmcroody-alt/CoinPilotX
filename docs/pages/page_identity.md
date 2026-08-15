# Page Identity & Switching

## Model

Identity is a *presentation actor*, not an auth principal. The authenticated user is
always the same `users` row; acting "as a page" means the write is attributed to a
page the user has a posting-capable role on.

`GET /api/pages/identities` returns:

```json
{ "ok": true,
  "personal": { "kind": "personal", "id": <user_id>, "name": ..., "handle": ..., "avatar_url": ... },
  "pages": [ { "kind": "page", "id": <page_id>, "name", "handle", "avatar_url",
               "page_type", "role", "verified" } ] }
```

Only pages where the member's role has the `post` permission appear.

## Native switcher

`FeedComposer` loads identities when opened. If the user has at least one page, the
composer shows a **"Posting as <Name>"** label and horizontal identity chips
(Personal + each page) *before* content creation — the mission's explicit-attribution
requirement. Selecting a page routes publish through
`POST /api/pages/:id/posts`; Personal keeps the existing composer path untouched.
If the identities call fails, the composer silently falls back to personal-only —
identity switching is additive, never a gate on posting.

## Attribution

Page posts live in `pulse_posts` with `page_id` set. The feed serializer
(`pulse_feed_engine._page_author`) swaps the author block for the page's name,
handle, avatar, and verified flag at read time. The ranking pipeline is unchanged.

## Guarantees

- No separate logins or fake accounts; sessions and tokens are per-user only.
- Switching identity never changes permissions elsewhere in the app.
- A page identity can only be assumed by members whose role grants `post`.
