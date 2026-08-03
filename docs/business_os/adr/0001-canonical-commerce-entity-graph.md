# ADR-0001 — Canonical commerce entity graph

Status: Accepted
Owner: Commerce data model
Date: 2026-08-01
Supersedes: the looser entity language in the Store, Marketplace and Orders
section missions

## Context

Nothing in the section missions ever wrote down the difference between a
product, a thing for sale in a seller's own store, and a thing for sale in the
marketplace. Each surface therefore invented its own reading, and the readings
do not agree.

`api/storeDashboard.ts` operates on a `StoreListingRow` with a `health` field
whose values are `in_stock`, `low_stock` and `out_of_stock` — an inventory
vocabulary, describing a thing the seller stocks. The marketplace surface
operates on `MarketplaceListing`, which is a posting: it has a location, a
recency, and a lifecycle that ends when it sells rather than when stock runs
out. Orders reference a third thing again, the line item that was actually
purchased, whose identity has to survive the listing being edited or withdrawn.

These are treated as near-synonyms in the code and in the copy, which is why
`deriveStatus` has to guess whether a seller with zero rows is open or empty,
why the marketplace can advertise proximity for a listing that has no location,
and why joining a conversation to "the thing it is about" has no obvious foreign
key to use.

The deep review's first architectural recommendation is to name these three
things separately and once. It is right, and the cost of not doing it grows with
every surface added.

## Decision

Three entities, distinct, with explicit relationships.

**Product** is the seller's catalogue item — the thing that exists in their
business whether or not it is currently for sale anywhere. It owns the durable
facts: identity, title, description, media, variants, cost, and inventory. A
product is not visible to buyers. Deleting a product is a business event, not a
merchandising one.

**Store listing** is a product offered through the seller's own storefront. It
owns storefront-specific merchandising: price in the store's currency,
availability, position, and the store-scoped status the readiness ladder in
ADR-0003 describes. A store listing references exactly one product. A product
may have zero or one store listings.

**Marketplace listing** is a product offered into the shared marketplace. It
owns posting-specific facts: marketplace price, location, posting time, category,
condition, and the posting lifecycle. A marketplace listing references exactly
one product. A product may have many marketplace listings over time, because
relisting is a normal seller behaviour and each posting has its own age and its
own audience.

**Order line items reference the product and snapshot the listing.** An order
records which product was bought, through which listing, and captures the price,
title and terms as they stood at purchase. Editing or withdrawing a listing must
never change what an order says was bought — that is a financial record, and it
is governed by the Payments and Orders missions, which this ADR does not relax.

The inventory relationship is that a product holds stock and its listings draw
from it. Two marketplace listings and a store listing over the same product draw
from one pool. This is the fact that makes "out of stock" meaningful and makes
"open" derivable rather than guessed.

## Consequences

`deriveStatus` stops guessing. A seller with a store but no store listings is
*empty*, distinguishable from a seller whose listings are all out of stock,
which is what ADR-0003's readiness ladder needs.

Proximity claims become checkable. "Just listed near you" is only sayable when
the marketplace listing has a location and the viewer has one; store listings do
not have locations at all, so the question does not arise for them.

Conversations gain something to point at. ADR-0004's entitlement checks and the
commerce inbox's `offer_id` / `order_id` / `listing_id` join both need a stable
target, and a marketplace listing that survives being relisted is not it — the
product is.

Migration is not free. Existing rows conflate the three, and separating them
requires deciding, for each existing listing, what product it implies. The
likely path is to synthesise one product per existing listing and then
de-duplicate, which will not be perfect and should be treated as a data-quality
project with its own reconciliation job under ADR-0007 rather than as a
one-shot migration.

## Open question

Whether a product may have more than one *concurrent* marketplace listing, or
only more than one over time. Allowing concurrency supports listing the same
item in two categories; forbidding it keeps the inventory draw unambiguous.
Recorded here rather than decided, because the answer is a product question
about how sellers actually relist.
