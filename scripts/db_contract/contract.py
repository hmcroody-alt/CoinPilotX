"""Resolve every executed table reference against a recognised schema authority.

The failure this exists to prevent, in full:

    try:
        cur.execute("SELECT 1 FROM private_chat_blocks WHERE ...")
        if cur.fetchone():
            return deny
    except Exception:
        pass

``private_chat_blocks`` had no CREATE TABLE, no migration, no writer and no row
in production. The read raised "no such table" on every request, the bare
``except`` swallowed it, and the block check had never once denied anything --
with no log line anywhere. The check reads correctly, names a plausible table,
passes review and passes tests. The only way to find it is to ask whether the
table exists.

Schema authority in this repo is genuinely distributed. There is no migration
framework; ``bot.init_db()`` creates ~536 tables imperatively, and another 458
are created by ``services/**/schema.py`` modules, per-service ``ensure_schema``
functions, ``pulse_communications_v2/models.py`` and one root module. Forcing
all of those into ``init_db()`` would be a false model, so the contract
recognises any runtime-scope declaration and *names the authority* in its
output (brief section 6).
"""
from __future__ import annotations

import fnmatch
import json
import os
from dataclasses import dataclass, field

from . import sqlscan

SKIP_DIRS = {
    ".git", "node_modules", ".venv", "venv", "__pycache__", ".expo",
    "ios", "android", "Pods", ".claude", "build", "dist", ".pytest_cache",
    "mobile", "mobile-native", "web", "static",
}

# --------------------------------------------------------------------------
# Scope: who is bound by the contract
# --------------------------------------------------------------------------

RUNTIME_PREFIXES = ("services/", "pulse_communications_v2/", "models/")
MIGRATION_PREFIXES = ("migrations/",)
TEST_PREFIXES = ("tests/",)
SCRIPT_PREFIXES = ("scripts/",)


def scope_of(rel: str) -> str:
    """runtime | migration | test | script | other.

    A root-level ``*.py`` is runtime: that is where ``bot.py`` and the workers
    live, and a worker running a dead query is exactly as broken as a route
    doing it.
    """
    rel = rel.replace(os.sep, "/")
    if rel.startswith(TEST_PREFIXES):
        return "test"
    if rel.startswith(SCRIPT_PREFIXES):
        return "script"
    if rel.startswith(MIGRATION_PREFIXES):
        return "migration"
    if rel.startswith(RUNTIME_PREFIXES):
        return "runtime"
    if "/" not in rel and rel.endswith(".py"):
        return "runtime"
    return "other"


# Named families, for reporting. Order matters: first match wins.
AUTHORITY_FAMILIES = (
    ("bot.init_db", ("bot.py",),
     "the imperative schema; ~536 tables, no migration framework"),
    ("comm_v2.models", ("pulse_communications_v2/*",),
     "TableSpec DDL applied by models.ensure_schema(cur)"),
    ("business_os.schema", ("services/business_os/*/schema.py",),
     "per-domain schema module with ensure_schema()"),
    ("service.schema", ("services/*/schema.py",),
     "per-package schema module with ensure_schema()"),
    ("service.ensure", ("services/*",),
     "service module that creates its own tables on first use"),
    ("migration", ("migrations/*",), "one-off migration script"),
    ("root.module", ("*.py",), "root-level module or worker"),
)


def authority_family(rel: str) -> tuple:
    rel = rel.replace(os.sep, "/")
    for name, patterns, why in AUTHORITY_FAMILIES:
        for pattern in patterns:
            if fnmatch.fnmatch(rel, pattern):
                return name, why
    return "unrecognised", "no recognised schema authority family"


# --------------------------------------------------------------------------
# Criticality (brief section 8)
# --------------------------------------------------------------------------

#: Substrings that put a table in a security/money/privacy domain. Matched
#: against the table name. Deliberately broad on the *table* side and narrow on
#: the consequence side: being critical raises the severity of an unresolved
#: reference, it never relaxes anything.
CRITICAL_DOMAINS = {
    "auth": ("session", "token", "login", "password", "credential", "otp",
             "verification", "refresh", "device_trust", "auth_"),
    "authz": ("permission", "role", "admin", "capability", "grant", "acl",
              "moderat", "restrict", "ban", "suspend"),
    "blocking": ("block", "mute", "report_", "abuse"),
    "privacy": ("privacy", "consent", "visibility", "audience", "settings"),
    "messaging": ("message", "conversation", "participant", "thread", "dm_"),
    "money": ("payment", "charge", "refund", "payout", "invoice", "ledger",
              "wallet", "balance", "transaction", "settlement", "escrow",
              "subscription", "entitlement", "price", "fee", "treasury",
              "stripe", "webhook", "idempoten"),
    "commerce": ("order", "cart", "checkout", "reservation", "inventory",
                 "stock", "fulfil", "fulfill", "shipment", "seller",
                 "supplier", "listing"),
}


