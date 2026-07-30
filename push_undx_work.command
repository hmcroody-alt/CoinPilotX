#!/bin/zsh
#
# Push the committed UNDX work to origin.
#
# Double-click this file in Finder.
#
# Why this exists: the assistant's shell runs in an isolated Linux sandbox that
# has the repo mounted but no route to github.com — both git@github.com:22 and
# https://github.com:443 are refused by the sandbox proxy. The commit can be
# made from there; the push cannot. This script runs the push on the Mac, where
# the SSH key and network actually are.
#
# It pushes only the current branch, and only if there is nothing uncommitted
# left behind, so a half-finished tree is never published as if it were whole.

set -e
cd "${0:A:h}"

print "=============================================="
print " push_undx_work"
print "=============================================="

branch=$(git rev-parse --abbrev-ref HEAD)
print "Branch:  ${branch}"
print "HEAD:    $(git log --oneline -1)"
print ""

# A dirty tree here does not block the push — the commit already made is still
# worth publishing — but it must be visible, because the usual cause is that
# something was edited after the commit and is about to be silently left behind.
dirty=$(git status --porcelain=v1)
if [[ -n "$dirty" ]]; then
  print "NOTE: uncommitted changes are present and will NOT be pushed:"
  print "$dirty"
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
