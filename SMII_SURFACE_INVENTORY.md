# SMII Surface Inventory — PulseSoc Operations Center

Audit date: 2026-08-31. Source of truth: sidebar `nav_groups` in `admin_page_html` (bot.py:14876-14938),
shell chrome bot.py:14871-15032. All admin screens are server-generated inline HTML in bot.py except
/admin/calls (blueprint + Jinja template). Per-row action counts are counted once per row template.
Counts are approximate (static markup review, not runtime render).

## Section 1 — Totals

```
SIDEBAR GROUPS : 11
SCREENS        : 39 sidebar links -> 38 unique screens (1 duplicate href) + Logout
ROUTES         : 38 mapped (37 in bot.py, 1 in pulse_communications_v2/routes.py)
BUTTONS        : ~45  (form submits + action buttons, row templates counted once)
LINKS          : ~220 (40 sidebar/topbar + ~180 in-page nav/filter/footer links)
DROPDOWNS      : 3    (Support status select, Emails filter select, +1 minor)
TOGGLES        : 2    (checkbox inputs on sidebar screens; 5 more on non-sidebar /admin/test-notification)
FILTERS        : ~42  (filter tab links + search/filter forms across Users, Emails, Ads Verification, Security, Audit Logs, Calls, Payment Emails)
FORMS          : ~36  (32 POST + 4 GET search/filter)
ROW ACTIONS    : ~26 distinct per-row controls (Moderation 3, Ads Review 4, Music 2, Ads Verif 5, Calls 5, Payment Emails 1, Security 5, Users 1)
BULK ACTIONS   : 9    (Delivery 4 real, Feed Health 4 [3 FAKE], Emails retry-failed 1)
MODALS         : 1    (shell command palette, Cmd-K `ops-palette`; plus JS confirm() on Calls force-end)
READ-ONLY / NAV-ONLY SCREENS: 24 of 38
```

## Section 2 — Per-group inventory

### Overview (3)
| Screen | Href | Route | Controls | Flags |
|---|---|---|---|---|
| Dashboard | /admin/dashboard | bot.py:15429 | ~15 links, 1 POST form/1 button | — |
| Global Command | /admin/global-command | bot.py:95292 | ~14 nav links, live-poll JS | Nav-only |
| Backend Command Center | /admin/command-center | bot.py:95763 | ~20+ card links (modules, seller queue, departments), provider table, 6 footer links | Owner-only; nav-only (0 forms) |

### Operations (6)
| Screen | Href | Route | Controls | Flags |
|---|---|---|---|---|
| Users | /admin/users | bot.py:12575 | 1 GET search form, 8 filter links, Export CSV, pager, per-row view links | List/read-only |
| Support | /admin/support | bot.py:27769 | 1 POST form + status select | — |
| Admins | /admin/admins | bot.py:26510 | add/edit links | Nav-only list |
| Employees | /admin/employees | bot.py:26698 | links | Nav-only list |
| Departments | /admin/departments | bot.py:26821 | department room links | Nav-only list |
| Data Recovery | /admin/data-recovery | bot.py:17089 | 2 POST forms | — |

### Moderation & Trust (6)
| Screen | Href | Route | Controls | Flags |
|---|---|---|---|---|
| PulseSoc Mod | /admin/pulse-moderation | bot.py:91403 | 3 POST buttons per row | POST handler skips verify_csrf (91410-91425) |
| Ads Review Board | /admin/pulse-ads-review-board | bot.py:20478 (action 20548) | 4 POST row-action forms/row | — |
| Music Review | /admin/pulse-music-review | bot.py:42092 (card 42016) | per pending card: audio player, proof link, note textarea, Approve/Reject POST; 3 KPI cards; read-only inventory table; 3 footer links | — |
| Chat Reports | /admin/private-chat-reports | bot.py:27358 | 0 actions | DEAD PAGE — read-only despite status column |
| Watch Rules | /admin/watch-rules | bot.py:38423 | 0 actions | Read-only |
| Scam Shield | /admin/scam-shield | bot.py:27217 | 0 actions | Read-only |

### Advertising (2)
| Screen | Href | Route | Controls | Flags |
|---|---|---|---|---|
| Advertiser Verification | /admin/pulse-ads-verification | bot.py:20590 (action 20673) | 6 filter tabs, search form, 5 POST row actions/row | — |
| Ad Review | /admin/pulse-ads-review-board | bot.py:20478 | (same screen) | DUPLICATE of Moderation link |

### Social Platform (4)
| Screen | Href | Route | Controls | Flags |
|---|---|---|---|---|
| Feed Health | /admin/pulse-feed-health | bot.py:91447 | 4-button repair POST form | 3 of 4 buttons FAKE SUCCESS (see Section 3) |
| PulseSoc Analytics | /admin/pulse-analytics | bot.py:91619 | 0 actions | Read-only |
| Education | /admin/education | bot.py:38439 | 0 actions | PLACEHOLDER (38454) |
| Calls | /admin/calls | pulse_communications_v2/routes.py:1526 (template templates/admin_calls_command_center.html) | 2 POST test forms (test-config L87, quality-test L90), 4 view filters + 3 utility links (L111-117), 10 topnav links, per-row: Inspect/Timeline/Delivery/Quality links + Force End POST w/ confirm (L158) | Only sidebar route outside bot.py |

