"""Mutation check for Sentinel's request-path controls (Stage 30 discipline).

A passing security test suite proves nothing on its own — it proves the tests
run, not that they can fail. This breaks each control in ``request_bridge.py``
and ``rate_limit.py`` one at a time and requires the suite to notice. A
surviving mutant names a security property that is described in prose and
defended by nothing.

Two rules this harness enforces on itself, both learned the hard way:

* **Every mutation asserts its anchor matches before it is applied.** A pattern
  that silently matches nothing produces a no-op mutation, the suite passes,
  and the report says "SURVIVED" — indistinguishable from a genuinely untested
  property. That false positive already happened once, in Stage 2, and it
  pointed at a *credential-scrubbing* test. Same failure class as a pipeline
  that masks a command's exit status: a check that reports success because it
  quietly did nothing.
* **Equivalent mutants are marked, not deleted.** See M12 — mutating one
  ``except`` clause when the next one has an identical body changes no
  behaviour, so no test can possibly kill it. The honest fix is a mutation that
  actually removes the property, not a quieter report.

Run: python3 scripts/sentinel_mutation_check.py
Exit code 1 means at least one property is untested.
"""
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
PYTHON = os.environ.get("SENTINEL_PYTHON", sys.executable)


def pytest_for(test_path):
    return [PYTHON, "-m", "pytest", test_path, "-q", "--no-header", "-x"]


BRIDGE_TARGET = ROOT / "services/sentinel/request_bridge.py"
BRIDGE_TESTS = "tests/sentinel/test_request_bridge.py"

RATE_TARGET = ROOT / "services/sentinel/rate_limit.py"
RATE_TESTS = "tests/sentinel/test_rate_limit.py"

BRIDGE_MUTANTS = [
    ("M1 default-OFF becomes default-ON", [('return str(os.getenv("SENTINEL_REQUEST_BRIDGE_ENABLED", "")).strip().lower() in _TRUTHY', 'return True')]),

    ("M2 emit stops gating on a verified schema", [("        if not bootstrap.schema_ready():", "        if False:")]),

    ("M3 emit stops honouring the ingest kill switch", [("        if not killswitches.ingest_enabled():\n            return False\n        if not bootstrap.schema_ready():", "        if False:\n            return False\n        if not bootstrap.schema_ready():")]),

    ("M4 flush drains through a killed ingest", [("    if not killswitches.ingest_enabled():", "    if False:")]),

    ("M5 per-request dedupe key reverts to the one-second default", [('        dedupe_key=f"reqbridge:{uuid.uuid4()}",', '        dedupe_key="",')]),

    ("M6 buffer overflow stops counting drops", [('                _BUFFER.popleft()\n                _STATS["dropped"] += 1', '                _BUFFER.popleft()')]),

    ("M7 a lost flush batch stops counting as lost", [('        with _LOCK:\n            _STATS["dropped"] += len(batch)\n        return 0', '        return 0')]),

    ("M8 evidence_complete always claims completeness", [('    snapshot["evidence_complete"] = snapshot["dropped"] == 0', '    snapshot["evidence_complete"] = True')]),

    ("M9 query strings are kept in the normalised path", [('    raw = str(path or "").split("?", 1)[0]', '    raw = str(path or "")')]),

    ("M10 unattributed callers become SYSTEM actors", [('        actor_id = f"device:{ip_hash[:32]}"\n        actor_type = "DEVICE"', '        actor_id = f"device:{ip_hash[:32]}"\n        actor_type = "SYSTEM"')]),

    ("M11 emit reaches the database on the request path", [("        _ensure_worker()", "        store.platform_db.connect()\n        _ensure_worker()")]),

    # NOTE: mutating only `except events.EventRejected:` is an EQUIVALENT
    # mutant — the generic `except Exception:` below it has an identical body,
    # so the batch survives either way and no test can tell them apart. The
    # property actually worth defending is "one bad event does not cost the
    # batch", and removing it takes both handlers. Hence the pair form.
    ("M12 one bad event takes the whole batch down", [
        ("            except events.EventRejected:", "            except ZeroDivisionError:"),
        ("            except Exception:\n                rejected += 1",
         "            except FloatingPointError:\n                rejected += 1"),
    ]),
]

