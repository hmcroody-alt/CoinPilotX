# Branch protection required for the real-time audio hard-lock

## Status: NOT CONFIGURED

This document specifies settings. It does not claim they are active.

The settings below could not be applied from the environment that built this
hard-lock: GitHub is unreachable from it (SSH returns `Forbidden`, HTTPS returns
`403 from proxy`), and branch protection is a repository-administration setting
that cannot be committed as a file in any case. Repository administration must
apply these manually and then replace the status line above with the date and
the account that verified them.

Until that happens, every other layer of the lock still works — the workflow
runs, the gate fails, the tests fail — but a maintainer with write access can
merge past a red check. Branch protection is the layer that removes that.

## Why each setting exists

A CI job that can be bypassed is a suggestion. The change-detection gate is the
only thing standing between a protected audio file and a merge with no record of
who validated it, so its check must be *required*, not merely present.

## Settings to apply

Repository → Settings → Branches → Add branch protection rule.

**Branch name pattern:** `main`
(and `master` if that ref is still in use; the workflow triggers on both.)

| Setting | Value | Why |
| --- | --- | --- |
| Require a pull request before merging | on | Direct pushes bypass every check below. |
| Require approvals | 1 minimum | |
| Dismiss stale pull request approvals when new commits are pushed | on | Otherwise a clean review can be reused for a later commit that touches audio. |
| **Require review from Code Owners** | **on** | Without this, `.github/CODEOWNERS` is an advisory reviewer-suggestion list and blocks nothing. |
| Require status checks to pass before merging | on | |
| Require branches to be up to date before merging | on | A stale branch can pass the gate against an old base and merge a protected change the gate never saw. |
| Require conversation resolution before merging | on | |
| Do not allow bypassing the above settings | on | Includes administrators. A lock that admins can walk through protects nobody, because the person most likely to be shipping an emergency audio fix at 2am is an admin. |
| Allow force pushes | off | A force push can rewrite the commit the baseline document pins. |
| Allow deletions | off | |

## Required status checks

Add these by exact job name. They come from
`.github/workflows/realtime-audio.yml`.

| Check name | Required | Why |
| --- | --- | --- |
| `Change declaration required` | yes | The hard gate. Fails when a protected path changed without an acceptable `reports/realtime_audio_change_declaration.md`. |
| `Architecture boundary (native + backend)` | yes | Catches a forbidden `AVAudioSession` call added to an unrelated screen — a file that is deliberately *not* in the protected manifest, so the gate alone would not see it. |
| `Critical audio golden flows` | yes | Release-blocking invariants plus TypeScript. |
| `Backend token and room policy` | yes | Publish rights are granted server-side, where no client test can see a mistake. |
| `Native build verification (prebuild)` | recommended | Runs on `macos-latest` and is the slowest job. Required only if the cost is acceptable; a change can pass every Jest test and still produce an app with no microphone permission. |
| `audio-critical-change label` | no | Advisory by design. Failing a build over a missing label teaches people to add labels, not to validate audio. |

A status check only becomes selectable in the branch-protection UI after it has
reported at least once. Open one pull request against the protected branch
first, let the workflow run, then add the checks by name.

## Label to create

Repository → Issues → Labels → New label.

- **Name:** `audio-critical-change` (exact string; the manifest and the workflow
  both reference it)
- **Colour:** any
- **Description:** *Changes a protected real-time audio path. Requires
  reports/realtime_audio_change_declaration.md and physical audible validation.*

The label is applied by the author, not by automation. The workflow emits a
warning annotation when it is missing, and does not fail.

## How to verify the settings actually took effect

Do not mark this document configured on the strength of the settings page. Prove
it end to end:

1. Branch from `main`. Add a comment line to
   `mobile-native/src/core/realtimeAudioEngine.ts`. Do not touch the
   declaration. Open a pull request.
2. Expect: `Change declaration required` fails, and the merge button is blocked
   rather than showing "merge anyway".
3. Fill in `reports/realtime_audio_change_declaration.md` in the same branch,
   naming that file. Push.
4. Expect: the check passes and the merge button unblocks, with a Code Owner
   review still outstanding.
5. Close the pull request without merging. Record the date and the verifying
   account at the top of this document.

Step 2 is the one that matters. If the merge button offers a bypass, "Do not
allow bypassing the above settings" is off and the lock is advisory.

## What this does not cover

Branch protection governs merges into the protected branch. It does not govern
the physical validation the declaration promises. Nothing in GitHub can verify
that a person heard audio on a phone; that evidence lives only in
`reports/realtime_audio_verified_baseline.md`, written by the person who heard it.
