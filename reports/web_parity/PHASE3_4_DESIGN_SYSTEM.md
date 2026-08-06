# PulseSoc Web Parity — Phase 3/4: Design System + Shell Audit

**Generated:** 2026-08-05
**Source snapshot:** `bot.py` @ md5 `522b9419c4283966d27d99abd5208720`
**Deliverable:** `static/css/pulsesoc-tokens.css` (new file, 160 tokens)
**Method:** static extraction + verification scripts. No tracked file modified.

---

## 1. Measured drift between native and web

| Source | Count |
|---|---|
| Native semantic tokens (`mobile-native/src/theme/colors.ts`) | 23 |
| Web CSS custom-property **names** across 19 stylesheets | 159 |
| Web vars defined with **conflicting values** in different files | **45** |
| Distinct hex colours in `static/css/*.css` | 310 |
| **Distinct hex colours hardcoded inside `bot.py`** | **180** |
| **Total hardcoded hex occurrences inside `bot.py`** | **1,002** |
| Inline `<style>` blocks inside `bot.py` | 97 |
| Native tokens matching their nearest web equivalent **exactly** | **0** |

Every one of the 23 native tokens drifts from its closest web value. The drift is
small but systematic — the signature of two palettes maintained independently:

| Native token | Native | Nearest web | Web value | ΔRGB |
|---|---|---|---|---:|
| `accent` | `#32e6b3` | `--pulse-green` / `#36e58f` (107 uses in bot.py) | `#36e58f` | 7.1 |
| `accentStrong` | `#61d8ff` | `--pulse-cyan` / `#6edff6` (108 uses) | `#6edff6` | 7.2 |
| `text` | `#f4f7fb` | `--pulse-text` / `#f2fbff` (76 uses) | `#f2fbff` | 5.7 |
| `background` | `#050910` | `--pulse-bg` / `#050b14` (42 uses) | `#050b14` | 2.2 |
| `muted` | `#9aa8b7` | `--muted` / `#9fb5c0` (60 uses) | `#9fb5c0` | 7.0 |
| `danger` | `#ff5f7e` | `--red` / `#ff6b7a` | `#ff6b7a` | 1.0 |
| `disabled` | `#51606c` | `--quiet` / `#6f8495` | `#6f8495` | **62.3 MISMATCH** |
| `border` | `#203746` | `--pulse-panel-strong` | `rgba(17,29,50,.94)` | **36.1 MISMATCH** |

Worst offenders are the *conflicting* names — the same variable resolves
differently depending on stylesheet load order:

```
--bg              : #020711  |  #030811  |  #050b14
--control-accent  : #49ffc8  |  #4f8cff  |  #5ff4ff     (three different hues)
--control-accent-2: #23dd93  |  #2de0c0  |  #2fb7ff
--control-accent-3: #3a8dff  |  #79ffe5  |  #7c5cff
```

`--control-accent` being simultaneously green, blue and cyan means messenger
surfaces render differently depending on which stylesheet loaded last. That is
a live inconsistency, not a theoretical one.

Full per-token data: `reports/web_parity/token_drift.json`.

---

## 2. Deliverable — `static/css/pulsesoc-tokens.css`

New file. Six sections:

1. **Primitives** — the native palette verbatim, all 23 values. Native is the
   reference for product experience per the mission's Primary Principle, so
   every colour is the *native* value, not the previous web value.
2. **Semantic tokens** — `surface.*`, `text.*`, `action.*`, `status.*`,
   `border.*`, plus `domain.*` for the five product accents (intelligence,
   creator, economy, safety, crypto) that carry meaning and must stay distinct.
   Naming follows the shared vocabulary in the mission brief.
3. **Scale** — spacing, radius, typography, elevation, motion, z-index,
   `--touch-target-min: 44px`. The spacing scale intentionally runs wider than
   native (`--spacing-section: 64px`) to preserve desktop ergonomics rather than
   forcing mobile-width rhythm onto wide screens.
4. **Accessibility baselines** — `prefers-reduced-motion` zeroes all durations;
   `prefers-contrast: more` lifts `--text-secondary` from roughly 4.1:1 to 7:1.
5. **Legacy compatibility aliases** — the load-bearing section. Re-points old
   variable names at canonical tokens and resolves the 45 conflicts in favour of
   the native value.