# The read-modify-write form of the shared counter. This is not a hypothetical:
# it is the implementation the module exists to avoid, and R7 below installs it
# verbatim to prove the concurrency test can tell the difference.
_RACING_UPSERT = """        cur.execute("SELECT hits FROM sentinel_rate_counters WHERE scope = ? AND subject = ? AND window_start = ?", (scope, subject, window_start))
        _r = cur.fetchone()
        if _r is None:
            cur.execute("INSERT INTO sentinel_rate_counters (scope, subject, window_start, hits, updated_at) VALUES (?, ?, ?, ?, ?)", (scope, subject, window_start, cost, stamp))
            current = cost
        else:
            import time as _t; _t.sleep(0.001)
            current = int(_r[0]) + cost
            cur.execute("UPDATE sentinel_rate_counters SET hits = ? WHERE scope = ? AND subject = ? AND window_start = ?", (current, scope, subject, window_start))"""

RATE_MUTANTS = [
    ("R1 an unrecognised mode enforces instead of staying off",
     [("    return raw if raw in _MODES else MODE_OFF", "    return raw if raw in _MODES else MODE_ENFORCE")]),

    ("R2 shadow mode silently enforces",
     [("        enforced = active == MODE_ENFORCE", "        enforced = True")]),

    ("R3 a memory-only decision claims to be distributed",
     [("        distributed = False\n        degraded_error = \"\"", "        distributed = True\n        degraded_error = \"\"")]),

    ("R4 the local short circuit answers while under the limit",
     [("        if local_estimate > limit:", "        if True:")]),

    ("R5 the weighted window collapses to a plain fixed window",
     [("    weight = max(0.0, 1.0 - (elapsed / float(window_seconds)))", "    weight = 0.0")]),

    ("R6 the carried window never decays, so one burst blocks forever",
     [("    weight = max(0.0, 1.0 - (elapsed / float(window_seconds)))", "    weight = 1.0")]),

    ("R7 the atomic increment reverts to read-modify-write",
     [("""        cur.execute(_UPSERT_SQL,
                    (scope, subject, window_start, cost, stamp, cost, stamp))
        row = cur.fetchone()
        current = int(row[0]) if row else cost""", _RACING_UPSERT)]),

    ("R8 expired windows are never deleted",
     [("        cur.execute(_PRUNE_SQL, (cutoff,))", "        pass")]),

    ("R9 pruning runs on every single check",
     [("        if now - _LAST_PRUNE < PRUNE_INTERVAL_SECONDS:", "        if False:")]),

    ("R10 the process-local map grows without bound",
     [("        if len(_LOCAL) >= MAX_LOCAL_KEYS:", "        if False:")]),

    ("R11 degradation to process memory stops being counted",
     [('                with _LOCK:\n                    _STATS["degraded"] += 1', "                with _LOCK:\n                    pass")]),

    ("R12 a database failure fails open instead of degrading",
     [("                estimate = local_estimate\n                degraded_error =", "                estimate = 0\n                degraded_error =")]),

    ("R13 the emergency kill switch is ignored",
     [("    if killswitches.emergency_killed():", "    if False:")]),

    ("R14 the counter is never committed, so no other worker sees it",
     [("        conn.commit()\n        return current, previous", "        return current, previous")]),
]

# The wiring, not the module. Every mutant above can be killed by a module with
# no call sites — which is exactly the state the Stage 0 map found Sentinel in:
# 55 modules, ~11k lines, and `grep -c sentinel bot.py` returning 0. These break
# the connection between the counter and the route instead.
WIRING_TARGET = ROOT / "bot.py"
WIRING_TESTS = "tests/sentinel_integration/test_rate_guard_routes.py"

