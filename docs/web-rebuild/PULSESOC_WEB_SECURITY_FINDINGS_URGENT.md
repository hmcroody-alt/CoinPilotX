# PulseSoc — Urgent Security Findings (pre-rebuild)

Status: **verified by direct execution**, 2026-09-12. These are findings in the *existing*
production website layer, not hypotheticals about the rebuild. They are listed here because
the rebuild plan must not inherit them, and because the highest-severity item warrants a fix
independent of the rebuild schedule.

---

## SEC-1 — `clean_html()` is a tag stripper, not an HTML escaper (systemic, HIGH)

`bot.py:119744`

```python
def clean_html(text):
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text
```

This is the codebase's de-facto sanitiser for user-supplied text rendered into HTML.

**Usage ratio: `clean_html(` appears 2,217 times in bot.py. `html_escape(` appears 4 times.**

### Why it does not work

It removes *well-formed* tags. It does not escape anything. Verified by execution:

| Input | `clean_html()` output |
|---|---|
| `<script>alert(1)</script>` | `alert(1)` — neutralised |
| `<img src=x onerror=alert(1)` | `<img src=x onerror=alert(1)` — **passes through intact** |
| `"><svg onload=alert(1)` | `"><svg onload=alert(1)` — **passes through intact** |
| `' onmouseover=alert(1) x='` | `' onmouseover=alert(1) x='` — **passes through intact** |
| `Tom & Jerry` | `Tom & Jerry` — `&` never encoded |

Two independent bypasses:

1. **Unterminated tag.** The regex `<[^>]+>` requires a closing `>`. A payload with no `>`
   is not a match, so it is copied through verbatim and the browser completes the tag
   against surrounding markup.
2. **No entity encoding.** `"`, `'`, `&` and `<` survive. Any interpolation into an
   attribute (`class='{value}'`, `href='{value}'`) is a straightforward quote-breakout,
   regardless of tag well-formedness.

### Confirmed render sites

- `bot.py:54126` — marketplace listing title: `f"<h1>{clean_html(row.get('title'))}</h1>"`.
  Cross-user readable. Stored input, written at ~`bot.py:94609`. Mitigating factor: listings
  pass an approval step, so this is not drive-by — but approval is a human review of
  *content*, not of markup.
- `bot.py:54005` — the same value on the marketplace grid.
- `bot.py:28760` — admin analytics console: `f"<td>{str(cell)[:180]}</td>"` with **no
  sanitisation at all**, rendering raw database cells that originate from users, into an
  admin's session. This is the classic privilege-escalation shape: user-controlled data
  executing in an administrator context.
- `bot.py:28779` — `request.args` reflected unescaped into an `href`. Admin-password gated,
  so not exploitable unauthenticated — but note the admin password travels in the query
  string (`?password=...`, see `bot.py:28758`), which puts it in logs, history, and referrers.

### What is clean

- SQL is parameterised throughout — no injection found.
- `next_url` at `bot.py:29445` is `quote()`d.
- The `script_html` parameter of `pulse_social_shell` has no request-data call site.

### Recommended handling

Do **not** attempt a blanket find-and-replace of 2,217 call sites; that is how escaping bugs
get introduced. Recommended order:

1. Fix `bot.py:28760` now — it is unsanitised and targets admins.
2. Introduce a correct escaper and migrate the *render* sites that interpolate
   user-controlled fields into HTML, highest-traffic first (marketplace, profile, post body).
3. The rebuild resolves this class structurally: a template engine with contextual
   auto-escaping (Jinja `autoescape=True`, or JSX) makes the default safe. This is one of
   the strongest arguments for not carrying the inline-HTML layer forward.

---

## SEC-2 — Production database has zero referential integrity (systemic, MEDIUM→HIGH at 2 clients)

Verified by introspection of production Postgres 18.6, 2026-09-12:

| Metric | Value |
|---|---|
| Tables (`public`) | 884 |
| Columns | 9,458 |
| Indexes | 2,045 |
| **FOREIGN KEY constraints** | **0** |

Every relationship — user→post, post→comment, conversation→message, order→line item —
exists only as convention inside query code. Today a single backend written by one team is
the only writer, which has contained the blast radius. Introducing a second client that
writes to the same tables materially raises the risk of orphaned and inconsistent rows,
and there is no database-level backstop.

This is a design decision to make explicitly in the database plan, not a defect to fix
reflexively. Adding 884 tables' worth of constraints is neither necessary nor safe; adding
them on the core social graph probably is.

---

## SEC-3 — Admin surface is the largest web surface in the repo

281 `/admin` routes — 43% of all non-API routes — of which 188 are inline-HTML pages using
neither shared shell. Authentication for at least part of it is a password in a query
string (`bot.py:28758`). The rebuild plan should treat admin as a first-class security
workstream, not a leftover phase.

---

## SEC-4 — Security alerts recorded a country the caller chose (confirmed live, MEDIUM) — FIXED

Verified by probe against production, 2026-09-13. A request to
`https://pulsesoc.com/api/mobile/auth/login` carrying `X-Country-Code: ZZ` was stored as `ZZ` in
`auth_events.country`; the control request with no such header stored `''`.

`request_country()` read any of four geo headers (`CF-IPCountry`, `X-Country-Code`,
`X-Appengine-Country`, `CloudFront-Viewer-Country`) straight off the request with no validation,
and `login_security_details()` feeds the result into admin security alerts as evidence of *where a
login came from*. Nothing in front of this deployment sets any of those headers, so the field was
never geolocation — it was a free-text field addressable by the caller, presented to an operator
as fact. Two further call sites wrote the header directly without going through
`request_country()` at all, which is how a fixed resolver can still leave the injection open.

Now gated on `PULSESOC_TRUSTED_GEO_HEADER` (`services/client_address.py`), **default unset**, and
validated to two alphabetic characters. Returning `''` is not a regression: `''` is what
production already produced for real visitors. Region and city had no trusted source at all and
now record nothing rather than recording a claim.

**Related, latent rather than live:** every per-IP control keyed on the *leftmost*
`X-Forwarded-For` element. The same probe proved Railway's edge replaces that header, so forged
values never reached the app and the vulnerability was not exploitable — correct by accident, with
nothing that would have noticed the accident ending. Detail and remedy in
`PULSESOC_WEB_SECURITY_MODEL.md` §6.1.

**Not fixed, documented in code:** `ANALYTICS_SALT` is unset in production, so every stored
`ip_hash` is `sha256` of the address under a constant that lives in this repository, and is
therefore reversible across the 2\*\*32 IPv4 space. `ip_hash` is a correlation key, so rotating the
salt orphans history rather than invalidating it; this needs a migration. §6.1.

---

## Cross-reference

Full context for these findings lives in:
- `PULSESOC_EXISTING_WEB_HTML_SHELL_INVENTORY.md` (the inline-HTML layer)
- `PULSESOC_DATABASE_INVENTORY.md` (schema and integrity)
- `PULSESOC_BACKEND_ROUTES_AND_AUTH.md` (auth, CSRF, admin gating)
