"""Mutation check for Sentinel's request bridge (Stage 30 discipline).

A passing security test suite proves nothing on its own — it proves the tests
run, not that they can fail. This breaks each control in ``request_bridge.py``
one at a time and requires the suite to notice. A surviving mutant names a
security property that is described in prose and defended by nothing.

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
TARGET = ROOT / "services/sentinel/request_bridge.py"
PYTEST = [os.environ.get("SENTINEL_PYTHON", sys.executable), "-m", "pytest",
          "tests/sentinel/test_request_bridge.py", "-q", "--no-header", "-x"]

MUTANTS = [
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

original = TARGET.read_text()
survived = []

for name, spec in MUTANTS:
    edits = spec if isinstance(spec, list) else [(spec[0], spec[1])]
    mutated = original
    for old, new in edits:
        assert old in mutated, f"ANCHOR NOT FOUND for {name} — mutation would be a no-op"
        mutated = mutated.replace(old, new, 1)
    assert mutated != original, f"{name} changed nothing"
    TARGET.write_text(mutated)
    try:
        r = subprocess.run(PYTEST, cwd=ROOT, capture_output=True, text=True)
    finally:
        TARGET.write_text(original)
    killed = r.returncode != 0
    print(f"  {'KILLED  ' if killed else 'SURVIVED'}  {name}")
    if not killed:
        survived.append(name)

assert TARGET.read_text() == original, "source not restored"
print()
if survived:
    print(f"{len(survived)} MUTANT(S) SURVIVED — those properties are not tested:")
    for s in survived:
        print(f"  - {s}")
    sys.exit(1)
print(f"all {len(MUTANTS)} mutants killed")