WIRING_MUTANTS = [
    ("W1 the guard stops consulting the shared counter at all",
     [("    refused = sentinel_rate_refused(request.path, limit, window_seconds)",
       "    refused = None")]),

    ("W2 a refusal is counted and then ignored",
     [("    if refused is not None:\n        logging.warning(",
       "    if False:\n        logging.warning(")]),

    ("W3 the limit passed to the counter stops matching the route's own",
     [("    refused = sentinel_rate_refused(request.path, limit, window_seconds)",
       "    refused = sentinel_rate_refused(request.path, limit * 1000, window_seconds)")]),

    ("W4 the subject becomes a constant, so every caller shares one bucket",
     [('            subject if subject is not None else f"ip:{client_ip_hash()}"',
       '            subject if subject is not None else "ip:shared"')]),

    ("W5 the distributed refusal answers in a body the shipped client never saw",
     [('        response = jsonify({"ok": False, "message": RATE_LIMIT_REFUSAL_MESSAGE})\n        response.status_code = 429',
       '        response = jsonify({"error": "rate_limited"})\n        response.status_code = 429')]),

    ("W6 the web form gets the API's JSON instead of its own shape",
     [('    if str(path or "").startswith("/api/"):', "    if True:")]),

    ("W7 a limiter failure escapes and 500s the route it protects",
     [('    except Exception:\n        # A limiter that can 500 the endpoint it protects has made the product\n        # less available, not more secure. rate_limit.stats() counts its own\n        # failures, so this is quiet rather than silent.\n        logging.exception("SENTINEL_RATE_GUARD_FAILED scope=%s", scope)\n        return None',
       '    except Exception:\n        raise')]),

    # NOTE: deleting the guard's own `if not _sentinel_rate.enabled(): return
    # None` is an EQUIVALENT mutant, and it is worth saying why rather than
    # quietly dropping it. That early return is a cost optimisation, not a
    # control: `rate_limit.check()` independently returns allowed-with-no-state
    # when the mode is off, so removing the guard's copy changes no response, no
    # counter and no row. The property "default OFF" is real and is defended —
    # by R1 and by test_off_costs_nothing, one layer down, where it is decidable.
    # A mutant that cannot change behaviour cannot be killed by any test, and
    # reporting it as SURVIVED would name a tested property as untested.

    ("W8 Retry-After stops reflecting the window the caller must actually wait",
     [("        return rate_limit_refusal(request.path, retry_after=refused.retry_after)",
       "        return rate_limit_refusal(request.path, retry_after=0)")]),
]

# Stage 5/29. These invariants are *detectors*, and a detector has a failure
# mode the controls above do not: it can report OK for a reason that has
# nothing to do with the property being true. T3 and T4 are the two that
# matter — T3 removes the actual cross-tenant comparison while leaving the
# query shape intact, and T4 turns "I could not look" into "all clear".
TENANT_TARGET = ROOT / "services/sentinel/invariants.py"
TENANT_TESTS = "tests/sentinel/test_tenant_isolation.py"

