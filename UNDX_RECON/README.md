# UNDX RECON OPERATIONS — INDEX

Source-backed reconnaissance only. **Not** the UNDX training file, **not** a YAML corpus,
**not** executable application code. No production code was changed to produce it.

Start with `00_COMPLETE_RECON_REPORT.md`.

## The ten requested deliverables

| # | Deliverable | File |
|---|---|---|
| 1 | Complete recon report | `00_COMPLETE_RECON_REPORT.md` |
| 2 | System knowledge map | `02_SYSTEM_KNOWLEDGE_MAP.md` |
| 3 | API knowledge map | `03_API_MAP.md` |
| 4 | Database knowledge map | `04_DATABASE_KNOWLEDGE_MAP.md` |
| 5 | User journey map | `05_USER_JOURNEY_MAP.md` |
| 6 | Security knowledge map | `06_SECURITY_KNOWLEDGE_MAP.md` |
| 7 | Feature status map | `09_FEATURE_STATUS_MAP.md` |
| 8 | UNDX capability map | `08_UNDX_CAPABILITY_MAP.md` |
| 9 | Questions / answers collection | `10_QUESTIONS_AND_ANSWERS.md` |
| 10 | Unknown areas requiring more investigation | `11_UNKNOWN_AREAS.md` |

## Supporting files

| File | Covers |
|---|---|
| `01_IDENTITY_AND_PRODUCT_MAP.md` | Stages 1–2 — identity, philosophy, roles, permissions, 32 product areas |
| `07_PAYMENTS_AND_COMMERCE_MAP.md` | Stage 7 — Stripe, Apple IAP, marketplace lifecycle, the payout trace |
| `_superseded/` | Thin first-pass drafts kept for provenance; superseded by the full versions above |

## Rules this recon followed

- Only verified repository information. Every claim cites `file:line`, a live registry key,
  or a live database query.
- Live introspection is preferred over grep — registry counts, status distributions, and
  table totals come from importing the modules and querying `coinpilotx.db`.
- "The doc says" and "the code does" are always distinguished. Where they conflicted, both
  are recorded along with the evidence that settled it.
- Static route or table presence is **not** treated as runtime production readiness.
- Route-pack mounting, provider health, App Store state, physical-device behaviour, and
  deployed Railway variables remain live-QA questions and are listed as such.
- **Two adversarial verification passes were run against the finished documents.** The first
  found 12 errors; the second found that one of the resulting *corrections* was itself wrong.
  Corrections are applied **in place and visibly marked** (⚠ blocks, strikethroughs, retraction
  notes) rather than silently overwritten, so a reader can see which claims have already failed.

## How to read the ⚠ blocks

A ⚠ block means the surrounding text was wrong and has been corrected. Several sections carry
**two** stacked corrections — the original claim, a correction, and a correction *to* the
correction. Those are the highest-risk passages in the set, not the safest: a claim that has
failed twice is a claim about which this codebase is genuinely confusing. Read the whole stack.

## Before writing any corpus

**Read `11_UNKNOWN_AREAS.md` Tier 1 first.** The top blocking question is §1.1 — two tool
registries (87 capabilities vs. 103) are both reachable in production and they disagree about
whether UNDX may send messages, create posts, and create reels. Until that is resolved, the
capability map cannot be treated as the outer bound of UNDX's authority.

One class of claim in this recon proved systematically unreliable: **statements that something
does not exist.** Payouts, comm_v2 schema DDL, and the UNDX tool gateway were each documented
as absent and each turned out to be present. Re-derive every absence claim from code before
relying on it.