def domains_for(table: str) -> tuple:
    low = table.lower()
    hits = []
    for domain, needles in CRITICAL_DOMAINS.items():
        if any(needle in low for needle in needles):
            hits.append(domain)
    return tuple(hits)


def is_critical(table: str) -> bool:
    return bool(domains_for(table))


# --------------------------------------------------------------------------
# Allowlist (brief section 25)
# --------------------------------------------------------------------------

@dataclass
class Allowlist:
    """Three sections with three different meanings -- see the ``_doc`` key in
    ``config/db_contract_allowlist.json``.

    ``entries`` are permanent, provable-only-by-a-human exceptions. ``defects``
    are known-wrong references awaiting a separate fix commit (brief section 36:
    the sentinel lands separately from the defects it finds). ``retired`` are
    objects that must never be referenced again, whatever else the repo says.
    """

    entries: dict = field(default_factory=dict)
    defects: dict = field(default_factory=dict)
    retired_tables: dict = field(default_factory=dict)
    retired_columns: dict = field(default_factory=dict)
    path: str = ""

    REQUIRED_FIELDS = ("reason", "owner", "why_unprovable")
    REQUIRED_DEFECT_FIELDS = ("evidence", "classification", "owner", "impact")

    @classmethod
    def load(cls, path):
        if not path or not os.path.exists(path):
            return cls(path=path or "")
        with open(path, encoding="utf-8") as handle:
            raw = json.load(handle)
        entries = {i["table"].lower(): i for i in raw.get("tables", [])}
        defects = {i["table"].lower(): i for i in raw.get("known_defects", [])}
        retired = raw.get("retired_objects", {}) or {}
        rt = {i["name"].lower(): i for i in retired.get("tables", [])}
        rc = {i["name"].lower(): i for i in retired.get("columns", [])}
        return cls(entries=entries, defects=defects, retired_tables=rt,
                   retired_columns=rc, path=path)

    def validate(self):
        """Every entry must say what it is, who owns it, and why static
        analysis cannot prove it. An anonymous permanent exception is how a
        gate stops meaning anything."""
        problems = []
        for table, item in sorted(self.entries.items()):
            for field_name in self.REQUIRED_FIELDS:
                if not str(item.get(field_name, "")).strip():
                    problems.append(
                        f"allowlist entry '{table}' is missing '{field_name}'")
        for table, item in sorted(self.defects.items()):
            for field_name in self.REQUIRED_DEFECT_FIELDS:
                if not str(item.get(field_name, "")).strip():
                    problems.append(
                        f"known_defects entry '{table}' is missing '{field_name}'")
        overlap = sorted(set(self.entries) & set(self.defects))
        for table in overlap:
            problems.append(
                f"'{table}' is in both 'tables' and 'known_defects'; it cannot be "
                f"both a correct-but-unprovable reference and a known defect")
        for table in sorted(set(self.retired_tables) & (set(self.entries) | set(self.defects))):
            problems.append(
                f"'{table}' is retired; it must not also be allowlisted or "
                f"recorded as a known defect")
        return problems

    def covers(self, table):
        return self.entries.get(table.lower())

    def known_defect(self, table):
        return self.defects.get(table.lower())

    def retired_table(self, table):
        return self.retired_tables.get(table.lower())


# --------------------------------------------------------------------------
# Analysis
# --------------------------------------------------------------------------

@dataclass
class Finding:
    kind: str          # undeclared_table / unresolved_sql / unrecognised_authority
    severity: str      # critical / high / medium
    reference: object
    detail: str
    domains: tuple = ()

    def render(self) -> str:
        """Brief section 13: say the file, the line, the statement and the
        reason -- not 'FAILED: table mismatch'."""
        ref = self.reference
        head = f"{ref.location}  ({ref.func})"
        dom = f" [{', '.join(self.domains)}]" if self.domains else ""
        return (f"{self.severity.upper():8s} {head}{dom}\n"
                f"         {getattr(ref, 'snippet', '')}\n"
                f"         {self.detail}")


@dataclass
class Report:
    declarations: dict = field(default_factory=dict)   # table -> [Declaration]
    references: list = field(default_factory=list)
    findings: list = field(default_factory=list)
    known: list = field(default_factory=list)          # triaged known defects
    statements: list = field(default_factory=list)
    unresolved: list = field(default_factory=list)
    files_scanned: int = 0
    seconds: float = 0.0
    alters: dict = field(default_factory=dict)
    indexes: dict = field(default_factory=dict)
    guarded: list = field(default_factory=list)

    @property
    def runtime_tables(self):
        return {r.table for r in self.references}

    @property
    def declared_tables(self):
        return set(self.declarations)