TENANT_MUTANTS = [
    ("T1 the roster-exists guard is dropped, so every orphan reads as a breach",
     [("""                   WHEN EXISTS (SELECT 1 FROM pulse_conversation_participants roster
                                WHERE roster.conversation_id = recent.cid)
                    AND NOT EXISTS""", "                   WHEN NOT EXISTS")]),

    # The one that would matter most in production: the query still runs, still
    # joins the roster, still returns a number — it just stops asking whether
    # THIS actor is in it. Every result goes green and nothing looks wrong.
    ("T2 the check stops comparing the actor, so any roster at all satisfies it",
     [("""                                    WHERE p.conversation_id = recent.cid
                                      AND p.user_id = recent.actor)""",
       "                                    WHERE p.conversation_id = recent.cid)")]),

    ("T3 the sense of the check inverts, so legitimate traffic is the violation",
     [("                    AND NOT EXISTS (SELECT 1 FROM pulse_conversation_participants p",
       "                    AND EXISTS (SELECT 1 FROM pulse_conversation_participants p")]),

    ("T4 an unreadable table reports all-clear instead of unknown",
     [("    except Exception:\n        return None, 0  # table missing / engine mismatch → SKIPPED",
       "    except Exception:\n        return 0, 0  # table missing / engine mismatch → SKIPPED")]),

    ("T5 a missing row count reports all-clear instead of unknown",
     [("    if not row:\n        return None, 0", "    if not row:\n        return 0, 0")]),

    ("T6 the table allowlist is bypassed, reopening SQL interpolation",
     [('    actor = _PARTICIPATION_SOURCES[table]  # KeyError = programming error, fail loud',
       '    actor = _PARTICIPATION_SOURCES.get(table, "user_id")')]),

    ("T7 the scan window collapses to a single row",
     [("TENANT_SCAN_LIMIT = 5000", "TENANT_SCAN_LIMIT = 1")]),

    ("T8 a cross-tenant read is filed as a generic violation, not a disclosure",
     [('    "INV_RECEIPT_READER_PARTICIPANT": (\n'
       '        _inv_receipt_reader_was_a_participant, "PRIVACY", "DATA_EXPOSURE"),',
       '    "INV_RECEIPT_READER_PARTICIPANT": (\n'
       '        _inv_receipt_reader_was_a_participant, "SECURITY", "INVARIANT_VIOLATION"),')]),

    ("T9 the OK line stops saying how much was examined",
     [('    return InvariantResult("INV_MESSAGE_SENDER_PARTICIPANT", STATUS_OK,\n'
       '                           f"{scanned} newest message(s) all written by a participant")',
       '    return InvariantResult("INV_MESSAGE_SENDER_PARTICIPANT", STATUS_OK,\n'
       '                           "no bypass detected")')]),

    ("T10 the invariant corrects the data instead of reporting it",
     [("    try:\n        cur.execute(sql)\n        row = cur.fetchone()",
       "    try:\n        cur.execute(f\"DELETE FROM {table} WHERE COALESCE(conversation_id,0) = 404\")\n"
       "        cur.execute(sql)\n        row = cur.fetchone()")]),
]

# Stage 5. These mutants break the *product's* access checks, not Sentinel's.
# That is the point: tests/sentinel_integration/test_object_authorization.py
# adds no control, it only claims the controls in bot.py are still there, and a
# claim like that is worth exactly as much as its ability to notice their
# absence. Every one of those 14 tests passed on the first run — which is how a
# regression harness looks both when it is real and when it is vacuous.
OBJAUTH_TARGET = ROOT / "bot.py"
OBJAUTH_TESTS = "tests/sentinel_integration/test_object_authorization.py"

_REACT_CHECK = ('    cur.execute("SELECT 1 FROM pulse_conversation_participants WHERE '
                'conversation_id=? AND user_id=? AND COALESCE(left_at,\'\')=\'\' LIMIT 1", '
                '(conversation_id, user["user_id"]))\n'
                '    if not cur.fetchone():\n'
                '        conn.close()\n'
                '        return api_error("Conversation not found.", 404, trace_id)')

