# PRE_EXISTING_WORKTREE — Premium Mission Safety Manifest

Captured: 2026-08-30 (before any Premium edit)
HEAD: 136106adfb32c8588b22f6f7e0d74bd8258c902f (= deployed SHA)
Branch: release/full-sweep-20260826

## Pre-existing NON-Premium changes (foreign work — DO NOT stage, commit, restore, or reset)

MODIFIED (tracked):
| File | Diff size | md5 at capture |
|---|---|---|
| bot.py | +7 | ea56f0b64b43a344e3c360c99a5df47d |
| services/undx_agent_runtime.py | +86/-x | b0a50138dd0eb324bd30ddec40433afe |
| undx_router.py | +134/-x | 5d948b374a40be323ff07f5fb6fdd2d5 |

UNTRACKED (preserve):
| File | md5 at capture |
|---|---|
| UNDX_CAPABILITY_PLANNER_REPORT.md | 725dd65975ec5306aa2495f3fe70b215 |
| services/undx_capability_planner.py | 0a704da97d622d013b7d1aea3f7d545e |
| services/undx_flag_diagnostics.py | a2bc6e6d75abadef17a2557dc4e5c2b0 |
| tests/undx_agent/test_capability_planner.py | 5e6629e6e95893bc5505e1bcf209b02f |

Note: bot.py is BOTH foreign-modified and likely needed by Premium. Rule: Premium
edits to bot.py must be additive and surgical; the pre-existing +7 diff hunk must
survive untouched. Stage bot.py only with `git add bot.py` after verifying the
foreign hunk is intact, never via `git add .`/`-A`.

## Commit discipline
- Stage files individually by explicit allowlist (maintained in the mission report).
- Pre-commit check: `git diff --cached --name-only` must be a subset of the allowlist.
- FAIL commit if any of: services/undx_agent_runtime.py, undx_router.py, or the
  4 untracked UNDX files appear staged.
