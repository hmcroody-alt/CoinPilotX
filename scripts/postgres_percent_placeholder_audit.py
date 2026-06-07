#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = (ROOT / "services" / "db.py").read_text(encoding="utf-8")
SITE_AUDIT = (ROOT / "scripts" / "site_functional_audit.py").read_text(encoding="utf-8")


def main():
    failures = []
    if "def _escape_postgres_percent_literals" not in DB:
        failures.append("Postgres percent literal escape helper missing")
    if "_escape_postgres_percent_literals(translated)" not in DB:
        failures.append("SQL translator does not escape literal percent characters for Postgres")
    if 'next_char in {"s", "%"}' not in DB:
        failures.append("Postgres percent escape must preserve %s placeholders and existing %% escapes")
    if '"/admin/emails"' not in SITE_AUDIT:
        failures.append("site functional audit must cover /admin/emails")
    if failures:
        raise SystemExit("postgres percent placeholder audit failed:\n- " + "\n- ".join(failures))
    print("postgres percent placeholder audit ok")


if __name__ == "__main__":
    main()
