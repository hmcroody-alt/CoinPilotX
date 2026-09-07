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

SUITES = [
    ("services/sentinel/request_bridge.py", BRIDGE_TARGET, BRIDGE_TESTS, BRIDGE_MUTANTS),
    ("services/sentinel/rate_limit.py", RATE_TARGET, RATE_TESTS, RATE_MUTANTS),
    ("bot.py (Stage 6 wiring)", WIRING_TARGET, WIRING_TESTS, WIRING_MUTANTS),
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
