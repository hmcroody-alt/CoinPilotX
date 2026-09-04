"""Audit `services.db.AUTO_PK_TABLES` against every table the app actually creates.

Membership in `AUTO_PK_TABLES` is what makes `CompatCursor.execute` append
`RETURNING <pk>` to an INSERT on Postgres, which is the only reason
`cur.lastrowid` ever holds a value there. A table with a generated surrogate key
whose id is read back after INSERT, but which is absent from the list, is a live
production defect on Postgres and completely invisible on SQLite.

Classifications:

  REGISTERED_CORRECTLY  generated integer PK, present in AUTO_PK_TABLES
  MISSING_LIVE          generated integer PK, absent, and a caller reads lastrowid
  MISSING_LATENT        generated integer PK, absent, but no caller reads lastrowid
  COMPOSITE_PK          PRIMARY KEY (a, b) -- no single surrogate key to return
  MANUAL_PK             caller supplies the key (TEXT PRIMARY KEY etc.)
  NO_PK                 no primary key declared
  REGISTERED_NO_SCHEMA  in AUTO_PK_TABLES but no CREATE TABLE found in source

Run: python3 scripts/auto_pk_tables_audit.py
"""

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from services.db import AUTO_PK_TABLES  # noqa: E402

CREATE_RE = re.compile(
    r"CREATE TABLE(?: IF NOT EXISTS)?\s+([a-zA-Z_]\w*)\s*\((.*?)\n\s*\)\s*", re.S | re.I
)
INSERT_RE = re.compile(r"INSERT\s+(OR\s+\w+\s+)?INTO\s+([a-zA-Z_]\w*)", re.I)
NEXT_EXECUTE_RE = re.compile(r"\.execute\s*\(|\.executemany\s*\(")
LASTROWID_RE = re.compile(r"\blastrowid\b")
AUTO_INT_RE = re.compile(r"(\w+)\s+(?:INTEGER|BIGINT)\s+PRIMARY\s+KEY(?:\s+AUTOINCREMENT)?\b", re.I)
SERIAL_RE = re.compile(r"(\w+)\s+(?:BIG)?SERIAL\s+PRIMARY\s+KEY", re.I)
COMPOSITE_RE = re.compile(r"^\s*PRIMARY\s+KEY\s*\(([^)]*,[^)]*)\)", re.I | re.M)
TEXT_PK_RE = re.compile(r"(\w+)\s+TEXT\s+PRIMARY\s+KEY", re.I)

WINDOW = 2500  # chars after an INSERT in which a lastrowid read counts as "consumes the id"


def sources():
    paths = [os.path.join(ROOT, "bot.py")]
    for directory, _, names in os.walk(os.path.join(ROOT, "services")):
        paths.extend(os.path.join(directory, n) for n in sorted(names) if n.endswith(".py"))
    paths.extend(
        os.path.join(ROOT, n)
        for n in sorted(os.listdir(ROOT))
        if n.endswith(".py") and n != "bot.py"
    )
    return paths


def main():
    blobs = {}
    for path in sources():
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as handle:
                blobs[path] = handle.read()
        except OSError:
            continue

    schema = {}
    for path, src in blobs.items():
        for match in CREATE_RE.finditer(src):
            name = match.group(1).lower()
            if name in schema:
                continue
            body = match.group(2)
            if COMPOSITE_RE.search(body):
                schema[name] = ("COMPOSITE_PK", None)
            elif AUTO_INT_RE.search(body):
                schema[name] = ("AUTO", AUTO_INT_RE.search(body).group(1))
            elif SERIAL_RE.search(body):
                schema[name] = ("AUTO", SERIAL_RE.search(body).group(1))
            elif TEXT_PK_RE.search(body):
                schema[name] = ("MANUAL_PK", TEXT_PK_RE.search(body).group(1))
            else:
                schema[name] = ("NO_PK", None)

    # One pass over every INSERT in the tree; record which tables have a caller
    # that reads lastrowid soon after inserting into them.
    # The read must belong to *this* INSERT, so the window ends at the next
    # .execute() call -- a lastrowid after an intervening statement is reading
    # that statement's id, not this one. `INSERT OR IGNORE` is excluded: the shim
    # rewrites it to ON CONFLICT DO NOTHING and deliberately appends no RETURNING,
    # so registration would not give those call sites a lastrowid either.
    consumers = {}
    for path, src in blobs.items():
        for match in INSERT_RE.finditer(src):
            if match.group(1):
                continue
            table = match.group(2).lower()
            window = src[match.end():match.end() + WINDOW]
            boundary = NEXT_EXECUTE_RE.search(window)
            if boundary:
                window = window[: boundary.start()]
            if LASTROWID_RE.search(window):
                line = src.count("\n", 0, match.start()) + 1
                consumers.setdefault(table, []).append(
                    f"{os.path.relpath(path, ROOT)}:{line}"
                )

    buckets = {
        "REGISTERED_CORRECTLY": [],
        "MISSING_LIVE": [],
        "MISSING_LATENT": [],
        "COMPOSITE_PK": [],
        "MANUAL_PK": [],
        "NO_PK": [],
        "REGISTERED_NO_SCHEMA": [],
    }
    for name, (kind, _pk) in sorted(schema.items()):
        if kind == "AUTO":
            if name in AUTO_PK_TABLES:
                buckets["REGISTERED_CORRECTLY"].append(name)
            elif name in consumers:
                buckets["MISSING_LIVE"].append(name)
            else:
                buckets["MISSING_LATENT"].append(name)
        else:
            buckets[kind].append(name)
    for name in sorted(AUTO_PK_TABLES):
        if name not in schema:
            buckets["REGISTERED_NO_SCHEMA"].append(name)

    print(f"CREATE TABLE definitions found : {len(schema)}")
    print(f"AUTO_PK_TABLES entries         : {len(AUTO_PK_TABLES)}")
    print()
    for label in (
        "MISSING_LIVE",
        "MISSING_LATENT",
        "REGISTERED_CORRECTLY",
        "COMPOSITE_PK",
        "MANUAL_PK",
        "NO_PK",
        "REGISTERED_NO_SCHEMA",
    ):
        print(f"{label:22s} {len(buckets[label])}")
    print()
    print("=== MISSING_LIVE (generated PK, unregistered, id read back) ===")
    for name in buckets["MISSING_LIVE"]:
        print(f"  {name:45s} {consumers.get(name, [])[:3]}")
    if not buckets["MISSING_LIVE"]:
        print("  (none)")
    print()
    print("=== MISSING_LATENT (first 60) ===")
    for name in buckets["MISSING_LATENT"][:60]:
        print(f"  {name}")
    print()
    target = "pulse_status_shares"
    print(f"{target}: schema={schema.get(target)} "
          f"registered={target in AUTO_PK_TABLES} "
          f"lastrowid_consumers={consumers.get(target, [])}")


if __name__ == "__main__":
    main()
