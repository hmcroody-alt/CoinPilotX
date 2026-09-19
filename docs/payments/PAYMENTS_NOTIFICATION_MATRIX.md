# Payments — Notification Matrix

Every money event PulseSoc sends, who receives it, on which channels, and what
stops it being sent twice.

The authority for this table is `services/payments_notifications.py:SPECS`. It
is not a description of the code; the code is generated from the same table,
and `tests/marketplace/test_payments_notifications.py` fails if the two drift.

## 1. The delivery path

There is one. A money event calls `payments_notifications.emit`, which calls
`pulsesoc_notification_system.intake_event` — the same engine every other
PulseSoc notification uses. No second engine was built, no second retry
mechanism exists, and no second dedupe table was added.

```
money event  ->  payments_notifications.emit
                 ->  intake_event          (notifications + notification_events)
                     ->  in_app delivery job
                     ->  email delivery job  ->  _dispatch_email
                     |                            -> payments_notifications.render_email
                     |                               -> payments_email_templates.render
                     ->  push delivery job
```

`_dispatch_email` asks `render_email` for a payment template before falling
back to the engine's generic email. A notification that carries no
`email_template` is untouched, so every other subsystem's email is unaffected.

## 2. Why the email is rendered at send time

The notification's `metadata_json` is persisted and mirrored to
`pulse_notifications`, which is client-visible. Storing rendered HTML there
would push an 8KB document into the notification feed of every device that
syncs. The metadata therefore carries a template key and a small context, and
the HTML is built in the email dispatcher.

## 3. The matrix

| Event | Recipient | In-app | Email | Push | CTA | Severity | Dedupe key |
|---|---|---|---|---|---|---|---|
| `seller_application_received` | seller | yes | yes | yes | Apply | normal | `event:user:application_id` |
| `seller_information_requested` | seller | yes | yes | yes | Apply | high | `event:user:application_id:reviewer_message` |
| `seller_approved` | seller | yes | yes | yes | Payouts | high | `event:user:application_id` |
| `seller_declined` | seller | yes | yes | yes | Apply | high | `event:user:application_id:reviewer_message` |
| `stripe_verification_required` | seller | yes | yes | yes | Payouts | high | `event:user:requirements` |
| `stripe_account_ready` | seller | yes | yes | yes | Payouts | high | `event:user` |
| `card_payments_enabled` | seller | yes | yes | yes | Payouts | high | `event:user` |
| `seller_account_restricted` | seller | yes | yes | yes | Payouts | **urgent** | `event:user:disabled_reason` |
| `new_paid_order` | seller | *existing* | yes | *existing* | Seller orders | high | `event:user:order_id` |
| `payment_succeeded` | buyer | *existing* | yes | *existing* | Orders | high | `event:user:order_id` |
| `order_shipped` | buyer | — | — | — | Orders | normal | `event:user:order_id:tracking_reference` |
| `payout_paid` | seller | yes | yes | yes | Payouts | high | `event:user:payout_id` |
| `payout_failed` | seller | yes | yes | yes | Payouts | **urgent** | `event:user:payout_id` |
| `refund_completed` | buyer | yes | yes | yes | Orders | high | `event:user:order_id:amount_cents` |
| `dispute_opened` | seller | yes | yes | yes | Seller orders | high | `event:user:dispute_id` |
| `dispute_action_required` | seller | yes | yes | yes | Seller orders | **urgent** | `event:user:dispute_id:evidence_due_by` |

*existing* means the in-app row and the push were already being written by
`pulse_emit_payment_checkout_event` and `notify_user` before this layer
existed. Those two events are emitted **email only**
(`emit(..., email_only=True)`), which also sets `skip_pulse_legacy_mirror`, so
the buyer does not see one order twice in one feed. Email was the channel
neither legacy path had.

SMS is never a channel for a payment event. `CHANNELS` is
`("in_app", "email", "push")` and a test pins it.

## 4. Where each event is raised

