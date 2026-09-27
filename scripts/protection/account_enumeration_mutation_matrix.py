#!/usr/bin/env python3
"""Prove each account-enumeration control is observed, by deleting it.

The suite for this work is green. That says the tests pass; it does not say they
would notice if the control they are named after were removed. On an oracle the
difference matters more than usual, because the failure is silent by
construction: an endpoint that leaks account existence returns 200 and looks
perfectly healthy, and the tests that "cover" it can pass while asserting
nothing that a leak would violate.

So each entry below edits one control out of the source, runs the suites that
claim to watch it, and asserts they go red. A mutation that survives is reported
as SURVIVED and fails this script's exit code.

Two mutations are here for the opposite reason from the rest: `confirmed-dropped`
and `admin-loses-privileged` remove *capability* rather than protection. A leak
is trivially closed by breaking the feature, and a suite that only asserts
"nothing leaks" would rate that a success. Those two prove the tests also notice
when the fix goes too far.

What this does not claim: killing a mutant shows the control is observed, not
that it is correct. A test pinning the wrong behaviour still goes red when the
wrong behaviour is removed.

Usage:  python3 scripts/protection/account_enumeration_mutation_matrix.py [--verbose]
Exit 0 only when every mutation is killed.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
BOT = "bot.py"
GUARD = "services/auth_subject_guard.py"
SUITE = "tests/test_account_enumeration_guard.py"

# Suites are named narrowly on purpose. Pointing a mutation at the whole tests/
# directory would let an unrelated test take credit for killing it, which is the
# same blind spot one layer up.
MUTATIONS = [
    # --- the guard table and the hook -------------------------------------
    dict(
        name="confirmation-status-dropped-from-the-table",
        control="The enumeration oracle is bounded at all.",
        path=BOT,
        old='    "/api/mobile/auth/confirmation-status": ("confirmation-status", ("email",), 8, 900),\n',
        new="",
        suites=[SUITE],
    ),
    dict(
        name="guard-watches-post-only",
        control=(
            "GET is guarded. basic_abuse_guard is POST/PUT only, and this endpoint "
            "answers GET, which is how the oracle stayed unbounded by three limiters "
            "at once."
        ),
        path=BOT,
        old='    if request.method not in {"GET", "POST", "PUT"}:\n        return None\n    scope, fields, limit, window_seconds = protected[request.path]',
        new='    if request.method not in {"POST", "PUT"}:\n        return None\n    scope, fields, limit, window_seconds = protected[request.path]',
        suites=[SUITE],
    ),
    dict(
        name="aliases-get-separate-scopes",
        control=(
            "/api/mobile and /api/pulse/mobile are one handler mounted twice, so they "
            "share one budget. Keying by path instead of by scope hands an enumerator "
            "a second budget for free, spelled into the URL."
        ),
        path=BOT,
        old='        f"{scope}:{actor}" if actor else "", subject, limit, window_seconds)',
        new='        f"{request.path}:{actor}" if actor else "", subject, limit, window_seconds)',
        suites=[SUITE],
    ),
    dict(
        name="every-endpoint-shares-one-scope",
        control=(
            "The mirror of the above, and what actually shipped first: keyed by actor "
            "alone, honest /confirmation-status polling spends /resend-confirmation's "
            "smaller budget and refuses a resend nobody asked for twice."
        ),
        path=BOT,
        old='        f"{scope}:{actor}" if actor else "", subject, limit, window_seconds)',
        new='        actor, subject, limit, window_seconds)',
        suites=[SUITE],
    ),
    dict(
        name="alarm-written-per-refused-request",
        control=(
            "One security_events row per actor per window. A DB write on the refusal "
            "path makes the guard an amplifier against an 8+8 pool for exactly the "
            "traffic it refuses."
        ),
        path=BOT,
        old="    if first_refusal is None:\n        security_monitor.record(",
        new="    if True:\n        security_monitor.record(",
        suites=[SUITE],
    ),

    # --- the confirmation-status response ---------------------------------
    dict(
        name="exists-restored",
        control=(
            "`exists` is gone. It answered the existence question for *unconfirmed* "
            "accounts too -- strictly more than `confirmed` can -- through a field no "
            "client ever read."
        ),
        path=BOT,
        old='        "ok": True,\n        "confirmed": confirmed,',
        new='        "ok": True,\n        "exists": bool(user),\n        "confirmed": confirmed,',
        suites=[SUITE],
    ),
    dict(
        name="confirmed-dropped",
        control=(
            "CAPABILITY, not protection: the signup screen exists to wait for this "
            "field. Closing a leak by breaking the feature must not read as success."
        ),
        path=BOT,
        old='        "ok": True,\n        "confirmed": confirmed,',
        new='        "ok": True,',
        suites=[SUITE],
    ),

    # --- the resend collapse ----------------------------------------------
    dict(
        name="unknown-address-answered-differently",
        control="An unknown address is indistinguishable from a known one.",
        path=BOT,
        old="    user = load_account_by_email(email)\n    if not user or int(user.get(\"email_verified\") or 0):\n        return dict(neutral)",
        new="    user = load_account_by_email(email)\n    if not user:\n        return {\"ok\": True, \"message\": \"If that account exists and still needs confirmation, PulseSoc will send a confirmation email.\", \"status\": 200}\n    if int(user.get(\"email_verified\") or 0):\n        return dict(neutral)",
        suites=[SUITE],
    ),
    dict(
        name="verified-account-announces-itself",
        control='"This email is already confirmed. You can log in." is the answer to the enumeration question.',
        path=BOT,
        old="    if not user or int(user.get(\"email_verified\") or 0):\n        return dict(neutral)",
        new="    if not user:\n        return dict(neutral)\n    if int(user.get(\"email_verified\") or 0):\n        return {\"ok\": True, \"message\": \"This email is already confirmed. You can log in.\", \"status\": 200}",
        suites=[SUITE],
    ),
    dict(
        name="delivery-failure-leaks-through-the-status",
        control=(
            "Only an existing unverified account can produce a send failure, so a 502 "
            "answers the same question as the message beside it."
        ),
        path=BOT,
        old="    if not privileged:\n        # The delivery failure is real and worth acting on",
        new="    if False:\n        # The delivery failure is real and worth acting on",
        suites=[SUITE],
    ),
    dict(
        name="trace-id-passed-through",
        control="trace_id was present only when a send was attempted -- the oracle in a field nothing reads.",
        path=BOT,
        old='        "trace_id": "",\n    }), int(result.get("status")',
        new='        "trace_id": result.get("trace_id"),\n    }), int(result.get("status")',
        suites=[SUITE],
    ),
    dict(
        name="privileged-defaults-to-open",
        control="Public callers get the neutral answer by default; only the admin console opts out.",
        path=BOT,
        old='def resend_account_confirmation_by_email(email, source="login", *, privileged=False):',
        new='def resend_account_confirmation_by_email(email, source="login", *, privileged=True):',
        suites=[SUITE],
    ),
    dict(
        name="admin-loses-privileged",
        control=(
            "CAPABILITY, not protection: the admin audit row needs the real outcome and "
            "trace id, and losing them fails silently."
        ),
        path=BOT,
        old='source="admin_email_page", privileged=True)',
        new='source="admin_email_page")',
        suites=[SUITE],
    ),
    dict(
        name="per-address-mail-cap-removed",
        control="Unbounded confirmation mail to one address, from any number of callers.",
        path=BOT,
        old="        capped = auth_subject_guard.subject_event_refused(email, limit, window_seconds)",
        new="        capped = None",
        suites=[SUITE],
    ),
    dict(
        name="throttled-caller-told-so",
        control=(
            "A distinguishable throttle response reintroduces the oracle by a side "
            "door: only a real unverified account can accumulate sends."
        ),
        path=BOT,
        old="            return dict(neutral)\n    user = load_account_by_email(email)",
        new='            return {"ok": False, "message": "Too many confirmation emails for this address.", "status": 429}\n    user = load_account_by_email(email)',
        suites=[SUITE],
    ),

    # --- the primitive ----------------------------------------------------
    dict(
        name="refused-subject-recorded",
        control=(
            "A refused subject is not recorded. Recording it lets a prober hold its own "
            "oldest entry alive by retrying, so the window never drains and whoever "
            "shares that address is blocked permanently."
        ),
        path=GUARD,
        old="        _SUBJECTS_BY_ACTOR[actor] = seen\n        oldest = min(seen.values())",
        new="        seen[token] = now\n        _SUBJECTS_BY_ACTOR[actor] = seen\n        oldest = min(seen.values())",
        suites=[SUITE],
    ),
    dict(
        name="subject-not-normalized",
        control=(
            "Case and whitespace fold to one subject. Otherwise eight spellings of one "
            "address are eight budget slots and the cap is decorative."
        ),
        path=GUARD,
        old='    normalized = str(subject or "").strip().lower()',
        new='    normalized = str(subject or "")',
        suites=[SUITE],
    ),
    # The first version of this entry deleted `not subject` from
    # distinct_subject_refused's early return and SURVIVED -- because the token
    # check two lines below caught it anyway. That was a finding about the
    # source, not the tests: the condition was redundant. It is now a single
    # `_absent(token)` check with `subject_token` as the one place an empty
    # subject is recognised, and the mutations target both real points.
    dict(
        name="empty-subject-gets-a-shared-bucket",
        control=(
            "An unparseable subject is not counted. Filing them all under one key lets "
            "eight free junk requests refuse the ninth honest one."
        ),
        path=GUARD,
        old='    normalized = str(subject or "").strip().lower()\n    if not normalized:\n        return ""',
        new='    normalized = str(subject or "").strip().lower()',
        suites=[SUITE],
    ),
    dict(
        name="absent-actor-gets-a-shared-bucket",
        control=(
            "client_ip_hash() returns \"\" when it cannot resolve a source. Counting "
            "those together means eight unattributable requests refuse the ninth real one."
        ),
        path=GUARD,
        old="    if _absent(actor) or _absent(token):",
        new="    if _absent(token):",
        suites=[SUITE],
    ),
    dict(
        name="subject-stored-in-clear",
        control="The in-memory tables are not a list of the addresses that recently touched the system.",
        path=GUARD,
        old='    return hmac.new(_SALT, normalized.encode("utf-8"), hashlib.sha256).hexdigest()',
        new="    return normalized",
        suites=[SUITE],
    ),
    dict(
        name="subject-hashed-without-the-salt",
        control=(
            "Salted, not a bare digest. An unsalted hash of an email address is "
            "reversible with a wordlist -- the same reason an unset ANALYTICS_SALT "
            "makes client_ip_hash reversible today."
        ),
        path=GUARD,
        old='    return hmac.new(_SALT, normalized.encode("utf-8"), hashlib.sha256).hexdigest()',
        new='    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()',
        suites=[SUITE],
    ),
    dict(
        name="distinct-cap-off-by-one",
        control="The limit is the limit. `>` admits one more subject than the table says.",
        path=GUARD,
        old="    if token not in seen and len(seen) >= max(1, int(limit)):",
        new="    if token not in seen and len(seen) > max(1, int(limit)):",
        suites=[SUITE],
    ),
    dict(
        name="actors-share-one-budget",
        control="One client's probing does not spend another client's budget.",
        path=GUARD,
        old="    seen = {\n        held: stamp\n        for held, stamp in _SUBJECTS_BY_ACTOR.get(actor, {}).items()",
        new='    seen = {\n        held: stamp\n        for held, stamp in _SUBJECTS_BY_ACTOR.get("all", {}).items()',
        suites=[SUITE],
    ),
    dict(
        name="mail-cap-window-never-drains",
        control="Expired sends stop counting, so a legitimate user is not locked out for good.",
        path=GUARD,
        old="    stamps = [stamp for stamp in _EVENTS_BY_SUBJECT.get(token, []) if now - stamp < window]",
        new="    stamps = list(_EVENTS_BY_SUBJECT.get(token, []))",
        suites=[SUITE],
    ),
    dict(
        name="no-memory-reclaim",
        control=(
            "Both tables are keyed by attacker-supplied values, so a table that only "
            "grows is a memory-exhaustion vector handed to the traffic being refused."
        ),
        path=GUARD,
        old='    _sweep("auth_subject_guard.events", _EVENTS_BY_SUBJECT, window, now, max)',
        new="",
        suites=[SUITE],
    ),
]


def sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_suites(suites, verbose):
    """Red if any suite fails. One process per file: these share module-level DB
    and limiter state, and batching produces failures that belong to the batching."""
    for suite in suites:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", suite, "-q", "--no-header", "-x",
             "-p", "no:cacheprovider"],
            cwd=ROOT, capture_output=True, text=True,
            env={**os.environ, "PYTHONPATH": str(ROOT)},
        )
        tail = [line for line in proc.stdout.splitlines()
                if line.startswith("FAILED") or " passed" in line or " failed" in line]
        if verbose:
            print(f"      {suite}: exit {proc.returncode} | {tail[-1] if tail else '?'}")
        if proc.returncode != 0:
            return False, suite, tail[-1] if tail else ""
    return True, None, ""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    touched = sorted({m["path"] for m in MUTATIONS})
    baseline_hash = {rel: sha256(ROOT / rel) for rel in touched}

    print("Baseline: suites must be green before a mutation means anything.\n")
    baseline = sorted({suite for m in MUTATIONS for suite in m["suites"]})
    green, suite, tail = run_suites(baseline, args.verbose)
    if not green:
        print(f"ABORT: {suite} is already failing ({tail}). Fix that first -- a red\n"
              f"baseline makes every mutation look killed.")
        return 2
    print(f"  {len(baseline)} suite(s) green.\n")

    survivors = []
    for mutation in MUTATIONS:
        path = ROOT / mutation["path"]
        original = path.read_text(encoding="utf-8")
        occurrences = original.count(mutation["old"])
        if occurrences == 0:
            print(f"  DRIFTED  {mutation['name']}")
            print(f"           anchor no longer present in {mutation['path']}. The control")
            print(f"           may have moved or been rewritten; re-point this entry before")
            print(f"           it can claim anything.")
            survivors.append(mutation["name"])
            continue
        if occurrences != 1:
            print(f"  AMBIGUOUS {mutation['name']}: anchor appears {occurrences} times.")
            survivors.append(mutation["name"])
            continue
        path.write_text(original.replace(mutation["old"], mutation["new"], 1), encoding="utf-8")
        try:
            still_green, _, _ = run_suites(mutation["suites"], args.verbose)
        finally:
            path.write_text(original, encoding="utf-8")
            # Verify the restore rather than trust it. This harness edits real
            # source in the working tree; a restore that silently failed would
            # leave a mutation committed, and it would be one deliberately
            # written to keep the suite green. Recorded here and acted on after
            # the `finally` -- returning from inside one swallows any in-flight
            # exception, which would hide the reason the restore was reached.
            restored = sha256(path)
        if restored != baseline_hash[mutation["path"]]:
            print(f"\nFATAL: restore of {mutation['path']} did not reproduce the")
            print(f"       baseline ({restored[:12]} != {baseline_hash[mutation['path']][:12]}).")
            print(f"       The working tree is dirty with a mutation. Fix before committing.")
            return 3
        if still_green:
            print(f"  SURVIVED {mutation['name']}")
            print(f"           {mutation['control']}")
            print(f"           Removing it changed no test result. Nothing observes this.")
            survivors.append(mutation["name"])
        else:
            print(f"  killed   {mutation['name']}")

    for rel in touched:
        if sha256(ROOT / rel) != baseline_hash[rel]:
            print(f"\nFATAL: {rel} is not byte-identical to the baseline after the run.")
            return 3
    print(f"\nall {len(touched)} touched file(s) byte-identical to baseline")

    if survivors:
        print(f"FAIL: {len(survivors)} of {len(MUTATIONS)} mutations survived: "
              f"{', '.join(survivors)}")
        return 1
    print(f"PASS: all {len(MUTATIONS)} mutations killed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
