# Legal Name Correction Audit

## Summary

Corrected the exact legal company display name from `CoinPilotXAI Inc.` to `CoinPlotXAI Inc.` everywhere it was safe to change in repo text files. This was intentionally not a broad product/repository rename. Bare `CoinPilotXAI`, domains, cache keys, service identifiers, package names, routes, and provider object identifiers were left untouched unless they were part of the exact legal company phrase.

## Safe Corrections Applied

- Corrected visible/legal text in Flask templates, privacy/terms/support/account/search/SEO pages, app metadata docs, email/service copy, SEO content, public LLM text, and user-facing report/docs.
- Corrected SVG embedded display text without renaming the SVG file.
- Corrected Brevo default folder display text in source. This changes only the display folder name used by the app code; it does not rename an external Brevo account or provider object.
- Did not rename `CoinPilotX`, PulseSoc branding, repository names, domains, service names, route names, package identifiers, bundle IDs, Stripe IDs, Firebase IDs, or deployment resources.

## Files Corrected

- `bot.py`
- `static/llms.txt`
- `templates/index.html`
- `templates/support.html`
- `templates/terms.html`
- `templates/account.html`
- `templates/search.html`
- `templates/seo_page.html`
- `templates/privacy.html`
- `seo/__init__.py`
- `seo/content.py`
- `seo/schema.py`
- `services/notification_service.py`
- `services/intelligence.py`
- `services/portfolio_service.py`
- `services/brevo_contacts.py`
- `services/email_service.py`
- `reports/pulsesoc_full_branding_audit.md`
- `reports/traffic-growth-recommendations.md`
- `reports/indexing-readiness-report.md`
- `reports/lighthouse-report.md`
- `reports/pulse_app_store_connect_setup.md`
- `reports/pulsesoc_text_replacement_changes.md`
- `reports/pulsesoc_missed_branding_audit.md`
- `reports/seo-report.md`
- `reports/saas-readiness-report.md`
- `reports/pulsesoc_final_branding_cleanup.md`
- `reports/scale-readiness-report.md`
- `reports/brevo_notifications_setup.md`
- `static/assets/coinpilotxai-share-card.svg`
- `mobile/pulse-react-native/store.config.json`
- `mobile/pulse-react-native/store-metadata/en-US/app-store.md`
- `mobile/pulse-react-native/store-metadata/en-US/play-store.md`

## Post-Correction Scan

- Exact old legal phrase remaining in scanned repo text: `0`
- Remaining bare/technical old-name references requiring review: `362`

Remaining references are intentionally documented in `reports/legal_name_risky_items_pending_approval.md` because changing them could be a product rename, service rename, identifier migration, or external-platform operation.

## Validation Plan

Validation performed after the correction:

- Python compile checks for changed Python files.
- Legal-name audit script to verify no exact old legal phrase remains in safe scanned files.
- Template/legal metadata checks for `CoinPlotXAI Inc.`.
- `git diff --check`.

## Notes

This correction applies the legal name `CoinPlotXAI Inc.` safely and preserves operational stability. External provider display/legal names are audited separately in `reports/external_platform_legal_name_audit.md`; no external provider resource was renamed from code.
