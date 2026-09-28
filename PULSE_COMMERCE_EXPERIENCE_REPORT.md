# PULSE COMMERCE EXPERIENCE — final report

Branch `pulse-commerce-experience`, worktree `/Users/hmcherie/Desktop/cpx-mktux`,
rebased onto `origin/main`. One commit.

12 files modified, 4 added (1,975 lines: the cart-schema module, its DB-backed
suite, the dock suite, and the mutation harness).

The chain the brief names — DISCOVER → BROWSE → PRODUCT CARD → PDP → VARIANT
SELECTION → ADD TO CART → CHECKOUT → ORDER — **closes**. It did not at the start:
the PDP resolved a single variant, printed its price, said "In stock", and offered
no way to buy it, because the cart table could not record which one the buyer had
picked. §4 is that fix and the largest single piece of this work.

---

## 1. Phase Zero — what was already there

The brief forbade starting with CSS, so the first pass was classification.

| Area | Finding |
|---|---|
| `marketplace_*` tables | **REAL.** Serves the live web marketplace. |
| `business_os_mkt_*` (canonical commerce) | **LIVE BUT EMPTY.** Every `BUSINESS_OS_*` flag is on in production and every table has zero rows. |
| `services/marketplace_web.py` | **REAL.** Owns *facts* — price derivation, badges, option groups, cart eligibility. Imports neither Flask nor `bot`. |
| `services/marketplace_storefront.py` | **REAL.** Owns *markup*. Pure dict→string. |
| Member PDP branch in `bot.py` | **PARTIAL** — rendered inline, not through the storefront. Now wired. |
| Anonymous PDP branch | **DUPLICATED.** Still a second implementation. See §5. |

The two commerce domains were left alone. Migrating the storefront onto the
empty canonical tables would have moved a working surface onto a dataset with
nothing in it.

## 2. What changed

**`bot.py`** — the member PDP now renders through
`marketplace_storefront.render_product(...)` rather than its own inline markup,
so the grid card and the product page can no longer disagree about a price, a
badge or a button. Added `marketplace_cart_table_exists(cur)` and used it to fix
the deadlock in §3.1.

**`services/marketplace_storefront.py`** — `render_product` gained the cart
affordance and the shared cart link; gallery thumbnails gained
`role="presentation"` on their `<li>` wrappers (§3.4). The buy panel renders the
add **disabled** while the picker is incomplete rather than omitting it (§4.2).

**`services/marketplace_cart_schema.py` (new)** — the canonical owner of
`marketplace_cart_items`: `variant_id`, the widened
`UNIQUE(user_id, listing_id, variant_id)`, and the per-engine retirement of the
old key. §4.2.

**`services/marketplace_cart_routes.py`** — `cart_add` takes and validates
`variant_id` and snapshots the *variant's* price; the read, the line state, the
order row and the Stripe metadata all carry it. §4.2.

**`services/marketplace_web.py`** — `cart_affordance` takes a resolved
`chosen_variant`, and `_accepted_choice` decides whether a selection is one. §4.2.

**`static/css/pulse_marketplace.css`** — gallery nav and thumbnail rail (§3.2,
§3.3).

**`static/css/pulse_home_os.css`** — **no longer changed by this branch.** See
§2.1.

### 2.1 main shipped the §7 dock fix while this branch was building it

