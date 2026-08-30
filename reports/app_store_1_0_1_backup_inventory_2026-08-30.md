# App Store Connect inventory — PulseSoc 1.0.1, captured 2026-08-30

Rollback/reference snapshot taken **before** any change was made in App Store
Connect, per the Build 19 resubmission brief. App ID `6777591572`.

Everything below is the state as Apple currently holds it. Nothing in this file
was modified by the capture; every call was a GET.

## Version

| Field | Value |
| --- | --- |
| Version (iOS) | `1.0.1` — id `57487c39-79fb-44b2-9880-fee3b4f5ef5d` |
| State | `REJECTED` (editable — a new build can be attached without creating a new version) |
| Release type | `MANUAL` |
| Created | 2026-07-26 |
| Previous live version | `1.0` — `READY_FOR_SALE` |

## Builds

Highest build on the **1.0.1** train is **18** (uploaded 2026-08-27, `VALID`,
not expired). Builds 2–18 exist on this train.

The numbers 19–27 that also appear in the build list belong to the older **1.0**
train and do **not** block reuse of 19 on 1.0.1. **Build 19 is free.**

## Screenshots — 19 total, all `COMPLETE`, 3 sets

| Display type | Count | Files |
| --- | --- | --- |
| `APP_IPAD_PRO_3GEN_129` | 4 | `ipad-13-premium-inside-2048x2732.png`, `ipad-13-feed-inside-2048x2732.png`, `ipad-13-messages-inside-2048x2732.png`, `ipad-13-welcome-2048x2732.png` |
| `APP_IPHONE_65` | 6 | `01_home`, `02_search`, `03_groups_rooms`, `04_profile`, `05_creator_studio`, `06_growth_center` (`_iphone_6_5_en.jpg`) |
| `APP_IPHONE_67` | 9 | `1-home`, `2-messages`, `3-crypto`, `4-premium`, `5-undx`, `6-profile`, `7-marketplace`, `8-security`, `9-reels` (`.jpg`) |

App preview videos: **none** (`PREVIEW_SETS` empty). Nothing to preserve there.

## Localizations

Exactly one: **en-US** (`a0128a8a-4456-4611-a251-4c7200a36fb8`).

| Field | Value |
| --- | --- |
| Keywords | `social,creator,reels,video,messaging,notifications,community,PulseSoc` |
| Promotional text | *(empty)* |
| Description | 895 chars, ends with the standard Apple `Terms of Use (EULA)` link |
| Support URL | `https://pulsesoc.com/support` |
| Marketing URL | `https://pulsesoc.com` |
| What's New | 213 chars (grid-first profile posts, Pulse ID, living-galaxy visuals, icon, Live audio) |

## In-app purchases

Subscription group **PulseSoc Premium** (`22328974`), group localization
`en-US: PulseSoc Premium` in `PREPARE_FOR_SUBMISSION`.

| Product | ID | Period | State | Group level |
| --- | --- | --- | --- | --- |
| PulseSoc Premium Monthly | `6804358210` / `com.pulsesoc.premium.monthly` | `ONE_MONTH` | `MISSING_METADATA` (`DRAFT`) | 1 |
| PulseSoc Premium Annual | `6804362192` / `com.pulsesoc.premium.annual` | `ONE_YEAR` | `MISSING_METADATA` (`DRAFT`) | 2 |

Both have prices, an `en-US` localization, a `COMPLETE` App Review screenshot,
and availability in 10 territories with `availableInNewTerritories = true`.
Both have `reviewNote = null` and **`submitWithNextAppStoreVersion = false`**.

Consumables, all `MISSING_METADATA`:
`com.pulsesoc.adcredits.tier1` (4.99), `tier2` (9.99), `tier3` (24.99),
`tier4` (49.99), `tier5` (99.99).

## App Review Information (as currently stored)

| Field | Value |
| --- | --- |
| Review detail id | `5fa9b99b-e4f0-47b6-97f1-be61c5528b15` |
| Contact | ROODY CHERIE, `support@pulsesoc.com`, `5164618652` |
| Demo account | **`cherieroody@gmail.com`** |
| Demo required | `true` |
| Demo password | set (value not read) |
| Notes | 3212 chars, written for **build 18** and the build-16 Guideline 2.1(b) rejection |

## Apple's open rejection

Submission `1ed02d42-3e31-4a3a-b8d5-d3f90c644b38`, review date **August 30, 2026**,
device **iPad Air 11-inch (M3)**, version reviewed **1.0.1 (18)**, state
`UNRESOLVED_ISSUES`.

> Guideline 2.1 — Information Needed. Apple cannot continue the review because
> they need access to a demo account with an expired subscription to review the
> entire purchase flow, and ask for that account to be provided in the App
> Review Information section.

The 2026-08-28 automated Terms of Use (EULA) message is **already resolved**: the
1.0.1 description carries the standard Apple EULA link and no custom EULA is set.
Guideline 2.1 is the only open issue.