### Commerce (4)
| Screen | Href | Route | Controls | Flags |
|---|---|---|---|---|
| Transactions | /admin/transactions | bot.py:16353 (alias 16354) | 0 actions | Read-only |
| Payments Command Center | /admin/payments-command-center | bot.py:92612 | 9 quick-action links, KPI grid | Nav-only; targets incl. 12 stacked stub routes (92746-92757) |
| Unmatched Payments | /admin/unmatched-payments | bot.py:17331 | 0 actions | Read-only |
| Payment Emails | /admin/emails/payment | bot.py:17185 | GET filter form (3 inputs), CSV export, per-row Resend POST | — |

### Communications (4)
| Screen | Href | Route | Controls | Flags |
|---|---|---|---|---|
| Notifications | /admin/notifications | bot.py:27240 | 2 links (Email Health, Delivery Logs), KPI + tables | Read-only (test-email form lives on subpage 27277) |
| Delivery | /admin/notification-delivery | bot.py:38275 (queue-action 38311) | 1 POST form, 4 bulk-action buttons (all real) | — |
| Emails | /admin/emails | bot.py:16546 | 12 filter links, 1 select, 4-5 POST forms (test / resend-confirmation / resend-welcome / retry-failed / owner set-password), pager | — |
| Telegram | /admin/telegram | bot.py:27024 | 0 actions | Read-only |

### Intelligence (2)
| Screen | Href | Route | Controls | Flags |
|---|---|---|---|---|
| AI Usage | /admin/ai-usage | bot.py:27184 | 0 actions | Read-only |
| Predictions | /admin/predictions | bot.py:29595 | 0 actions | Read-only |

### Infrastructure (3)
| Screen | Href | Route | Controls | Flags |
|---|---|---|---|---|
| PulseSoc Infra | /admin/pulse-infrastructure | bot.py:15829 | 0 actions | Read-only |
| System | /admin/system | bot.py:15620 | 0 actions | Read-only |
| Performance | /admin/performance | bot.py:78068 | 0 actions (3 KPI cards, 3 tables) | Read-only |

### Security (4)
| Screen | Href | Route | Controls | Flags |
|---|---|---|---|---|
| Security | /admin/security | bot.py:27499 | 5 tab links; per-row Block IP / Block Domain / Mark Safe / Investigate POST forms + Explain Risk | Investigate + Explain Risk partially fake (Section 3) |
| Audit Logs | /admin/audit-logs | bot.py:17348 | filter links only | Read-only |
| Command Logs | /admin/command-logs | bot.py:27201 | 0 actions | Read-only |
| Visitors | /admin/visitors | bot.py:16302 | 0 actions | Read-only |

### Growth (1)
| Screen | Href | Route | Controls | Flags |
|---|---|---|---|---|
| SEO | /admin/seo | bot.py:38458 | 0 actions | Read-only |

Shell chrome (every screen): command palette modal (Cmd-K), status strip polling /admin/ops/status.json
(bot.py:15035), palette search /admin/ops/search.json (bot.py:15114), mobile menu button, Logout /admin/logout.

## Section 3 — Dead / placeholder / suspect controls

1. **bot.py:91474-91475** (buttons rendered ~91543) — Feed Health "Rebuild Feed Cache", "Rebuild Trending",
   "Regenerate Media Thumbnails": POST handler matches the action name and only sets a "queued safely"
   message. **No work is performed — fake success.** Only `flush_deleted_cache` is real.
2. **bot.py:27541-27542** — Security "Investigate": sets "Investigation noted for: {value}" message only;
   no investigation record beyond the activity log.
3. **bot.py:27555-27557** (also 27687) — Security "Explain Risk": silently degrades to static
   "AI analysis not enabled" text when the command center is disabled.
4. **bot.py:27358-27369** — Private Chat Reports: read-only table with a status column but zero action
   controls; dead moderation queue (no producer→queue→action wiring).
5. **bot.py:38454** — Education page self-describes as placeholder ("Editing UI can be expanded here");
   no controls.
6. **bot.py:26692** — Edit Admin "Save Admin" rendered `disabled` when owner_locked (intentional guard,
   flagged for completeness).
7. **bot.py:91410-91425** — pulse-moderation POST handler does not call `verify_csrf()` (only sidebar
   mutation handler without CSRF; security flag, not a dead control).
8. **bot.py:92746-92757** — 12 stacked route decorators (/admin/treasury … /admin/disputes) share one
   thin stub handler; these are the targets of Payments Command Center quick-action links.
9. **nav_groups bot.py:~14900** — "Ad Review" (Advertising) is a duplicate href of "Ads Review Board"
   (Moderation & Trust): same screen listed twice in the sidebar.