I wrote the dock suppression into `pulse_home_os.css`: `@media (min-width:
1024px) { html body … .pulse-universal-dock { display: none !important } }`.
Rebasing onto `origin/main` revealed that `05327c34f` ("pulse emoji: one picker
for the whole web surface, and get the phone dock off the desktop", #93) had
landed **the same rule** — same selector, same floor, same `html`-prefixed
specificity bump, same display-only reasoning — and theirs also clears the 138px
of dead bottom padding that the dock's absence leaves behind.

So mine was dropped. `pulse_home_os.css` on this branch is now byte-identical to
`origin/main`, and the §7 fix is **main's, already deployed** — not this branch's.

What survives from my half is the **gate**, and it is not redundant:
`tests/web_surface/test_universal_dock_is_a_phone_control.py` reads every sheet
carrying a dock rule, which includes `pulse_home_os.css` — the sheet #93 edited.
main's own `test_desktop_shell_breakpoint_scope.py` only ever reads
`pulse_desktop_shell.css`, so the rule #93 added had no test on it.

That gate is what caught the duplication. Its
`assert checked == 1` — "exactly one width-scoped suppression is expected; two
would mean two floors to keep in agreement" — failed on the rebase with
`found 2`. Nothing else in either lineage would have noticed, and the branch was
green on both sides of the merge-base.

**`static/js/pulse_marketplace.js`** — add-to-cart wiring, cart count updates in
place; the variant resolver enables the add and writes the chosen id into
`data-mkt-variant`, which the POST then sends. It writes the same `0` sentinel the
server renders when nothing resolves, so the two sides never disagree about what
"nothing chosen" looks like, and it never re-enables a button the buyer has
already spent.

## 3. Defects found and fixed

### 3.1 The cart table nobody could create

`marketplace_cart_items` is created by `services/marketplace_cart_routes.py`
`_ensure_schema` and by **nothing else** — not by `init_db()`. So on a fresh
deployment the table is absent until the first `POST` to the cart API.

The product page reads a cart count for its header badge. A failed read answered
`None`, and `render_product` documents `None` as the caller withholding the
entire cart UI — Add to cart included.

Those two facts met in a self-sealing deadlock: **no button → no POST → no table
→ no button.** The public page's "Sign in to add to cart" promise led to a
member page offering no way to add to a cart, and the POST that button would
have made was the one thing that would have created the table.

Fixed by asking the engine's catalogue (`pg_class` / `sqlite_master`) and
answering `0` — a table that does not exist holds nothing for anybody. That is a
fact, not the guess `None` exists to refuse.

Deliberately **not** written with the repo's usual `_table_exists` fallback
(`SELECT 1 FROM {name} LIMIT 1`, e.g. `services/pulse_social_graph_service.py:93`).
That aborts the surrounding transaction on Postgres when the table is missing,
and this runs part-way through a page render with queries still pending — it
would have turned a missing badge into a 500.

Guarded by a test that drops the table itself, so it cannot be masked by a
sibling test's cart traffic creating the table first.

### 3.2 Gallery arrows ate the row

`static/css/pulse_mobile_system.css` gives every `button` on the site
`width: 100%` below 768px. Right for an action that stacks, wrong for a control
with siblings: on a 390px screen the two gallery arrows grew to **172px each**,
took the whole 370px row between them, and left the counter **11px** in which to
render "1 / 3" — so it wrapped onto three lines.

The global rule was **not** narrowed; pages outside the storefront lean on it,
and it is correct for the storefront's own stacked action list (verified — see
§6). The storefront opts its own arrows out instead. Two class names outrank a
bare `button`, so no `!important` was needed.

### 3.3 Thumbnail sizing that could never apply

`.mkt-gallery-thumb` declared `flex: 0 0 auto; width: 64px` on an `<a>` that was
neither a flex child nor a block box. The rail's flex items are the `<li>`
wrappers, so `flex` belonged to the parent; and `width` does not apply to an
inline element at all. Both declarations were inert.

The thumbs took their size from the image inside, and the anchor's border
painted on the *line box* — a stray vertical rule taller than the picture beside
every thumbnail. Fixed by sizing the `<li>` and making the anchor a block.

### 3.4 A tablist that did not own its tabs

The `<li>` between `role="tablist"` and `role="tab"` landed in the accessibility
tree as a generic element and broke the relationship, so thumbnails were
announced without their position in the set. Marked `role="presentation"`.

### 3.5 Test vocabulary pinned to markup that no longer exists

