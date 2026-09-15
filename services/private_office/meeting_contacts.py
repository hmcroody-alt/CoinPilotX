"""Meeting invitees become people in the member's directory. Automatically.

Why this exists
---------------
A member who schedules a meeting with someone has told the Office they know
them. Making them type that person in a second time, on a different screen, to
get a contact card is the kind of double entry that ends with two half-filled
directories and a member who trusts neither.

So: after an invite is accepted by the meetings engine, each invitee is
resolved against the member's existing people and either linked, updated or
created. The canonical PulseSoc account id does the matching, which is why this
never produces the duplicate that a name-based version would.

Three properties this module is built around
--------------------------------------------
**It cannot break an invitation.** Every invitee is handled independently and
every failure is swallowed and logged. The invite has already succeeded by the
time this runs; a directory write is bookkeeping, and bookkeeping that can fail
a meeting invitation is a worse feature than no bookkeeping. This is §21.

**It does not rename anyone.** A person already in the directory keeps the name
the member gave them. The account's display name is only used for someone being
created here, who by definition has no name yet — otherwise scheduling a
meeting would silently rewrite "Mum" to whatever the account's profile says.

**It adds nobody to the host's own directory but the host's.** The writes are
owner-scoped to the member who sent the invites. Invitees do not get a contact
card for the host: they did not ask for one, and a directory that filled itself
from other people's meetings would be the surveillance this package forbids.
"""

from __future__ import annotations

import logging

from services.private_office import accounts as _accounts
from services.private_office import relationships as _relationships

LOGGER = logging.getLogger("private_office.meeting_contacts")

#: Ceiling per call, matching the meetings engine's own invite cap. A bound
#: here as well as there, because the two numbers are allowed to be different
#: and the failure of trusting the caller's is unbounded work in a request.
MAX_INVITEES = 50


def record_invitees(
    cur,
    *,
    owner_user_id: int,
    user_ids,
    actor_user_id: int | None = None,
) -> dict:
    """Link or create a directory entry for each invited account.

    Returns ``{"created": [...], "linked": [...], "failed": [...]}`` of account
    ids, so a caller can report what happened without re-reading the directory.
    Never raises.
    """
    owner = int(owner_user_id or 0)
    actor = int(actor_user_id or owner)
    out: dict[str, list[int]] = {"created": [], "linked": [], "failed": []}
    if owner <= 0:
        return out

    wanted: list[int] = []
    for value in user_ids or ():
        try:
            account = int(value or 0)
        except (TypeError, ValueError):
            continue
        # The host is not one of their own contacts.
        if account > 0 and account != owner and account not in wanted:
            wanted.append(account)
    wanted = wanted[:MAX_INVITEES]
    if not wanted:
        return out

    for account in wanted:
        try:
            # Resolved without a shared index on purpose. `save_person` reads a
            # fresh one anyway, and a cached index would be stale the moment
            # this loop created somebody — cheap here, because the list is
            # capped and the read is over the member's own bounded directory.
            existing = _relationships.resolve_person(
                cur, owner_user_id=owner, pulsesoc_user_id=account)
            profile = _accounts.lookup(cur, user_id=account)
            saved = _relationships.save_person(
                cur,
                owner_user_id=owner,
                # Only a person being created gets a name from the account.
                name=(None if existing else _accounts.display_name_for(
                    profile, fallback=f"PulseSoc member {account}")),
                username=(profile or {}).get("username") or None,
                pulsesoc_user_id=account,
                source=_relationships.SOURCE_MEETING_INVITEE,
                actor_user_id=actor,
            )
        except Exception:  # noqa: BLE001
            # §21. The invitation is already out; this is bookkeeping.
            LOGGER.exception("PRIVATE_MEETING_CONTACT_FAILED account=%s", account)
            out["failed"].append(account)
            continue

        if saved.get("status") == _relationships.SAVE_CREATED:
            out["created"].append(account)
        else:
            out["linked"].append(account)
    return out
