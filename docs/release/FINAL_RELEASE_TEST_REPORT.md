# Final release test report — 2026-09-17

Every gate below was run on `7dfeb7ac` in an isolated worktree
(`/Users/hmcherie/Desktop/cpx-final-consolidation`), not in the shared primary
checkout, so no other session's dirty tree could contaminate a result.

## Results

| Gate | Command | Result |
|---|---|---|
| Real-time audio change gate | `scripts/realtime_audio_change_gate.py --base 321f1c1c --head HEAD` | **accepted**, exit 0 |
| Protection suite | `scripts/protection/run_protection_suite.py` | **687 checks / 46 suites**, all passed |
| Jest | `npm test` (mobile-native) | **467 suites / 8055 tests**, all passed |
| TypeScript | `tsc --noEmit` | clean |
| Swift host unit tests | `scripts/test_apple_translation_swift.sh` | **57 tests / 194 assertions** |
| Swift bridge typecheck | `scripts/typecheck_apple_translation_swift.sh` | OK against the real iOS SDK and the real `ExpoModulesCore.swiftmodule` |
| Audio batteries (jest) | protected-path suites | 191 + 377 + 22 passed |
| Audio batteries (backend) | protected-path suites | 19 + 13 passed |
| Environment / route / route-contract | `tests/protection/` | 35 passed |
| iOS push gates | push protection suites | 24 passed |
| Icon badge | `tests/test_push_icon_badge_combined.py` | 8 passed |
| i18n coverage | `npm run verify` i18n gate | 11 locales at 100% (4612/4612) |
| Secret scan | full delta | clean |
| Build artifacts | full delta | none |

## Two gate results that needed interpreting rather than reading

**The audio gate first said no.** It rejected the range with
`DECLARATION NOT ACCEPTED`, exit 1. The naming check was fine — `Podfile.lock`
was already named in the declaration. `--json` showed the real reason: the
declaration was last updated in `cd431e7f`, but protected paths changed seven
commits later in `6ba609b9`. A declaration that is an *ancestor* of the change it
claims to describe cannot describe it, which is exactly the failure mode the
staleness check exists to catch. Appending the addendum in `7dfeb7ac` makes the
declaration a descendant, and the gate accepts.

Worth stating plainly: the gate was right both times. The first answer was not
noise to be worked around.

**Jest looked like it was failing and was not.** Grepping the audio suites for
`FAIL|✕` returned source code-frame lines such as
`148 | if (verbose || FAILURE_EVENTS.has(event.name)) {` — matches on the word
`FAIL` inside the code being *displayed*, not on a failure. Re-grepping only
`^(Tests|Test Suites|Snapshots):|^FAIL` and separately capturing the exit code
showed both suites fully green.

## The declaration this release discharges

`reports/realtime_audio_change_declaration.md` carried a `### Still owed` section
under its 2026-09-17 translation addendum, asking for the full battery to be run
and recorded. `7dfeb7ac` appends a "Final consolidation addendum" containing the
range, the base, the reason, the affected-file table, and the validation table —
which is the thing that was owed. The section is now discharged rather than
carried forward.

## What these gates do not cover

Static checks do not replace device QA for livestream, push, checkout or uploads.
This mission did not change any of those subsystems, but the limitation is stated
here rather than left implied.

Physical audible validation of a live call was **not** performed — see
`FINAL_DEVICE_INSTALL_REPORT.md` for why, and for what was verified instead.