Three test files asserted the retired inline PDP's attribute names
(`data-add-to-cart`, `data-contact-seller`, `data-save-listing`,
`data-report-listing`). Worse, they matched *bare attribute names*, which the
page's own click handlers also contain — so they passed on a page carrying no
controls at all. Rewritten to require the attribute inside an element's opening
tag, and the endpoint assertion rewritten as the chain it now is: the page loads
the script, the script posts to the endpoint.

## 4. The structural gap — variant selection now reaches the cart

**This was the mission chain's terminus. It is closed.** The subsection below
keeps the diagnosis, because the shape of the defect is what justifies the shape
of the fix, and then records what landed.

### 4.1 What was wrong

`marketplace_cart_items` was **listing-grained, not variant-grained**:

```
user_id, listing_id, qty, price_snapshot_minor, currency, added_at, updated_at
UNIQUE(user_id, listing_id)
```

`cart_add` read only `listing_id` and `qty`. There was no column, no parameter
and no checkout field that could represent "size M in Snowflake Blue".

`marketplace_web.requires_variant_choice` therefore withheld the quick-add from
any listing offering a real choice, and `cart_affordance` returned
`CART_HIDDEN_NEEDS_CHOICE`. That refusal mirrored nothing downstream — it existed
precisely because the cart API *would* have accepted the add and recorded a line
naming no variant.

The consequence was visible on the page. On listing 1 the buyer picks COLOR:
Snowflake Blue and SIZE: M; the page resolved a single variant, printed a firm
**$95.33** and **In stock** — and offered no Add to cart:

| Rendering | `data-mkt-add`, before | after |
|---|---|---|
| `member_pdp` (options unchosen) | 0 | 1, **disabled** |
| `member_pdp_chosen` (both options chosen) | **0** | **1, enabled** |
| `member_pdp_simple` (no options) | 1 | 1 |

`requires_variant_choice` asked about the *listing*, never about the buyer's
*selection*. That is exactly right for a grid card, which has no selection state.
On the PDP — where the picker **is** the page — it meant DISCOVER → BROWSE → PDP
→ VARIANT SELECTION → **ADD TO CART** stopped one step short for every
multi-variant listing.

### 4.2 What landed, in the order the gap required

The ordering constraint was load-bearing and was honoured: **step 4 alone would
have produced exactly the mis-recorded cart line the old check existed to
prevent**, so the gate was opened last.

1. **`services/marketplace_cart_schema.py` (new, 382 lines)** — one canonical
   owner for the table's DDL, modelled on `marketplace_reservation_schema.py`:
   structured `{status, ...}` result, `STATUS_READY/MISSING/ERROR`, never raises.
   It adds `variant_id INTEGER NOT NULL DEFAULT 0` and replaces the key with
   `UNIQUE(user_id, listing_id, variant_id)`.

   Three things about it are not incidental:
   - The column is added through a defensively-applied `ALTER TABLE`, not by
     editing a `CREATE TABLE`. There is no migration framework here, and an
     edited `CREATE TABLE` reaches only fresh databases — production would have
     silently skipped it.
   - `variant_id` is `NOT NULL DEFAULT 0` rather than nullable. **NULLs are
     distinct in a unique index on both engines**, so a nullable column would let
     the same listing be added to the same cart without limit — reintroducing the
     duplicate-line bug the old key existed to prevent, via the change meant to
     preserve it. `NO_VARIANT = 0` is the sentinel, and it is a real value in the
     key.
   - Retiring the old key differs by engine and both paths are implemented:
     Postgres drops the constraint or index by name; SQLite, which cannot drop a
     table-level `UNIQUE`, rebuilds the table inside one transaction — **copying
     row ids explicitly**, because `cart_update`, `cart_remove` and the checkout
     cart-clear all address lines by `id`, and renumbering them would invalidate
     every line id a client is holding.

2. **`cart_add` takes and validates `variant_id`** — the price snapshot comes off
   the *variant* row, not the listing, so a Medium and a Large are held at their
   own prices. Two new refusals: `VARIANT_REQUIRED` (400) for a bare `listing_id`
   on a listing that needs a choice, and `VARIANT_UNAVAILABLE` (409) for a variant
   that is archived or belongs to a different listing. The variant lookup is
   scoped to the listing, so a caller cannot borrow another seller's row.
   `VARIANT_REQUIRED` is what makes opening the client-side gate safe: a stale
   cached page or an older app build posts a bare `listing_id` and is refused
   rather than booked.