6. **Primitive utilities** — `.pulse-surface`, `.pulse-status--*`,
   `.pulse-action--*`, focus-visible ring, skip link. Deliberately not a
   framework; the goal is to give inline handlers something semantic to emit.

### Why aliasing matters here

151 page routes generate HTML by string concatenation inside `bot.py` with
hardcoded class names. They cannot be restyled by editing a template, because
there is no template. Aliasing lets those pages converge on the native palette
without any route handler being touched — which is also what makes this work
safe to do while other agents hold `bot.py`.

### Verification (`scripts/web_parity/verify_tokens.py`)

```
braces balanced : True (23 open / 23 close)
parens balanced : True (149 / 149)
tokens defined  : 160
internal refs   : 72
DANGLING refs   : 0
legacy vars consumed across css + templates : 149
  covered by token layer : 40
  not covered            : 109
```

The 109 uncovered are per-component geometry (`--media-ratio`, `--wave-index`,
`--video-aspect`, `--status-story-font-size`) — locally computed values that
legitimately do not belong in a global colour/scale layer. All semantic strays
found by the verifier (`--line-strong`, `--warn`, `--soft`, `--media-backdrop`,
safe-area insets) were folded in.

---

## 3. Phase 4 shell audit — there is no global shell

`grep` for `<head>` in `bot.py`: **32 distinct emissions**, across **14 shell
builder functions** (`arena_page_shell`, `education_shell`,
`dashboard_account_shell`, `dashboard_ads_shell`, `dashboard_ai_shell`,
`dashboard_creator_shell`, `dashboard_crypto_shell`, `dashboard_economy_shell`,
`dashboard_intelligence_shell`, `dashboard_network_shell`,
`dashboard_system_shell`, `pulse_section_shell`, `pulse_social_shell`,
`_verification_admin_shell`).

| Shell property | Count |
|---|---|
| `<head>` emissions | 32 |
| That load **any** external stylesheet | **3** |
| That load **no** stylesheet (inline `<style>` only) | **29** |
| That load the token layer | 0 (new file, not yet wired) |

Only `admin_page_html` (line 14292), `pulse_page_html` (35571) and
`pulse_social_shell` (41661) link external CSS. The other 29 shells are
visually self-contained with inline `<style>` blocks — which is why the same
product renders differently across `/about`, `/arena`, `/education`,
`/quote`, `/watch`, `/alerts` and the dashboards.

**Phase 4 conclusion:** aligning the shell is not "restyle the shell." There
are 14 parallel shells and 32 head blocks. The work is to collapse them to one,
and that requires editing `bot.py`.

---

## 4. Adoption worklist (for whoever owns `bot.py`)

The token layer is inert until linked. Adoption is one line inserted at each of
32 known locations, ordered **before** any other stylesheet so the cascade
resolves aliases first:

```html
<link rel="stylesheet" href="/static/css/pulsesoc-tokens.css">
```

Exact line numbers and current stylesheet loads per site:
`reports/web_parity/shell_audit.json`.

Suggested sequencing, lowest risk first:

1. Wire the 3 shells that already load external CSS (14292, 35571, 41661) —
   highest traffic, immediately visible, easy to revert.
2. Wire the 11 `*_shell()` builders — one insertion covers many routes each.
3. Wire the remaining one-off pages.
4. Only then begin deleting inline `<style>` blocks and replacing the 1,002
   hardcoded hex values with `var(--…)`.

Step 4 is the large one and should be a mission of its own. Steps 1–3 are
mechanical and independently revertable.

---

## 5. Not established

- Token layer is **not wired in** — requires `bot.py` edits, blocked by concurrent agents
- No visual regression testing performed; no before/after screenshots
- Contrast figures are computed, not measured with an auditing tool against rendered pages
- Native spacing/typography scales were **not** extracted — `mobile-native/src/theme/` contains only `colors.ts` and `ThemeContext.tsx`, so the spacing, radius and type scales above are web-side proposals informed by the legacy CSS (`--pulse-radius: 16px` preserved as `--radius-card`), not native-derived. **This is the one place where values were proposed rather than extracted, and it should be reconciled against native `StyleSheet` usage before being treated as canonical.**
