# Device Intelligence Provider Evaluation (Mission 4, Stages 19–20, 43)

## Decision summary

| Option | Decision | Why |
|---|---|---|
| Server-verified device-intel API (vendor request-id verification) | **PILOT** | No SDK, no client-side tracking, server controls every byte shared; abstraction already built (`DeviceIntelligenceAdapter`) |
| Fingerprint (client SDK) | **DEFER** | Requires a native SDK and app.json changes — forbidden this mission (Stage 43); revisit only with owner sign-off and a privacy review |
| MaxMind GeoIP2 (IP reputation/geo) | **DEFER** | Overlaps Cloudflare infrastructure-reputation contract already in place; add only if Cloudflare intel proves insufficient |
| Any provider requiring raw device fingerprinting of all users | **REJECT** | Violates "never query every visitor" (Stage 18) and the minimization gate; profiling is a disallowed purpose |

## Rationale

**No native tracking SDK** was added (Stage 43): no new native module, no
app.json/Info.plist change, no client-side collection. The only integration
shape accepted is **server-verified**: the client (if ever wired) would hand
the server an opaque vendor request-id; the server calls the vendor to verify
it. The vendor never gets user identifiers from us, and the client never gets
vendor credentials.

`DeviceIntelligenceAdapter` (in `external_contracts.py`) encodes the
abstraction: `verify(vendor_request_id)` → `DEVICE_PROVIDER_REF` observation,
confidence hard-capped at 0.7 (COMMERCIAL_INTELLIGENCE ceiling), verdict
SUSPICIOUS only when the vendor reports concrete signals. Without a
configured transport it returns NOT_CONFIGURED honestly — never a guess.

## Guardrails that survive any future adoption

- Kill switch `SENTINEL_DEVICE_INTEL_ENABLED`, default OFF.
- `requires_credentials=True`: no key → NOT_CONFIGURED, not FAILED.
- Purpose-gated to `DEVICE_INTEGRITY_CHECK`; profiling remains disallowed.
- Device evidence is external evidence: subject to the 0.6 external-only risk
  cap, corroboration rules, expiry, and the human-authority requirement.

## Revisit criteria

Adopt beyond PILOT only if: (a) internal Mission 3 device signals prove
insufficient for a real, recurring abuse pattern; (b) owner approves the
vendor contract and privacy addendum; (c) the integration remains
server-verified with no SDK.