| Event | Trigger | Location |
|---|---|---|
| `seller_application_received` | seller submits or resubmits | `api_pulse_seller_application_submit`, `admin_seller_application_action` |
| `seller_information_requested` / `seller_approved` / `seller_declined` | reviewer decision | `admin_seller_application_action` |
| `stripe_verification_required` / `stripe_account_ready` / `card_payments_enabled` / `seller_account_restricted` | `account.updated` | Stripe webhook |
| `new_paid_order` / `payment_succeeded` | `payment_intent.succeeded` | Stripe webhook, both the cart and single-transaction branches |
| `payout_paid` / `payout_failed` | `payout.paid`, `payout.failed` | Stripe webhook |
| `refund_completed` | `charge.refunded` | Stripe webhook, via the allocator's per-order results |
| `dispute_opened` | `charge.dispute.created` | Stripe webhook, **after** the settlement hold lands |
| `dispute_action_required` | `charge.dispute.updated` with `needs_response` and a deadline | Stripe webhook |
| `order_shipped` | **nothing raises this** | see §7 |

## 5. Idempotency

Three independent layers, in the order a redelivered Stripe event meets them:

1. `stripe_events.stripe_event_id` is `UNIQUE`. A replayed event id is recorded
   and not reprocessed.
2. `notification_events.event_key` is `UNIQUE`, built from the dedupe key in
   the table above. Two emits with the same key produce one notification and
   the second returns `deduped: True`.
3. Delivery jobs carry a sha256 dedupe key, so a retried job does not send a
   second copy of an email that already went out.

Two dedupe choices are deliberate and are worth knowing before changing them:

- `stripe_account_ready` and `card_payments_enabled` dedupe on the **seller
  alone**, with no context field. Stripe sends `account.updated` constantly —
  on every requirement change, every balance change, every capability
  transition. Keying these on anything that varies would email a verified
  seller "you're verified" on a loop.
- `stripe_verification_required` dedupes on `requirements`. This is the
  inverse case: a *second, different* requirement must reach the seller, or
  they sit waiting for a payout that a field they were never told about is
  blocking.

`emit` never raises. A notification that fails to send must not fail the
webhook, because Stripe would then redeliver an event whose database effects
have already been applied.

## 6. What may never reach a notification

`SAFE_CONTEXT_KEYS` is an allowlist. `safe_context` drops every key not on it
and coerces what survives to JSON scalars. This is structural, not a
convention: a future caller cannot pipe a card number, a CVV, an IBAN, a Stripe
secret key, a KYC document filename, a verification code or a password into a
notification row, because the key is not on the list and the value is dropped
before the payload is built.

`test_forbidden_material_cannot_reach_a_notification` asserts each of those
seven by searching the serialized payload.

The allowlist is also checked *against the templates*: every field any template
reads must be on it. That test exists because the two had already drifted —
twelve fields including `arrival_date`, `payout_reference` and
`tracking_reference` were read by templates and stripped before rendering, so
they would have arrived blank in emails about a seller's money.

## 7. The one unwired event

`order_shipped` has a template, a spec and a dedupe key, and nothing raises it.

No route in the codebase writes a shipped status. `"shipped"` appears only as a
*read-side* status group in `bot.py:97530`, derived from
`seller_transactions.status`, and no writer ever sets that value.
`mark_delivered` likewise has no caller.

This is the same gap recorded as owner action #4 in
`STRIPE_CONNECT_IMPLEMENTATION_REPORT.md`: the delivery-confirmation mechanism
is undecided, so no settlement can leave `pending_fulfillment`. Inventing a
trigger here would mean inventing a fulfillment model. The template is left in
place so that the decision, when it is made, needs a call site and nothing
else.

## 8. Known edge

`_rules_check` falls back to `["in_app"]` when a recipient's preferences leave
no channel allowed. An `email_only` event sent to a seller who has turned off
payment emails therefore becomes a row in the engine's own feed rather than
nothing. It does not reach the legacy feed, because `skip_pulse_legacy_mirror`
is set, so it cannot duplicate the row `notify_user` already wrote.

This was left alone rather than special-cased: the fallback is shared by every
subsystem on the engine, and changing it to serve one caller would change
delivery behaviour for twenty others.
