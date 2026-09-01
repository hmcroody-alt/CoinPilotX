# SMII Stage 2 — Zero-Trust Cross-Examination of Ops2 Report

Date 2026-08-31. Rule: only evidence levels A (live prod runtime proof) or B (integration test executed this pass) count. C/D/E/F PASS labels are demoted to UNPROVEN-pending-runtime and queued for Stage 3-8/30-44 revalidation.

Evidence levels: A=live prod proof · B=integration test run · C=unit test run · D=static code trace · E=route existence · F=inference.

| Ops2 stage | Prior label | Actual evidence | Demoted? | Revalidation path |
|---|---|---|---|---|
| 1-2 Deploy/prod sample | BLOCKED | A (Railway MCP: prod=main@6a051182; ba87c46c..6ec09e99 unmerged) | no | owner merge+deploy |
| 3-5 Live crawl | BLOCKED | — | no | owner admin session (Chrome MCP) |
| 6 Email master matrix | PASS | D (static trace of senders/outbox) | YES → UNPROVEN | Stage 11-13 runtime email matrix |
| 7 Subscription exactly-once | PASS | B (test_subscription_exactly_once 2/2, hermetic sqlite) · Stripe path D (ENV-SKIP) | Stripe path YES | Stage 33 prod webhook replay |
| 8 QA purchase | BLOCKED | — | no | deployed build |
| 9 Email provider/worker | PASS(arch)/FLAG | A (Railway MCP proved email_worker ABSENT) | no — FLAG confirmed | **FIXED this pass: email_worker service created (b512ca74), building main@6a051182** |
| 10 Delivery vocab / webhook auth | PASS + FLAG | D (code) + A (var ABSENT) | partially | **FIXED this pass: BREVO_WEBHOOK_SECRET set on web; owner must update Brevo webhook URL** |
| 11 Push master | PASS | D | YES → UNPROVEN | Stage 14 runtime push proof |
| 12 Dedupe tests | PASS | B (test_notification_email_dedupe 3/3) | no | — |
| 13 Support | PASS | D | YES → UNPROVEN | Stage 15 live mutation |
| 14 Admins/RBAC | PASS/PARTIAL | D | YES → UNPROVEN | Stage 16 live denial matrix |
| 15 Employees | PASS | D (bot.py:26801 read) | YES → UNPROVEN | Stage 16 live CRUD |
| 16 Data Recovery | PASS | D | YES → UNPROVEN | Stage 18 live |
| 17 Moderation queues | PARTIAL | D + B (moderation audit suites 5/5) | YES for runtime | **Chat Reports wired 6ec09e99 (was DEAD QUEUE); Music uploader-notify still open** |
| 18 Advertising | PASS | D | YES → UNPROVEN | Stage 20 live decision trace |
| 19 Commerce | PASS/PARTIAL | D | YES → UNPROVEN | Stage 21 live refund preview check |
| 20 Comms safety | PASS | D | YES → UNPROVEN | Stage 22 |
| 21 Intelligence | PASS | D | YES → UNPROVEN | Stage 23 |
| 22 Infra health | PASS/PARTIAL | D + A (Railway service list, 8 svc SUCCESS) | runtime checks YES | Stage 28 done (A); page semantics need live view |
| 23 Security/CSRF | PASS | D (middleware read) | YES → UNPROVEN | Stage 37-39 live CSRF/IDOR probes |
| 24 Growth | PASS | D | YES → UNPROVEN | Stage 40 |
| 25-30 Zero/empty truth | PASS | D | YES → UNPROVEN | Stage 30-32 live empty-state render |
| 31 N+1 | PASS | D (query shape review) | YES → UNPROVEN | Stage 41 timing on prod lists |
| 32 IDOR | PASS | D | YES → UNPROVEN | Stage 38 authenticated probes |
| 33-34 Browser stages | BLOCKED | — | no | owner session |
| 35 Audit fields | PASS | D | YES → UNPROVEN | Stage 42 live audit read-back |
| 36 Manual-only flags | RECORDED | A (Railway MCP) | no | flags (a),(b) fixed this pass; (c) obsolete once worker green |
| 37-39 Fix batches | PASS | B (suites green) + D (gate) | no | — |
| 40-41 Prod acceptance | BLOCKED | — | no | owner merge+deploy |

## Standing after cross-exam
- A-level held: Railway ground truth (Stage 28), deploy-SHA gap, worker/var absence (now remediated).
- B-level held: 3 hermetic suites (2/2, 3/3, 5/5) run this branch.
- Everything else (≈22 prior PASS labels) is D-level and stands UNPROVEN until the live crawl + mutation stages, all gated on: (1) owner merge of release/full-sweep-20260826 → main + deploy, (2) owner-authenticated admin browser session.

## Infra remediation log (this pass)
- email_worker Railway service b512ca74-5e43-4215-90c1-97a3b4f0205e: repo hmcroody-alt/CoinPilotX@main, start `python email_worker.py`, restart ALWAYS, 177 vars referenced from web service (`${{CoinPilotX.*}}`), UNDX_WORKER_ENABLED=0, interval 10s batch 25.
- BREVO_WEBHOOK_SECRET set on web service (redeploy triggered). OWNER ACTION (DONE 2026-09-01): Brevo dashboard webhook URL set to `https://pulsesoc.com/api/brevo/webhook?secret=<value>` (value in Railway var). NOTE: earlier version of this line gave a wrong path (`/webhooks/brevo/delivery` → 404); corrected and verified live — delivery events now update email status (proof: message 80ec5ce5de2f4378 sent→clicked).