OBJAUTH_MUTANTS = [
    ("O1 the conversation detail route stops checking membership",
     [("""        if not cur.fetchone():
            chat_health_service.record_trace(cur, user["user_id"], f"/api/pulse/messages/{conversation_id}", "forbidden", trace_id, {"conversation_id": conversation_id, "http_status": 403})""",
       """        if False:
            chat_health_service.record_trace(cur, user["user_id"], f"/api/pulse/messages/{conversation_id}", "forbidden", trace_id, {"conversation_id": conversation_id, "http_status": 403})""")]),

    ("O2 the react route stops checking membership",
     [(_REACT_CHECK, _REACT_CHECK.replace("    if not cur.fetchone():", "    if False:", 1))]),

    # The realistic version of the same bug, and the one a reviewer skims past:
    # the query still joins the roster and still returns a row, it just stops
    # asking whether THIS caller is in it. Any conversation with a single member
    # becomes readable by everyone.
    ("O3 the react route checks that the conversation has members, not that the caller is one",
     [('cur.execute("SELECT 1 FROM pulse_conversation_participants WHERE conversation_id=? '
       'AND user_id=? AND COALESCE(left_at,\'\')=\'\' LIMIT 1", (conversation_id, user["user_id"]))\n'
       '    if not cur.fetchone():\n'
       '        conn.close()\n'
       '        return api_error("Conversation not found.", 404, trace_id)',
       'cur.execute("SELECT 1 FROM pulse_conversation_participants WHERE conversation_id=? '
       'LIMIT 1", (conversation_id,))\n'
       '    if not cur.fetchone():\n'
       '        conn.close()\n'
       '        return api_error("Conversation not found.", 404, trace_id)')]),

    ("O4 the seen route stops checking membership before writing receipts",
     [('    cur.execute("SELECT 1 FROM pulse_conversation_participants WHERE conversation_id=? '
       'AND user_id=? LIMIT 1", (conversation_id, user["user_id"]))\n'
       '    if not cur.fetchone():\n'
       '        conn.close()\n'
       '        return api_error("Conversation not found.", 404)',
       '    cur.execute("SELECT 1 FROM pulse_conversation_participants WHERE conversation_id=? '
       'AND user_id=? LIMIT 1", (conversation_id, user["user_id"]))\n'
       '    if False:\n'
       '        conn.close()\n'
       '        return api_error("Conversation not found.", 404)')]),

    ("O5 the send path stops refusing a stranger, so a refusal becomes a write",
     [('    if not participant and not bool(conversation.get("is_public")):',
       "    if False:")]),

    # Not an access-control break — an information leak. The caller is still
    # refused, but the refusal now tells them which message ids are real.
    ("O6 the refusal for a forbidden object differs from the one for a missing object",
     [(_REACT_CHECK,
       _REACT_CHECK.replace('return api_error("Conversation not found.", 404, trace_id)',
                            'return api_error("You do not have access to this chat.", 403, trace_id)'))]),

    ("O7 a departed member keeps their access forever",
     [(_REACT_CHECK,
       _REACT_CHECK.replace("AND COALESCE(left_at,'')='' LIMIT 1", "LIMIT 1"))]),
]

# Stage 10. The target is the *seam*, not a control: two functions that each clamp
# correctly, composed so the second one truncated the first one's rendered envelope and
# left an opening fence with no close. Every mutant below restores some version of that
# defect, because the fix is only worth what the tests' ability to notice its removal is
# — and the test that already covered this exact path passed throughout, on a fixture
# whose payload was 144 characters when the break begins around 180.
SEAM_TARGET = ROOT / "services/pulse_ai_knowledge.py"
SEAM_TESTS = "tests/undx_brain/test_prompt_boundary_seam.py"

_SEAM_CHECK = """            if item.get("envelope_sealed") and envelope.is_sealed(raw):
                sealed_sections.append(raw)
                continue
            body = compact_text(raw, 700)"""

SEAM_MUTANTS = [
    # The original defect, restored exactly: every body goes through the 700 clamp.
    ("S1 a sealed envelope is clamped again and loses its closing fence",
     [(_SEAM_CHECK, """            body = compact_text(raw, 700)""")]),

    # The hole the first draft of the fix actually had. `is_sealed` is a question about
    # shape, and a retrieved document can contain both fence tokens in the right order,
    # so trusting it alone lets any text exempt itself from the clamp.
    ("S2 the producer's claim is dropped and the text is allowed to vouch for itself",
     [(_SEAM_CHECK, _SEAM_CHECK.replace(
         'item.get("envelope_sealed") and envelope.is_sealed(raw)',
         "envelope.is_sealed(raw)"))]),

    # The mirror image: the structural check goes, so a mismarked item renders malformed.
    ("S3 a mismarked item is trusted without checking the envelope is well formed",
     [(_SEAM_CHECK, _SEAM_CHECK.replace(
         'item.get("envelope_sealed") and envelope.is_sealed(raw)',
         'item.get("envelope_sealed")'))]),

    # Deleting the clamp entirely also makes the fence survive. It is the lazy fix, it
    # passes every fence assertion, and it spends the prompt budget the clamp protects.
    ("S4 the clamp is removed for everything instead of skipped for envelopes",
     [(_SEAM_CHECK, _SEAM_CHECK.replace("compact_text(raw, 700)", "compact_text(raw, 10**9)"))]),

    # The sealed block renders, but back under the heading that calls a stranger's web
    # page approved — contradicting the declaration inside the envelope itself.
    ("S5 sealed content is filed under the approved-knowledge heading again",
     [(_SEAM_CHECK, _SEAM_CHECK.replace(
         "sealed_sections.append(raw)\n                continue",
         'knowledge_lines.append(f"- {title}: {raw}")\n                continue'))]),

    # Silently dropping untrusted content would also produce an intact-looking prompt.
    ("S6 the sealed section is computed and never rendered",
     [("        sections.extend(sealed_sections)", "        pass")]),
]

