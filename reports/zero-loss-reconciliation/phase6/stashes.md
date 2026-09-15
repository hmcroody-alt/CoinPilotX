# Phase 6 — stash audit

Four stashes. **All four are SUPERSEDED. Zero work is lost by leaving them
unapplied.** None was dropped, popped, or applied; the question of what a stash
*contains* is answerable without touching the working tree.

## A measurement bug that reported the exact opposite

The first run of this probe reported `0/50 present` for three of the four
stashes — which reads as "hundreds of lines of unshipped work". It was an
artifact, and it is worth writing down because the failure is silent and it
fails *toward* a false alarm.

`git stash push` writes a **merge commit**: first parent the HEAD you stashed
from, second the index, optionally a third for untracked files. `git show` on a
merge emits **combined diff** format — one prefix column per parent. An added
line therefore arrives as `++text`, not `+text`. The Phase 4b extractor strips
one leading character, correct for an ordinary commit, which left a literal `+`
welded to the front of every line. Every content search then looked for a string
that cannot occur in any source file, and every search missed.

The fix is to ask for the two-dot diff explicitly, `git diff <stash>^ <stash>`,
which is an ordinary one-column patch. `scripts/zero_loss_stash_presence.py`
now does that and additionally **raises** if a second marker survives, so this
particular lie cannot be told again quietly.

The tell was available before the re-run: the branding rename in stashes 1–3 had
already been independently confirmed present on main across 136 files. When a
measurement contradicts a fact you already established, the measurement is the
thing to doubt.

## Results after the fix

| stash | subject | sampled | present | verdict |
|---|---|---|---|---|
| `stash@{0}` | wip: premium command center awaiting 10-locale completion | 60 | 40 → see below | SUPERSEDED |
| `stash@{1}` | undx-v3-pre-integration-preservation-20260719 | 60 | 59 | SUPERSEDED |
| `stash@{2}` | autostash | 60 | 59 | SUPERSEDED |
| `stash@{3}` | autostash | 60 | 59 | SUPERSEDED |

### stash@{1}, stash@{2}, stash@{3} — branding, already upstream

103–120 files each, dominated by the `CoinPilotXAI` → `CoinPlotXAI Inc.`
rename. That rename is on main.

Of the four lines the sampler first called missing, three are present and merely
*extended* past the sampled prefix — confirmed by prefix search at 90
characters. The fourth is genuinely absent in the stash's form, and main's
replacement is **stronger**, not merely different:

```
stash:  Sender: {clean_html(brevo_diag.get('sender_email_masked') ...)}
main:   Sender: {html_escape(clean_html(brevo_diag.get('sender_email_masked') ...))}
```

The stash added a Reply-to field to the Brevo diagnostics panel. Main has the
Reply-to field *and* wraps every interpolation in `html_escape`. Applying the
stash would strip an XSS hardening off a rendered admin panel. This is the case
the mission's "a branch being merged does not prove all its work is present"
warning has a mirror image of: sometimes the *stash* is the stale side, and
"restoring" it is a regression.

### stash@{0} — Premium Command Center, an earlier draft of what shipped

138 insertions across `PremiumCenterScreen.tsx` (+95) and
`i18n/catalogs/en/extended.json` (+43). The 20 lines the sampler could not find
are real absences, so this needed reading rather than counting — and reading it
shows main holds a strict superset.

| | stash draft | main today |
|---|---|---|
| `COMMAND_SPACES` | 8 flat string keys | 9 objects — the same 8 plus `verified` |
| tiles | all inert "NEXT" chips | 4 navigate to shipped routes (`VerificationCenter`, `UndxCapabilities`, `CreatorStudio`, `TrustSafetyHelp`); 5 inert |
| founder gating | none | `founderOnly` filter on the founder tile |
| `usage`, `recommended` | roadmap rows with NEXT chips | promoted to live sections rendering real server signals and recommendation reasons |
| locales | `en` only | all 11 (`ar de en es fr hi ht ja ko pt zh`) |

The stash's own message names its blocker — *"awaiting 10-locale
completion"* — and that blocker is resolved: every locale carries the
`commandCenter` keys on main. The two module keys that disappear from
`COMMAND_MODULES` between the draft and main (`usage`, `recommended`) were not
dropped; they graduated out of the roadmap list into sections that render
measured data.

Applying this stash would replace nine partly-live tiles with eight dead ones
and regress ten locales to fallback.

## Disposition

No stash is applied. No stash is dropped — they stay as historical record, which
costs nothing and is what the mission's git-safety rules require. Every stash is
classified **SUPERSEDED** in the master ledger.