3. **The variant is carried through the read, the line state and checkout** —
   each line names its `variant_id`, `variant_key`, its options and a
   human-readable `variant_label` (`"Color: Snowflake Blue · Size: M"`). Line
   state reads the *variant's* stock, falling back to the listing's; a retired
   variant reports `removed` with the listing surviving, a sold-out one reports
   `sold`. The order row records `variant_label` **and** the ids, because packing
   a parcel reads the label and reconciling a return reads the id. Stripe
   metadata carries `variant_ids` positionally with `0` sentinels so it pairs
   against `listing_ids`.

4. **The gate opens on the selection, not on the listing** — `render_product`
   passes the resolved single variant to `cart_affordance` as `chosen_variant`;
   the grid, which has no picker, passes nothing and is still refused.

   The one judgement call worth naming: while the picker is incomplete the button
   is rendered **disabled, not omitted**. Omitting it leaves the scripted page
   with no element for the resolver to enable, so the buyer could never reach an
   add at all. The disabled `.mkt-cta` is painted grey by the stylesheet, so the
   page carries no mint gradient until the picker resolves — which is the honest
   state, and the one that makes the button lighting up mean something. *Message
   seller* demotes to a ghost behind it, because the buyer is one radio away from
   buying and promoting *Message seller* would tell them the opposite.

A resolved choice is not automatically an acceptable one. `_accepted_choice`
refuses three ways: no variant resolved (an unfinished picker), a variant that is
not **available** (offering Add to cart on the one combination that is sold out
reads as though the page checked), and a variant that is not one of the rows the
decision was made against (two reads disagreeing about what is for sale withhold
the button). The route re-derives all of it; none of this is a trust boundary.

### 4.3 Proof

`tests/test_marketplace_cart_variants.py` (new, 937 lines, 41 tests, own process,
registered in `config/ci_test_manifest.json`) proves the *effect* against a real
database, in four sections: schema migration, add validation, read states, order
and Stripe metadata. The schema section runs against hermetic scratch SQLite
files with the **legacy DDL reproduced in the test rather than imported** — a
constant in the schema module would be a copy that moved with the fix and stopped
representing what is actually in production.

`tests/test_storefront_add_to_cart.py` holds the *contract* (110 tests). Five of
its tests pinned the retired premise and were **inverted rather than deleted** —
one of them said so in its own docstring — and seven were added, including
negative controls for every positive claim: choosing the *other* size names the
other row, a stale `?opt_size=XXL` leaves the add disabled, the sold-out size
does not unlock it while the in-stock size on that same listing does.

`scripts/protection/cart_variant_mutation_matrix.py` (new) applies 19 inverse
edits across all four modules and asserts the tree is restored byte-for-byte
after each. **All 19 are killed.** Its docstring names one control that is
deliberately *absent*: the `UPDATE ... SET variant_id=0 WHERE variant_id IS NULL`
backfill cannot be killed, because `ADD COLUMN ... DEFAULT 0` already fills
existing rows on both engines. Listing it would produce a permanent SURVIVED and
train a reader to ignore the output.

Suites green after the change: 41 + 110 + 11 + 102 + 58 + 14 + 6 + 53 + 14 + 9.

## 5. Deliberate non-changes

- **The 901–1023px band** renders both the dock and the shell's `.nav` strip.
  Pre-existing. A 901px floor risks a band with no navigation at all, which
  `test_shell_nav_parity` warns about in as many words.
- **The anonymous PDP** is not wired to the storefront's `public_document`. Two
  implementations of one indexable page is how a feed and a page begin
  disagreeing about a price — but rewiring it is an SEO-visible change to the
  only indexable commerce surface and wanted its own pass.
