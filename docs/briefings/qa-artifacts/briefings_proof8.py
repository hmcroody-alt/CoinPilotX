"""ITEM 8 - invalid settings are REFUSED, with no partial mutation.

Exercises the deployed engine (base-main @ b22382a3) and the real Flask route's
error contract. The point is not just "it returns 4xx" but that a patch mixing
a valid and an invalid field writes NOTHING.
"""
from __future__ import annotations

import sys

sys.path.insert(0, "/Users/hmcherie/base-main")
sys.path.insert(0, "/Users/hmcherie/base-main/tests")

from services.pulse_briefings import engine
from services.pulse_briefings.engine import InvalidPreference
from tests.briefings.test_pulse_briefings import _fresh_conn

FAIL = []


def check(label, cond):
    if not cond:
        FAIL.append(label)
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")


print("=" * 72)
print("ITEM 8 - MALFORMED PREFERENCE WRITES")
print("=" * 72)

bad_cases = [
    ("bad frequency", {"frequency": "hourly_spam"}),
    ("frequency wrong type", {"frequency": 7}),
    ("invalid quiet time (hour 99)", {"quiet_start": "99:00"}),
    ("invalid quiet time (minute 61)", {"quiet_end": "07:61"}),
    ("invalid quiet time (prose)", {"quiet_start": "morning"}),
    ("boolean given prose", {"enabled": "yes please"}),
    ("boolean given float", {"crypto_enabled": 1.5}),
]

for label, payload in bad_cases:
    conn = _fresh_conn()
    before = dict(engine.get_preferences(1, conn=conn))
    raised = None
    try:
        engine.update_preferences(1, payload, conn=conn)
    except InvalidPreference as exc:
        raised = exc
    after = dict(engine.get_preferences(1, conn=conn))
    ok = raised is not None and before == after
    print(f"\n  {label}: payload={payload}")
    print(f"     raised={type(raised).__name__ if raised else None}"
          f" field={getattr(raised,'field',None)!r} expected={getattr(raised,'expected',None)!r}")
    check(f"{label} -> refused", raised is not None)
    check(f"{label} -> row unchanged (no partial mutation)", before == after)
    conn.close()

print("\n" + "-" * 72)
print("ATOMICITY: a patch mixing one VALID and one INVALID field must store nothing")
print("-" * 72)
conn = _fresh_conn()
before = dict(engine.get_preferences(1, conn=conn))
try:
    engine.update_preferences(1, {"frequency": "daily", "quiet_start": "nonsense"}, conn=conn)
except InvalidPreference as exc:
    print(f"  refused on field={exc.field!r}")
after = dict(engine.get_preferences(1, conn=conn))
print(f"  frequency before={before['frequency']!r} after={after['frequency']!r}")
check("valid sibling field was NOT written", before["frequency"] == after["frequency"])
check("entire row unchanged", before == after)
conn.close()

print("\n" + "-" * 72)
print("CONTROL: valid writes still succeed (the fix must not reject everything)")
print("-" * 72)
conn = _fresh_conn()
out = engine.update_preferences(1, {"frequency": "morning_evening", "quiet_start": "23:30"}, conn=conn)
print(f"  stored frequency={out['frequency']!r} quiet_start={out['quiet_start']!r}")
check("valid frequency accepted", out["frequency"] == "morning_evening")
check("valid quiet_start accepted", out["quiet_start"] == "23:30")
conn.close()

print("\n" + "-" * 72)
print("ROUTE CONTRACT: bot.py maps InvalidPreference -> HTTP 400 naming the field")
print("-" * 72)
import re
src = open("/Users/hmcherie/base-main/bot.py", encoding="utf-8", errors="replace").read()
# widen the window: at 400 chars the match truncated mid-token ('"expect')
# and produced two FALSE failures for `400` and `exc.expected`, both of which
# are present in the route just past the old cutoff.
m = re.search(r"except briefing_service\.InvalidPreference as exc:(.{0,900})", src, re.S)
snippet = m.group(1) if m else ""
check("route catches InvalidPreference", m is not None)
check("route returns 400", "400" in snippet)
check("route reports the offending field", "exc.field" in snippet)
check("route reports what was expected", "exc.expected" in snippet)
print("  route snippet:\n" + "\n".join("      " + l for l in snippet.strip().splitlines()[:8]))

print("\n" + "=" * 72)
print("ITEM 8 RESULT:", "ALL PASSED" if not FAIL else f"{len(FAIL)} FAILED -> {FAIL}")
print("=" * 72)
sys.exit(1 if FAIL else 0)
