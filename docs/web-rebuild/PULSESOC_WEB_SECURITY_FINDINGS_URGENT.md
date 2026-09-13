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

## Cross-reference

Full context for these findings lives in:
- `PULSESOC_EXISTING_WEB_HTML_SHELL_INVENTORY.md` (the inline-HTML layer)
- `PULSESOC_DATABASE_INVENTORY.md` (schema and integrity)
- `PULSESOC_BACKEND_ROUTES_AND_AUTH.md` (auth, CSRF, admin gating)
