# Pulse Default Handle Branding

## Change Summary

Generated public fallback handles now use Pulse branding:

- New default public handle: `Pulse-####`
- Previous generated shape: `Pilot-####`

Existing custom usernames and stored public IDs are not overwritten. Existing `pilot-` lookup compatibility remains so old links and identifiers can still resolve safely.

## Updated Areas

- Pulse profile fallback handles.
- Pulse user search fallback display.
- Chat realtime public profile fallback.
- Roast/Arena fallback participant IDs.
- Repost attribution fallback handles.

## Guardrail

Added `scripts/pulse_default_handle_branding_audit.py` to ensure new generated handles use Pulse branding while retaining old `pilot-` compatibility in lookup paths.
