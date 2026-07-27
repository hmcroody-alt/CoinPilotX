# Safe Cleanup Inventory

## Status UI V4 Commit

- Committed: `c3e22b63` (`Refine PulseSoc Status UI V4`)
- Pushed: `origin/main`
- Scope: isolated Status UI V4 files plus a generated partial staged patch for only the Status-related hunks in `bot.py`

## Cleanup Decision

No files were deleted or reset.

The worktree contains many modified source files, scripts, tracked reports, and untracked screenshots/reports. None were safe enough to remove without risking active user work or QA evidence. Cleanup is therefore limited to inventory and preservation.

## 1. Safe Generated Artifacts

No artifact was classified as safe to delete automatically.

Several tracked JSON/Markdown reports look generated, but because they are tracked and modified, they were preserved:

- `reports/feed_post_v3_audit.json`
- `reports/postgres_compatibility_audit.json`
- `reports/pulsesoc_completion_audit.json`
- `reports/pulsesoc_home_feature_inventory.json`
- `reports/push_notification_audit.json`
- `reports/reel_feed_audio_status_nav_audit.json`

## 2. Old Screenshots / Temporary Images

These untracked files look like QA screenshots or captured evidence. They may be cleanup candidates, but they were preserved because they could be useful App Store, verification, or design QA evidence:

- `reports/screenshots/ScreenRecording_06-19-2026 21-09-33_1.mov.png`
- `reports/screenshots/app-review-recording-02s.png`
- `reports/screenshots/app-review-recording-08s.png`
- `reports/screenshots/app-review-recording-15s.png`
- `reports/screenshots/app-review-recording-24s.png`
- `reports/screenshots/app-review-recording-30s.png`
- `reports/screenshots/app-review-recording-32s.png`
- `reports/screenshots/app-review-recording-34s.png`
- `reports/screenshots/app-review-recording-36s.png`
- `reports/screenshots/app-review-recording-38s.png`
- `reports/screenshots/app-review-recording-40s.png`
- `reports/screenshots/app-review-recording-44s.png`
- `reports/screenshots/app-review-recording-46s.png`
- `reports/screenshots/app-review-recording-48s.png`
- `reports/screenshots/app-review-recording-50s.png`
- `reports/screenshots/app-review-recording-52s.png`
- `reports/screenshots/app-review-recording-54s.png`
- `reports/screenshots/app-store-resolution-reply-2026-06-26.png`

Additional screenshot files exist under `reports/screenshots/` but were not shown as dirty because they are already tracked or unchanged.

## 3. Duplicate Reports

The following report files appear related to older audit/branding/legal/payment work. They were preserved because they may be intentional deliverables:

- `reports/alerts-worker-railway.md`
- `reports/backend-admin-upgrade-report.md`
- `reports/external_platform_legal_name_audit.md`
- `reports/full-platform-review.md`
- `reports/human-psychology-ux-audit.md`
- `reports/legal_name_correction_audit.md`
- `reports/legal_name_risky_items_pending_approval.md`
- `reports/media_engine_build_stuck.md`
- `reports/payment-email-repair-report.md`
- `reports/production-hardening-audit.md`
- `reports/public_homepage_pulse_mockup.html`
- `reports/pulse_production_publish_trace.md`
- `reports/pulsesoc_final_branding_cleanup.md`
- `reports/pulsesoc_full_branding_audit.md`
- `reports/pulsesoc_missed_branding_audit.md`
- `reports/pulsesoc_text_replacement_changes.md`

## 4. Old Audit Outputs

These changed audit scripts or audit outputs were preserved:

- `scripts/brevo_notifications_audit.py`
- `scripts/legal_name_correction_audit.py`
- `scripts/notification_delivery_audit.py`
- `scripts/performance_audit.py`
- `scripts/phase2_media_cdn_audit.py`
- `scripts/pulse_public_homepage_positioning_audit.py`
- `scripts/pulse_roast_battle_route_audit.py`
- `scripts/pulsesoc_branding_audit.py`
- `scripts/pulsesoc_payment_routes_audit.py`
- `scripts/r2_upload_smoke_test.py`
- `scripts/stripe_premium_audit.py`
- `scripts/undx_homepage_audit.py`

## 5. Untracked Test Artifacts

These untracked files were preserved and need owner approval before deletion or commit:

- `reports/content_planner_dashboard_report.md`
- `reports/live_inside_reels_audit.json`
- `reports/verification_backend_management_qa.md`
- `reports/verification_badge_system_report.md`
- `reports/verification_center_report.md`
- `reports/verification_feature_unlock_matrix.md`
- `reports/verification_security_privacy_review.md`
- `scripts/company_name_branding_audit.py`
- `scripts/company_name_route_qa.py`
- `scripts/email_sms_identity_audit.py`

## 6. Real Source-Code Changes That Must Not Be Deleted

These are modified source, service, template, worker, documentation, or configuration files. They must be preserved unless Roody explicitly approves a reset or cleanup plan:

- `.env.example`
- `alert_worker.py`
- `bot.py`
- `docs/pulse_architecture_report.md`
- `docs/undx_manual.md`
- `media_worker.py`
- `pulse_communications_v2/twilio_service.py`
- `pulse_worker.py`
- `seo/schema.py`
- `services/*.py`
- `services/providers/sms_provider.py`
- `static/llms.txt`
- `telegram_worker.py`
- `templates/account.html`
- `templates/index.html`
- `templates/privacy.html`
- `templates/pulse_labs.html`
- `templates/seo_page.html`
- `templates/terms.html`
- `undx_execution_kernel.py`
- `undx_router.py`

## 7. Unknown Files Requiring Roody Approval

All dirty files not included in the Status UI V4 commit require explicit approval before deletion, reset, or staging.

## Verification Results

- `git status --short`: dirty worktree remains, all unrelated dirty files preserved
- `git diff --check`: passed
- `curl -fsS http://127.0.0.1:5069/health`: passed with `{"ok":true,"service":"coinpilotx-web"}`

## Cleanup Performed

None. This was the safest outcome because no dirty file was clearly disposable without risking evidence or active work.
