#!/bin/zsh
#
# Stage, commit and push the UNDX subject-naming work.
#
# Double-click this file in Finder.
#
# Why this exists, rather than the assistant just running `git commit`:
#
#   1. `.git/index.lock` is owned by the Mac user and the assistant's sandbox
#      mounts the repo without permission to unlink it. A stale zero-byte lock
#      is therefore fatal there and trivial here.
#   2. The sandbox has the repo mounted but no route to github.com — both
#      git@github.com:22 and https://github.com:443 are refused by its proxy.
#      The push has to happen on the Mac, where the SSH key and the network are.
#
# The lock is only removed if no git process is actually running, because a lock
# held by a live process is not stale — it is the thing keeping two writers from
# corrupting the index.

set -e
cd "${0:A:h}"

print "=============================================="
print " commit_and_push_undx_work"
print "=============================================="

# git takes more than one lock, and a process that dies mid-command leaves
# whichever one it held at the time. Clearing only `index.lock` gets as far as
# `git add` and then fails on `HEAD.lock` during the commit — which is exactly
# what happened the first time this ran. So sweep them all, under one judgement.
setopt local_options null_glob
locks=(.git/*.lock .git/refs/**/*.lock .git/logs/**/*.lock)

if (( ${#locks} )); then
  print "Lock files present:"
  for lock in $locks; do
    print "  ${lock}  ($(stat -f %z "$lock") bytes, $(stat -f %Sm "$lock"))"
  done
  print ""

  # `pgrep -x git` is too blunt on its own: an IDE, a shell prompt or a Finder
  # extension can keep a short-lived `git` around, and none of them is holding
  # *this* repository. What matters is whether a git process has this repo open.
  repo="${0:A:h}"
  print "git processes on this machine:"
  ps -Ao pid,etime,command | grep '[g]it' || print "  (none)"
  print ""

  holders=$(ps -Ao pid,command | grep '[g]it' | grep -F "$repo" | grep -v commit_and_push || true)
  if [[ -n "$holders" ]]; then
    print -u2 "A git process has this repository open:"
    print -u2 "$holders"
    print -u2 "Those locks are real. Wait for it to finish and run this again."
    exit 1
  fi

  # A real lock is held for a fraction of a second. One that has been sitting
  # there for minutes with nothing holding the repo is the corpse of a process
  # that died mid-command — here, a git run in a sandbox that cannot unlink it
  # afterwards because the file belongs to this account.
  now=$(date +%s)
  for lock in $locks; do
    age=$(( now - $(stat -f %m "$lock") ))
    if (( age < 60 )); then
      print -u2 "${lock} is ${age}s old — too new to call dead."
      print -u2 "Run this again in a minute."
      exit 1
    fi
  done

  for lock in $locks; do
    print "Removing stale ${lock} (nothing has this repo open)."
    rm -f "$lock"
  done
  print ""
fi

branch=$(git rev-parse --abbrev-ref HEAD)
print "Branch: ${branch}"
print ""

git add -A

if git diff --cached --quiet; then
  print "Nothing staged — the tree was already committed."
else
  print "Staged:"
  git diff --cached --name-status
  print ""
  git commit -F - <<'MSG'
feat(undx): make the completed-write receipt name what it changed

Found on an iPhone 17 Pro Max simulator against a local backend, not by
reading. A confirmation card correctly said

    Pause one crypto alert so it stops triggering
    BTC alert · above · 999,999: active → paused

and one tap later the receipt for that same write said

    Done — the current value is paused, and I read it back from PulseSoc
    to confirm it.

Both sentences are true; only one is checkable. Someone holding four alerts
reads the second and cannot tell which of the four moved, which leaves the
receipt unable to do the one job a receipt has. Batch 16 taught the card to
name its subject. The sentence on the next screen never learned it.

Cause: crypto.alerts.pause/resume declare no verified_fields, and
crypto_alert_status returns a bare string as `observed`, so the loop in
_write_state_sentence finds nothing and falls to its last generic branch.

  * describe_alert and format_amount move to services/undx_agent_contracts.py,
    the only module the runtime, the verifier and the prose layer can all
    import. Response intelligence cannot import the runtime, and a second copy
    of the composition would let the card and the receipt word the same row
    differently — which is exactly the comparison describe_alert exists to keep
    fair. undx_agent_runtime re-exports it, and keeps `_amount` for its
    existing call sites.
  * undx_verification.crypto_alert_status publishes evidence["subject"], built
    from the row it just read back and scoped to the calling user.
  * _write_state_sentence renders that subject when it is present, and keeps its
    old wording verbatim when it is not. A verifier that read no record has
    withheld nothing, so nothing is invented.

The subject travels on the verification evidence rather than being composed in
the renderer, and that is load-bearing: validate_consistency discards, silently
and totally, any sentence containing a digit absent from plan.allowed_numbers,
and _allowed_numbers builds that set by scraping verification.evidence. A label
like "BTC alert · above · 999,999" carries digits. Composed in the prose layer,
every named receipt would have been thrown away and replaced by the last-resort
line — a worse outcome than the defect being fixed.
tests/undx_agent/test_receipt_names_subject.py asserts that rather than
assuming it, along with the identity of the shared function, the user scoping of
the read-back, and the two screens agreeing word for word.

Proven on the device after restarting the backend against the fixed code:
"pause my btc alert" now returns "I confirmed this against your account after
the change: BTC alert · above · 999,999 is now paused.", with alert_rules id 29
paused at 2026-07-30T23:00:48 and id 30 untouched.

Also included, all of it evidence or the tooling that produced it:

  * reports/live_simulator_batch16_18.md — the live runs for Batches 16, 17 and
    18, each read back against the database, including the excluded-row proof
    for both spellings of "don't".
  * reports/app_backend_link_diagnosis.txt and diagnose_app_backend_link.command
    — why an entire earlier session of "UNDX is currently read-only" was
    production answering, not the local server whose flags were being edited.
  * restart_undx_live_backend.command — now compares the pid it started against
    the pid reported by /health/undx, so a stale server that survived the kill
    cannot answer while every flag printed belongs to a process nobody is
    talking to.
  * restart_metro_local_backend.command, restart_pulsesoc_dev_app.command —
    the Expo dev client has no main.jsbundle, so EXPO_PUBLIC_PULSE_API_BASE_URL
    is a property of the shell that starts Metro.
  * bot.py — /health/undx, which is what makes the pid comparison above possible.

Full UNDX suite: 653 tests, OK.
MSG
  print ""
  print "Committed:"
  git log --oneline -1
  print ""
fi

print "Unpushed commits:"
git log --oneline "origin/${branch}..HEAD" 2>/dev/null || print "  (no remote-tracking branch yet)"
print ""

print "Pushing to origin/${branch}..."
git push origin "${branch}"

print ""
print "=============================================="
print "Pushed. Remote is now:"
git log --oneline -1 "origin/${branch}"
print "=============================================="
print ""
print "You can close this window."