- **Pricing divergence**: the public page prices from `price_label`, the member
  storefront from `variants.price_cents`. Same listing, two sources. Should
  converge on the storefront's derivation when the item above is done.
- **The shell's global `button { width: 100% }`** below 768px — left intact.
  See §3.2.
- **The cart pill beside the `<h1>`** reads as detached on desktop, where the
  left rail already has a Cart item. Kept anyway: it is the only cart affordance
  carrying a live count, and it is the one the add-to-cart flow updates in
  place. The rail item has no badge.

## 6. Visual verification (§133)

Rendered through a local harness (test client → static files, iframe at fixed
width so CSS media queries evaluate against a real narrow box — `resize_window`
would not produce one).

| Surface | Width | Result |
|---|---|---|
| Grid (Reference A) | 1440 | Desktop shell + left rail, card grid, per-listing **Add to cart** / **Choose options**. Dock `display:none`. |
| Grid | 390 | Dock present, rail gone, two-column grid. |
| PDP (Reference B) | 1412 | Breadcrumb, gallery, buy panel, variant pickers, seller card, delivery & payment, app-promotion CTA. |
| PDP, options chosen | 1412 | COLOR/SIZE mint-selected, **In stock**, firm price — no Add to cart (§4). |
| PDP | 390 | Square arrows, single-line counter, 64px thumbs — post-fix. |
| PDP, single-variant | 390 | **Add to cart** the sole filled CTA; Message seller / Save / Report as ghosts. |

19 of 19 grid images loaded, 0 broken.

**Harness artifact, not a product defect:** an eager, async-decoded hero can
screenshot dark if captured before decode completes. Confirmed benign via
`naturalWidth` (900×1200), `elementFromPoint` (the `<img>` is topmost, opacity
1) and a re-shoot that shows the picture.

One filled CTA per surface throughout — `contact_class` falls back to
`mkt-ghost` whenever a buy action is present.

## 7. No fabricated data

Nothing renders that cannot be resolved from a real column. No invented ratings,
sold counts, compare-at prices, delivery estimates, stock numbers, seller
metrics or recommendations. Absences are absences: unknown stock renders no
line rather than guessing either answer; an unparseable price label renders no
price rather than zero; the thumbnail rail shows the media that exists and
disappears below two items rather than padding to the reference's seven.

## 8. Testing

All files pass in their own pytest process (marketplace tests set module-scope
DB state at import and cannot share one).

| File | Result |
|---|---|
| `test_marketplace_cart_variants.py` | 41 passed (**new**, §4.3) |
| `test_marketplace_storefront.py` | 102 passed (3 new) |
| `test_storefront_add_to_cart.py` | 110 passed (5 inverted, 7 new) |
| `test_marketplace_public_pages.py` | 58 passed, 9 subtests |
| `test_web_marketplace_cart.py` | 11 passed |
| `test_web_cart_checkout.py` | 53 passed |
| `web_parity/test_marketplace_listing_links.py` | 14 passed |
| `test_marketplace_seo.py` | 38 passed, 20 subtests |
| `test_marketplace_listing_detail.py` | 8 passed, 3 subtests |
| `web_surface/test_universal_dock_is_a_phone_control.py` | 6 passed, 2 skipped (new) |

Protection gates: `test_every_test_file_is_run_by_ci.py` 9 passed ·
`protection/test_environment_contract.py` 14 passed ·
`protection/test_route_auth.py` 12 passed.

Both new test files are registered in `config/ci_test_manifest.json` — that gate
is default-deny.

### Mutation proofs

Every new assertion was proved capable of failing.

The gallery and dock fixes: four mutations, each failing only its own test; both
source files restored and verified byte-identical with `shasum -c`.

