# Pulse SMS Readiness

Date: 2026-06-06

## Current State

The application has an SMS alert foundation through the notification service, but production SMS should remain disabled until account capabilities, sender rules, pricing, opt-in handling, and country support are confirmed.

## Brevo SMS Readiness Checklist

- Confirm Brevo SMS is enabled for the account.
- Confirm SMS credits/pricing before any send test.
- Confirm sender requirements for target countries.
- Confirm opt-in and unsubscribe requirements.
- Confirm supported countries for the initial Pulse user base.
- Confirm whether transactional SMS is available separately from marketing SMS.

## Do Not Do Yet

- Do not purchase credits without approval.
- Do not enable broad SMS delivery before preference and consent checks are verified.
- Do not send SMS containing secrets, tokens, or private account data.

## Recommended SMS Categories

- Security alerts only at first.
- Premium/payment alerts after billing language is reviewed.
- Message digests only after explicit user opt-in.

## Status

External Brevo SMS account verification remains pending because account-browser access was not available from this turn.
