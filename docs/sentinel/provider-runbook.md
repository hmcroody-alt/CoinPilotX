# Sentinel Provider Runbook

1. Put a provider credential directly into its named Railway production
   service; never echo it or commit it.
2. Set the non-secret provider scope in that same service.
3. Verify the credential is read-only and narrow.
4. Enable the provider switch, then the master switch.
5. Verify a measured provider-health record; configured is not healthy.
6. Disable only the failing provider if it degrades—never unrelated workers or
   verified Stripe webhook processing.

Initial harmless checks: refresh CISA KEV; query a known public OSV/NVD
package/CVE; read GitHub alert metadata only; use a benign Cloudflare indicator
only after the exact permission is verified; read an existing Sentry issue; and
inspect an existing test-mode Stripe event without creating a charge.

Cloudforce One, Datadog, Fingerprint, MaxMind, VirusTotal, PagerDuty, and Okta
have no adapter, variable, dependency, or scheduled work in this release.