# --- Stage 21: the gate registry --------------------------------------------
# The defect this stage fixed was not that two gates ignored the emergency switch.
# It was that nothing could notice: the test called `test_emergency_kills_everything`
# exercised the three functions defined in killswitches.py and no consumer, so it
# was structurally incapable of failing for a gate defined elsewhere. Two were.
#
# A registry only helps if the tests around it can fail, and a registry is unusually
# easy to test vacuously — iterating an empty tuple satisfies almost every assertion
# you would write about one. Most of the mutants below therefore attack the *test's*
# ability to see rather than the gate logic itself.
GATE_TARGET = ROOT / "services/sentinel/killswitches.py"
GATE_TESTS = "tests/sentinel/test_gate_registry.py"

_GATE_BOOTSTRAP_ENTRY = """    Gate("schema_bootstrap", "services.sentinel.bootstrap", "bootstrap_enabled",
         _SCHEMA, True, "creating Sentinel tables at boot"),"""

_GATE_LIMITS_ENTRY = """    Gate("distributed_limits", "services.sentinel.rate_limit", "enabled",
         _ENFORCE, False, "rejecting requests with 429 across gunicorn workers"),"""

GATE_MUTANTS = [
    # A gate exists in the code but drops out of the registry — the precise shape of
    # the original defect, since an unregistered gate is one the emergency test
    # cannot reach. The AST scan is what has to catch this.
    ("G1 a real gate is dropped from the registry and nothing scans for it",
     [(_GATE_BOOTSTRAP_ENTRY, "")]),

    # The vacuity failure: every loop over GATES passes when GATES is empty.
    ("G2 the registry reports no gates at all",
     [("    return {gate.name: bool(_resolve(gate)()) for gate in GATES}",
       "    return {}")]),

    # Unknown-means-open. A gate name that no longer resolves would read as allowed.
    ("G3 an unknown gate name reports as enabled instead of denied",
     [("""        if gate.name == name:
            return bool(_resolve(gate)())
    return False""",
       """        if gate.name == name:
            return bool(_resolve(gate)())
    return True""")]),

    # Enforcement checks iterate this; returning nothing makes them all trivially pass.
    ("G4 no gate is classified as enforcement, so the default-off rule guards nothing",
     [("    return tuple(g for g in GATES if g.kind in ENFORCEMENT_KINDS)",
       "    return ()")]),

    # Misclassifying the one gate a user can feel as read-only exempts it from the
    # rule that enforcement may never arrive switched on.
    ("G5 the 429 limiter is reclassified as observation",
     [(_GATE_LIMITS_ENTRY, _GATE_LIMITS_ENTRY.replace("_ENFORCE", "_OBSERVE"))]),

    # Documentation drift: the registry claims default-off for something that is on.
    ("G6 the limiter is documented default-off while defaulting on",
     [(_GATE_LIMITS_ENTRY, _GATE_LIMITS_ENTRY.replace("_ENFORCE, False,", "_ENFORCE, True,"))]),

    # Health stops carrying the registry, so an operator sees a hand-picked subset.
    ("G7 the health snapshot stops reporting the gate registry",
     [('        "gates": all_gates(),', "")]),
]

