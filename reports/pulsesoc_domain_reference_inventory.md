# PulseSoc Domain Reference Inventory

Date: 2026-06-06

## Primary Public Domain

- https://pulsesoc.com is now the preferred public canonical domain in public metadata and user-facing links.
- https://www.pulsesoc.com remains supported as a Railway-attached custom domain and should not be force-redirected until approved.

## coinpilotx.app References Kept

The following categories remain intentionally:

- APP_BASE_URL example fallback for existing production compatibility.
- cdn.coinpilotx.app storage/CDN references.
- old-domain host compatibility checks.
- scam-shield allowlist entry for the existing domain.
- audit fixtures and diagnostics that validate old-domain/CDN behavior.
- historical docs and generated reports.

## Old Email References

- No active source references to support@coinpilotx.app, security@coinpilotx.app, or noreply@coinpilotx.app were found after the migration pass.

## PulseSoc Email References

- support@pulsesoc.com
- security@pulsesoc.com
- noreply@pulsesoc.com

## Deferred Domain Work

- Do not remove coinpilotx.app from allowed hosts, old-domain logic, or production compatibility until a separate redirect/cutover plan is approved.
- Do not migrate cdn.coinpilotx.app without a CDN/storage plan and media audit.
- Do not change auth callback behavior without explicit approval.