The variant path: `scripts/protection/cart_variant_mutation_matrix.py`, 19
mutations across `marketplace_cart_schema.py`, `marketplace_cart_routes.py`,
`marketplace_web.py` and `marketplace_storefront.py`. **All 19 killed.** The
harness asserts the restore after each edit rather than trusting it, and it
refuses to run an ambiguous anchor — a mutation whose target text appears more
than once reports `AMBIGUOUS` instead of silently editing the wrong call site.
That check earned its keep: `if variant and not _variant_available(variant):`
appears twice in the routes module, once in `cart_add` and once in `_line_state`,
so both of those mutations carry multi-line context anchors.

Three of the 19 survived the first run, and none of the three was a harness bug:

| Survivor | What it meant | Fix |
|---|---|---|
| `sqlite-rebuild-renumbers-the-line-ids` | `DRIFTED` — the anchor had been reformatted across lines | re-pointed the anchor |
| `sold-out-choice-still-offers-the-button` | nothing asserted that picking the sold-out size leaves the add disabled | two tests, one fixture: the sold-out size stays disabled, the in-stock size on that same listing does not |
| `unresolvable-choice-accepted-anyway` | the `known` consistency check is unreachable through `render_product`, which resolves the choice *from* `variants` | asserted against `cart_affordance` directly, with a control |

The last one is the interesting one. A survivor is a claim nothing observes, and
the honest response is a test rather than a shrug — but it is also evidence about
*where* the test belongs. That check cannot be reached through the page, so
testing it through the page was never going to work; it is a contract between two
reads and it is now pinned at that seam.

| Mutation | Result |
|---|---|
| Remove `.mkt-gallery-nav .mkt-ghost` | arrow test failed |
| Remove the `<li>` rule and restore inert `flex` on the anchor | thumb test failed |
| Keep the `<li>` rule, let the anchor fall back to inline | thumb test failed |
| Drop `role="presentation"` | tablist test failed |

The arrow test also asserts its own *premise* — that the shell really does still
stretch bare buttons below 768px. If that rule is ever removed the override
becomes dead weight, and whoever removes it is the one told about it, rather
than the test passing on forever defending a phantom.

## 9. Definition of Done — status

**Done**
- One canonical rendering path for grid card and product page; no shadow copy.
- Duplicate desktop/mobile navigation removed (§7 of the brief); dock is a phone
  control, verified in-browser at both breakpoints. **The rule is main's, from
  #93, not this branch's** — see §2.1. What this branch adds is the test that
  holds it, on the sheet main's own test does not read.
- Cart affordance present, wired, and correct on first use of a deployment.
- Canonical design tokens; mint CTA; one filled CTA per surface.
- No fabricated data anywhere on the surface.
- Gallery correct and accessible at phone and desktop widths.
- Regression tests for every fix, each mutation-proved.
- Existing commerce functionality preserved — nothing removed, no route deleted.
- **The full chain closes**: DISCOVER → BROWSE → PRODUCT CARD → PDP → VARIANT
  SELECTION → ADD TO CART → CHECKOUT → ORDER. A chosen size and colour now reaches
  the cart line, the order row and the Stripe metadata under its own id and its
  own price — §4.

**Not done, and why**
- **Anonymous PDP on the shared storefront** — §5. SEO-visible; wants its own pass.
- **Public/member pricing convergence** — §5, blocked on the item above.
- **The 901–1023px navigation band** — §5, pre-existing shell behaviour.
- **Device QA** — static checks and headless rendering do not replace it for
  checkout and uploads.

---

### Next decision for you

Committed on `pulse-commerce-experience`, rebased onto `origin/main`.

§4 is closed, so the chain has no dead end left in it. Two things I would want
before calling this shippable, and neither is something I can do from here:

1. **Device QA on the variant path.** The schema change runs against SQLite in
   tests and against PostgreSQL in production, and the two retire the old unique
   key by *different* code paths — Postgres drops the constraint, SQLite rebuilds
   the table. The Postgres path is the one production will take and the one no
   test in this repo exercises. Worth a throwaway Postgres before it meets a real
   cart. Then: pick a size on a real device, add it, check out.
2. **The remaining §5 items** — the anonymous PDP and the pricing convergence
   behind it, both SEO-visible and both wanting their own pass rather than being
   tacked onto this one.
