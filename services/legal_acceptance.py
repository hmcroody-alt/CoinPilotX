"""What a member agreed to, and which version of it.

Signup asks the member to agree to the Terms, the Privacy Policy and the
no-tolerance rules, refuses to create the account without it, and then throws the
answer away. So there is no record that any member ever accepted anything — and
because no *version* was recorded either, there is no way to ask who has not yet
seen a rewrite. §1 of ``docs/legal/PULSE_LEGAL_SURFACE_AUDIT.md`` says both
published documents have to be rewritten, which is exactly the event this table
exists to survive.

The seller side of this repository already does it correctly
(``services/marketplace_commercial_operations.py``): a row per
``(seller, version)``, ``UNIQUE`` on the pair, so changing a disclosure forces
re-acceptance instead of silently reinterpreting an older consent as agreement to
the new text. This is the same shape for members.

A version is not a serial number invented here. It names what the document says
about itself — both currently state "Last updated: May 2026" — and
``tests/test_legal_acceptance.py`` asserts each constant still agrees with the
page that renders. Editing a document without bumping its constant fails there,
which is the only thing that makes a stored version mean anything. Without that
test the column would record which string was in this file, not which text the
member read.

Nothing is stored beyond the user id, the document, the version, where the
acceptance came from, and when. No IP address and no user agent: this records a
decision, not a session, and the same mission writing this is also writing the
data inventory this platform has to publish. Widening it needs the justification
any other personal-data field needs.
"""

from __future__ import annotations

from datetime import datetime, timezone

from services import db


#: What both documents currently say about themselves, verbatim. Shared because
#: they were last revised together and the test reads this same string out of
#: each rendered page.
STATED_LAST_UPDATED = "May 2026"

#: Document key -> the version a member accepts today. Keys are stored in the
#: table, so renaming one orphans existing rows; add a new key instead.
DOCUMENTS = {
    "terms": "PULSESOC_TERMS_2026_05",
    "privacy": "PULSESOC_PRIVACY_2026_05",
}

#: The signup checkbox also binds the member to the "no-tolerance rules", and
#: `/community-rules` publishes no revision date of any kind — so there is no
#: version to record and nothing to detect a rewrite against. Recording one would
#: mean inventing a version for a document that does not claim one, which is a
#: worse failure than the honest gap: it would look like coverage.
#:
#: OWNER DECISION REQUIRED (D-L2): either publish a revision date on
#: `/community-rules` and add it to DOCUMENTS above, or stop naming it in the
#: acceptance checkbox. It is currently named in what the member agrees to.
UNVERSIONED_DOCUMENTS = {
    "community_rules": "/community-rules publishes no revision date, so no version exists to record.",
}

#: Where an acceptance came from. Closed set because "which surface asked" is the
#: part of this record a reviewer will question, and a free-text column fills up
#: with three spellings of the same answer.
SOURCES = ("web_signup", "web_login", "mobile_register")

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def ensure_schema(conn=None) -> None:
    """Create the table. Called from `init_db()`, which is the only safe moment.

    Not lazily from `record()`, which was the first attempt and deadlocks: the
    caller is mid-transaction on its own connection by then, and CREATE TABLE
    from a second connection waits for a write lock the caller will not release
    until it returns. SQLite reports `database is locked` and the whole signup
    rolls back, so every account creation fails — the first version of this
    module turned a missing audit record into a total outage of the thing it was
    auditing.
    """

    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS user_legal_acceptances (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                document TEXT NOT NULL,
                document_version TEXT NOT NULL,
                acceptance_source TEXT NOT NULL,
                accepted_at TEXT NOT NULL,
                UNIQUE(user_id, document, document_version))"""
        )
        if owned:
            conn.commit()
    finally:
        if owned:
            conn.close()


def record(cur, user_id, *, source: str) -> list[str]:
    """Record acceptance of every current document, on the caller's cursor.

    On the caller's cursor so the acceptance commits with the account that it is
    a condition of. An account that exists without its acceptance row is the
    defect this replaces, and writing the row afterwards on a second connection
    would leave a window that produces exactly that.

    Returns the documents actually inserted. Re-accepting a version already on
    file is not an error and not a second row — the UNIQUE carries that, and a
    member who signs in twice has not agreed to anything new.
    """

    if source not in SOURCES:
        raise ValueError(f"unknown acceptance source {source!r}; expected one of {SOURCES}")
    if not user_id:
        raise ValueError("an acceptance needs the member it belongs to")

    now = _now()
    written = []
    for document, version in sorted(DOCUMENTS.items()):
        cur.execute(
            "INSERT OR IGNORE INTO user_legal_acceptances "
            "(user_id, document, document_version, acceptance_source, accepted_at) "
            "VALUES (?,?,?,?,?)",
            (int(user_id), document, version, source, now),
        )
        written.append(document)
    return written


def accepted(user_id, conn=None) -> list[dict]:
    """Every acceptance on file for this member, oldest first."""

    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT document, document_version, acceptance_source, accepted_at "
            "FROM user_legal_acceptances WHERE user_id=? ORDER BY accepted_at, document",
            (int(user_id),),
        ).fetchall()
    finally:
        if owned:
            conn.close()
    return [
        {
            "document": document,
            "document_version": version,
            "acceptance_source": source,
            "accepted_at": at,
        }
        for document, version, source, at in (db.row_values(row) for row in rows)
    ]


def outstanding(user_id, conn=None) -> list[str]:
    """Documents this member has not accepted at the version now in force.

    This is why the version is stored instead of a boolean. When the Terms are
    rewritten, `DOCUMENTS` changes and every member is immediately outstanding
    again — which is the correct consequence, and is unanswerable from a column
    that only says "yes".
    """

    on_file = {entry["document_version"] for entry in accepted(user_id, conn)}
    return sorted(
        document for document, version in DOCUMENTS.items() if version not in on_file
    )