# The two gates that actually survived the emergency switch, each restored in its own
# file. These are named separately from the registry mutants because losing them again
# should fail with the file's name attached.
GATEBOOT_TARGET = ROOT / "services/sentinel/bootstrap.py"
GATEBRIDGE_TARGET = ROOT / "services/sentinel/request_bridge.py"

GATEBOOT_MUTANTS = [
    ("G8 schema bootstrap ignores the emergency switch and runs DDL anyway",
     [("""    if killswitches.emergency_killed():
        return False
    raw = os.getenv("SENTINEL_SCHEMA_BOOTSTRAP_ENABLED")""",
       """    raw = os.getenv("SENTINEL_SCHEMA_BOOTSTRAP_ENABLED")""")]),
]

GATEBRIDGE_MUTANTS = [
    ("G9 the bridge reports itself enabled while the emergency switch has it stopped",
     [("""    if killswitches.emergency_killed():
        return False
    return str(os.getenv("SENTINEL_REQUEST_BRIDGE_ENABLED", "")).strip().lower() in _TRUTHY""",
       """    return str(os.getenv("SENTINEL_REQUEST_BRIDGE_ENABLED", "")).strip().lower() in _TRUTHY""")]),
]

SUITES = [
    ("services/sentinel/request_bridge.py", BRIDGE_TARGET, BRIDGE_TESTS, BRIDGE_MUTANTS),
    ("services/sentinel/rate_limit.py", RATE_TARGET, RATE_TESTS, RATE_MUTANTS),
    ("bot.py (Stage 6 wiring)", WIRING_TARGET, WIRING_TESTS, WIRING_MUTANTS),
    ("services/sentinel/invariants.py (Stage 5/29 tenant isolation)",
     TENANT_TARGET, TENANT_TESTS, TENANT_MUTANTS),
    ("bot.py (Stage 5 object authorization)",
     OBJAUTH_TARGET, OBJAUTH_TESTS, OBJAUTH_MUTANTS),
    ("services/pulse_ai_knowledge.py (Stage 10 prompt boundary seam)",
     SEAM_TARGET, SEAM_TESTS, SEAM_MUTANTS),
    ("services/sentinel/killswitches.py (Stage 21 gate registry)",
     GATE_TARGET, GATE_TESTS, GATE_MUTANTS),
    ("services/sentinel/bootstrap.py (Stage 21 emergency reach)",
     GATEBOOT_TARGET, GATE_TESTS, GATEBOOT_MUTANTS),
    ("services/sentinel/request_bridge.py (Stage 21 emergency reach)",
     GATEBRIDGE_TARGET, GATE_TESTS, GATEBRIDGE_MUTANTS),
]

survived = []
total = 0

for label, target, tests, mutants in SUITES:
    print(f"\n{label}  ({len(mutants)} mutants against {tests})")
    original = target.read_text()
    pytest_cmd = pytest_for(tests)
    for name, spec in mutants:
        total += 1
        edits = spec if isinstance(spec, list) else [(spec[0], spec[1])]
        mutated = original
        for old, new in edits:
            assert old in mutated, f"ANCHOR NOT FOUND for {name} — mutation would be a no-op"
            mutated = mutated.replace(old, new, 1)
        assert mutated != original, f"{name} changed nothing"
        target.write_text(mutated)
        try:
            r = subprocess.run(pytest_cmd, cwd=ROOT, capture_output=True, text=True)
        finally:
            target.write_text(original)
        killed = r.returncode != 0
        print(f"  {'KILLED  ' if killed else 'SURVIVED'}  {name}")
        if not killed:
            survived.append(name)
    assert target.read_text() == original, f"{label} not restored"

print()
if survived:
    print(f"{len(survived)} MUTANT(S) SURVIVED — those properties are not tested:")
    for s in survived:
        print(f"  - {s}")
    sys.exit(1)
print(f"all {total} mutants killed")
