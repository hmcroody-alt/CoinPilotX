#!/usr/bin/env python3
"""Twenty ways to break the live-ban authority. Each must turn the suite red.

A green suite proves nothing on its own. This table shipped with six
authorization readers, zero rows, and a passing test suite for its entire
life — the tests asserted the source text of the checks, which stayed true
while the authority behind them did not exist. So before trusting the new
tests, break the authority on purpose and confirm each break is caught.

Each mutation below is a plausible refactor or a plausible mistake, not a
syntactic scribble: delegating to the wrong helper, inverting a fail-closed
return, dropping a self-ban guard, narrowing a vocabulary, unbanning one row
instead of all, renaming a denial event to something that means a different
thing. If any survives, the corresponding property is not actually tested and
the gap is named in the output rather than glossed over.

Three delete the ban check at one boundary each, on the three co-host paths.
Stubbing the shared reader is not enough on its own: with four boundaries still
honouring the ban, a suite that only drives the audience gate stays green while
a banned account walks onto the stage. These three fail unless each promotion
path is actually driven end to end.

Three of them attack the observability rather than the decision, because an
authority nobody can prove fired is only half built -- and one of those three
attacks it from the other side, firing the ban event for every audience miss.
An event that is emitted for all denials identifies none of them.

One reorders the handler rather than changing any of its logic, because the
no-enumeration-oracle property is the only one here that depends purely on the
order two correct checks run in -- and the source does not look wrong either way.

Two attack the audit trail, which is the part most likely to rot unnoticed:
nothing in the product reads it back, so dropping the write or widening it to
carry the private moderator note both leave every user-visible behaviour intact.

The last one is not an attack on this branch's code at all. It makes the global
abuse limiter key its buckets on the moderator instead of the request path --
the shape a plausible "tidy the limiter keys" hardening change would take -- and
must be caught, because it would silently take away a host's ability to clear a
raid of more than twenty-four accounts.

Edits a scratch copy of the repository. Nothing is written to the working
tree. Run:  python3 scripts/protection/mutate_live_moderation_authority.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

SERVICE = "services/live_moderation.py"
BOT = "bot.py"
SECURITY = "services/pulse_security_core.py"

#: (name, file, find, replace, why this mutation is worth testing)
MUTATIONS = [
    (
        "read_failure_fails_open",
        SERVICE,
        "            live_id, target_user_id, exc.__class__.__name__, exc,\n        )\n        return True",
        "            live_id, target_user_id, exc.__class__.__name__, exc,\n        )\n        return False",
        "A read error answers 'not banned'. This is the exact defect the "
        "database contract sentinel was built to find, now on a live access gate.",
    ),
    (
        "ban_is_not_idempotent",
        SERVICE,
        '    existing = active_ban(cur, live_id, target_user_id)\n    if existing:\n        return {"status": "already_active", "ban": existing}',
        "    existing = active_ban(cur, live_id, target_user_id)",
        "Every press of Ban writes another active row. Harmless until an unban "
        "clears only one of them.",
    ),
    (
        "unban_clears_only_one_row",
        SERVICE,
        '        "WHERE live_id=? AND target_user_id=? AND status=\'active\' "\n        f"  AND LOWER(COALESCE(action,\'\')) IN {_BAN_ACTION_SQL}",\n        (STATUS_REVERSED, _now(), live_id, target_user_id),',
        '        "WHERE id=(SELECT id FROM pulse_live_moderation "\n        "          WHERE live_id=? AND target_user_id=? AND status=\'active\' LIMIT 1)",\n        (STATUS_REVERSED, _now(), live_id, target_user_id),',
        "Two moderators banning concurrently leave a viewer permanently banned "
        "that no amount of unbanning from the UI can release.",
    ),
    (
        "cohost_may_ban_the_host",
        SERVICE,
        '    if target_user_id == host_user_id:\n        return False, "cannot_moderate_host"\n',
        "",
        "A co-host can evict the owner of the broadcast they were invited onto.",
    ),
    (
        "a_host_can_ban_themselves",
        SERVICE,
        '    if target_user_id == actor_user_id:\n        return False, "cannot_moderate_self"\n',
        "",
        "A host locks themselves out of their own live through an ordinary button.",
    ),
    (
        "anyone_may_moderate",
        SERVICE,
        '    if not can_moderate(actor_role):\n        return False, "not_a_moderator"\n',
        "",
        "Any viewer can ban any other viewer.",
    ),
    (
        "legacy_ban_spellings_stop_counting",
        SERVICE,
        "_BAN_ACTION_SQL = \"('block', 'blocked', 'ban', 'banned')\"",
        "_BAN_ACTION_SQL = \"('ban')\"",
        "Narrowing the reader to the spelling we write silently un-bans every "
        "row written by hand or by any other caller.",
    ),
    (
        "the_moderator_note_is_unbounded",
        SERVICE,
        '    text = text.replace("\\r", " ").replace("\\n", " ")\n    return text[:REASON_MAX]',
        "    return text",
        "An unbounded, newline-carrying note reaches a security table and every "
        "log line that renders it.",
    ),
    (
        "the_enforcement_reader_stops_delegating",
        BOT,
        "    return live_moderation.is_banned(cur, live_id, user_id)",
        "    return False",
        "All six enforcement sites answer 'not banned' forever. This is the "
        "original production state, reintroduced.",
    ),
    (
        "the_stage_request_stops_checking_the_ban",
        BOT,
        '        if pulse_live_user_is_blocked(cur, live_id, user["user_id"]):',
        "        if False:",
        "A banned viewer asks to join the stage and is allowed to. Deleting one "
        "boundary's check is the mutation the shared-reader one above cannot "
        "catch, because the other four still delegate and the suite stays green "
        "on all of them.",
    ),
    (
        "the_invite_stops_checking_the_ban",
        BOT,
        "        if pulse_live_user_is_blocked(cur, live_id, target_user_id):\n"
        "            conn.close()\n"
        '            return pulse_live_cohost_error("BLOCKED_BY_HOST", status=403, step="invite_target_validation",',
        "        if False:\n"
        "            conn.close()\n"
        '            return pulse_live_cohost_error("BLOCKED_BY_HOST", status=403, step="invite_target_validation",',
        "A co-host invites the account the host just removed, and the stage "
        "accepts them. The ban is still in the table and every other boundary "
        "still honours it, so the moderation UI reports success throughout.",
    ),
    (
        "the_invite_answer_stops_checking_the_ban",
        BOT,
        "        if pulse_live_user_is_blocked(cur, live_id, target_user_id):\n"
        "            conn.close()\n"
        '            return pulse_live_cohost_error("BLOCKED_BY_HOST", status=403, trace_id=trace_id,',
        "        if False:\n"
        "            conn.close()\n"
        '            return pulse_live_cohost_error("BLOCKED_BY_HOST", status=403, trace_id=trace_id,',
        "An invite issued before the ban is answered after it. Nothing sweeps "
        "outstanding invites when a ban lands, so this re-read is the only "
        "thing standing between a removed account and a microphone.",
    ),
    (
        "the_join_boundary_stops_naming_the_ban",
        BOT,
        '        if viewer_reason == PULSE_LIVE_BANNED_REASON:\n            # A ban is the one denial here',
        '        if False:\n            # A ban is the one denial here',
        "A banned viewer is refused at join and nothing records it, so there is "
        "no way to show from the logs that a moderator's decision took effect.",
    ),
    (
        "the_token_mint_reports_a_ban_as_an_audience_miss",
        BOT,
        "                \"LIVE_TOKEN_DENIED_BANNED trace_id=%s live_id=%s user_id=%s requested_role=%s\",",
        "                \"LIVE_TOKEN_DENIED_FOLLOWERS trace_id=%s live_id=%s user_id=%s requested_role=%s\",",
        "The most important denial in the chain -- the token that buys access to "
        "the stream -- is filed under a name that means something else.",
    ),
    (
        "a_followers_only_miss_is_reported_as_a_ban",
        BOT,
        "        if viewer_reason == PULSE_LIVE_BANNED_REASON:\n            # The generic failure log below",
        "        if True:\n            # The generic failure log below",
        "Every audience miss is logged as a moderation ban. An event that fires "
        "for all denials identifies none of them, and it invents bans that a "
        "moderator never issued.",
    ),
    (
        "the_target_is_checked_before_the_actor",
        BOT,
        # The same helper is called by the read route, so the anchor has to
        # reach the line after it to stay unique.
        "        actor_role = pulse_live_moderation_actor_role(cur, live, user)\n"
        "        allowed, denial = live_moderation.authorize(",
        '        cur.execute("SELECT user_id FROM users WHERE user_id=? LIMIT 1", (target_user_id,))\n'
        "        if not cur.fetchone():\n"
        "            conn.close()\n"
        '            return api_error("That account could not be found.", 404)\n'
        "        actor_role = pulse_live_moderation_actor_role(cur, live, user)\n"
        "        allowed, denial = live_moderation.authorize(",
        "Validating the target before deciding whether the caller may act is the "
        "ordinary way to order a handler, and it turns this route into an account "
        "existence oracle for anybody with a session: 404 means that user id is "
        "free, 403 means somebody is there.",
    ),
    (
        "the_ban_stops_being_audited",
        BOT,
        '            pulse_live_audit(\n                cur, live_id, actor_user_id,\n                "viewer_ban_already_active" if already else "viewer_ban",\n                target_user_id=target_user_id,\n                metadata={"has_reason": bool(reason)},\n            )\n',
        "",
        "Bans still work and nothing user-visible changes, so this is the one "
        "mutation that could ship unnoticed -- and it leaves no record of who "
        "removed whom, which is the whole point of a moderation audit trail.",
    ),
    (
        "the_audit_row_carries_the_private_note",
        BOT,
        '                metadata={"has_reason": bool(reason)},',
        '                metadata={"has_reason": bool(reason), "reason": reason},',
        "The natural thing to put in audit metadata is the reason itself. That "
        "copies a private trust & safety note into a second table and into every "
        "log line that renders the audit row.",
    ),
    (
        "the_abuse_limiter_is_keyed_on_the_moderator",
        SECURITY,
        '        f"ip:{ip_hash}:{rule.action}:{path}",\n        f"user:{int(user_id or 0)}:{rule.action}:{path}" if user_id else "",\n        f"device:{device_hash}:{rule.action}:{path}" if device_hash else "",',
        '        f"ip:{ip_hash}:{rule.action}",\n        f"user:{int(user_id or 0)}:{rule.action}" if user_id else "",\n        f"device:{device_hash}:{rule.action}" if device_hash else "",',
        "Reads as a hardening change -- one bucket per actor instead of one per "
        "URL -- and takes away a host's ability to ban more than 24 accounts in "
        "five minutes, which is exactly the situation the button exists for.",
    ),
    (
        "the_route_skips_authorization",
        BOT,
        '        if not allowed:\n            logging.warning(\n                "LIVE_MODERATION_DENIED',
        '        if False:\n            logging.warning(\n                "LIVE_MODERATION_DENIED',
        "The authority writes without consulting its own authorization. The "
        "route still 200s and the UI still looks correct.",
    ),
]

TESTS = [
    "tests/test_live_moderation_authority_behavior.py",
    "tests/protection/test_live_moderation_authority.py",
]


def _run_tests(workdir: Path) -> tuple[bool, str]:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", *TESTS, "-x", "-q", "-p", "no:warnings"],
        cwd=workdir,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    return proc.returncode == 0, (proc.stdout + proc.stderr).strip().splitlines()[-1:]


def main() -> int:
    scratch = Path(tempfile.mkdtemp(prefix="live_moderation_mutants_"))
    workdir = scratch / "repo"
    print(f"copying the repository to {workdir} (nothing is written to the working tree)")
    shutil.copytree(
        ROOT, workdir,
        ignore=shutil.ignore_patterns(
            ".git", "node_modules", "*.db", "__pycache__", ".venv",
            "ios", "android", ".fuse_hidden*",
        ),
    )

    baseline_green, baseline_tail = _run_tests(workdir)
    if not baseline_green:
        print(f"BASELINE IS RED -- mutation results would be meaningless: {baseline_tail}")
        shutil.rmtree(scratch, ignore_errors=True)
        return 2
    print(f"baseline: GREEN {baseline_tail}\n")

    survivors = []
    for name, rel, find, replace, why in MUTATIONS:
        target = workdir / rel
        original = target.read_text(encoding="utf-8")
        occurrences = original.count(find)
        if occurrences == 0:
            print(f"SKIP  {name}: the text this mutation edits no longer exists in {rel}")
            survivors.append((name, "mutation is stale", why))
            continue
        if occurrences > 1:
            # A non-unique anchor is the worst possible failure here: the edit
            # lands somewhere else in the file, the suite stays green for an
            # honest reason, and the report calls a tested property untested.
            # This cost one false "SURVIVED" before it was caught.
            print(f"SKIP  {name}: anchor matches {occurrences} places in {rel}")
            survivors.append((name, f"anchor is not unique ({occurrences} matches)", why))
            continue
        target.write_text(original.replace(find, replace, 1), encoding="utf-8")
        try:
            green, tail = _run_tests(workdir)
        finally:
            target.write_text(original, encoding="utf-8")
        if green:
            print(f"SURVIVED  {name}")
            survivors.append((name, "suite stayed green", why))
        else:
            print(f"caught    {name}")

    print()
    if survivors:
        print(f"{len(survivors)} of {len(MUTATIONS)} mutations were not caught:")
        for name, how, why in survivors:
            print(f"  - {name} ({how})\n      {why}")
        shutil.rmtree(scratch, ignore_errors=True)
        return 1
    print(f"all {len(MUTATIONS)} mutations turned the suite red")
    shutil.rmtree(scratch, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