def iter_python(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            if name.endswith(".py"):
                yield os.path.join(dirpath, name)


def analyse(root, allowlist_path=None, include_scopes=("runtime",)):
    import time

    started = time.time()
    allowlist = Allowlist.load(allowlist_path)
    report = Report()

    declarations = {}
    references = []
    unresolved = []
    statements = []

    for abs_path in iter_python(root):
        rel = os.path.relpath(abs_path, root).replace(os.sep, "/")
        scope = scope_of(rel)
        if scope == "other":
            continue
        try:
            facts = sqlscan.scan_file(root, abs_path)
        except SyntaxError:
            continue
        report.files_scanned += 1

        # Declarations count from runtime and migration scope only. A table
        # that exists only because a test created it is not provisioned in
        # production -- that is precisely the failure mode being gated.
        if scope in ("runtime", "migration"):
            for decl in facts.declarations:
                declarations.setdefault(decl.table, []).append(decl)
            for table, line, func in facts.alters:
                report.alters.setdefault(table, []).append((rel, line, func))
            for index, table, unique, line in facts.indexes:
                report.indexes.setdefault(table, []).append(
                    {"index": index, "unique": unique, "file": rel, "line": line})

        if scope in include_scopes:
            references.extend(facts.references)
            statements.extend(facts.statements)
            unresolved.extend(
                (rel, line, func, shape) for line, func, shape in facts.unresolved)

    report.declarations = declarations
    report.references = references
    report.unresolved = unresolved
    report.statements = statements

    # -- retired objects (brief section 12) --------------------------------
    # Checked first and unconditionally. A retired object is fatal even if
    # something in the repo later re-declares it: re-adding the CREATE TABLE
    # for private_chat_blocks would not make the dead block check work, it
    # would just make an empty table that still denies nothing.
    for ref in references:
        retired = allowlist.retired_table(ref.table)
        if not retired:
            continue
        report.findings.append(Finding(
            kind="retired_table",
            severity="critical",
            reference=ref,
            domains=domains_for(ref.table),
            detail=(f"runtime {ref.verb.upper()} names '{ref.table}', which was "
                    f"RETIRED in {retired.get('retired_in', 'a previous fix')}. "
                    f"{retired.get('why_fatal', '')} "
                    f"Use {retired.get('replacement', 'the current store')} "
                    f"instead. This reference reintroduces a closed defect."),
        ))

    for stmt in statements:
        for name, item in allowlist.retired_columns.items():
            table, _, column = name.partition(".")
            if not column:
                continue
            # Qualified (users.message_privacy), or unqualified in a statement
            # whose only table is the owning one.
            hit = (name in stmt.qualified
                   or (stmt.tables == (table,) and column in stmt.identifiers))
            if not hit:
                continue
            report.findings.append(Finding(
                kind="retired_column",
                severity="critical",
                reference=stmt,
                domains=domains_for(table),
                detail=(f"statement names the RETIRED column '{name}'. "
                        f"{item.get('why_fatal', '')} "
                        f"Use {item.get('replacement', 'the current store')}."),
            ))

    # -- the contract ------------------------------------------------------
    for ref in references:
        if allowlist.retired_table(ref.table):
            continue                      # already reported above
        if ref.table in declarations:
            continue
        if ref.resolution == "guarded":
            # The call site probes for the table before using it, so absence
            # is a handled state rather than a silent no-op. Recorded for the
            # report, never raised as a finding.
            report.guarded.append(ref)
            continue
        covered = allowlist.covers(ref.table)
        if covered:
            continue
        domains = domains_for(ref.table)
        defect = allowlist.known_defect(ref.table)
        if defect:
            # Real, already triaged, fix lands in its own commit (section 36).
            report.known.append(Finding(
                kind="known_defect",
                severity="known",
                reference=ref,
                domains=domains,
                detail=(f"KNOWN DEFECT ({defect.get('classification')}): "
                        f"{defect.get('impact')} Owner: {defect.get('owner')}."),
            ))
            continue
        severity = "critical" if domains else "high"
        report.findings.append(Finding(
            kind="undeclared_table",
            severity=severity,
            reference=ref,
            domains=domains,
            detail=(f"runtime {ref.verb.upper()} names table '{ref.table}', which "
                    f"has no CREATE TABLE in any recognised schema authority "
                    f"(searched bot.py, services/**, pulse_communications_v2/**, "
                    f"migrations/**). A query against a table that does not "
                    f"exist raises at runtime; if it sits inside a broad "
                    f"except, the statement is a permanent no-op."),
        ))

    report.seconds = time.time() - started
    return report, allowlist
