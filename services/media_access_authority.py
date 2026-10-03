"""Whether media bytes may still be retrieved, separately from whether PulseSoc shows them.

The defect this exists to close: PulseSoc has never had any ability to affect
media access. All 375 assets in the Mux account carry ``playback_policy:
["public"]``, no code path in the repository deletes a playback id, deletes an
asset, or changes an asset's policy, and the only Mux-mutating call anywhere is
``PUT /live-streams/{id}/disable`` -- which stops future ingest and leaves the
recorded asset's public address untouched. So every deletion path writes a
database boolean, the content stops being shown, and a saved
``stream.mux.com/<id>.m3u8`` or ``image.mux.com/<id>/thumbnail.jpg`` keeps
returning 200 indefinitely.

**Three states, not one.** The thing this module refuses to collapse:

* APPLICATION VISIBILITY -- can PulseSoc show this to this viewer?
* MEDIA ACCESS -- can a party holding the URL retrieve bytes?
* MEDIA RETENTION -- should the bytes continue to exist?

Writing the first alone is what produced the defect. Retention says nothing
about access, and legal hold -- which blocks destruction -- does not grant
retrievability: retained evidence may stay unreachable by ordinary users.

**Revoke, do not destroy.** Mux will not change an existing playback id's
policy, but it will delete one, and deleting a playback id does not delete the
asset. So revocation is ``DELETE /assets/{id}/playback-ids/{pid}``: every saved
stream and poster URL starts returning the same 404 a never-existing playback id
returns, while the bytes remain for moderation review, appeal, legal hold and
account recovery. Destruction is a separately permissioned retention action
reached through ``PURGE_PENDING``, never a consequence of a user pressing delete.

One playback id addresses the stream *and* the poster, thumbnail, gif and
storyboard. That is why revocation is modelled as playback-id deletion rather
than a policy flip: it covers the whole media surface in one call, and a poster
is part of the private media surface.

**Deliberately pure.** Like ``services/music_authority.py``, which this
generalises rather than replaces, nothing here performs I/O or imports ``bot``.
It is handed the state, the actor, the permission check and the viewer's
audience answer, and it decides. That is what lets the adversarial cases in
``docs/search_os/AGENT_00_MEDIA_ACCESS_LIFECYCLE.md`` §20 be tested without
touching a provider -- and a privacy control whose tests need a provider is a
privacy control that does not get tested.

Design contract: ``docs/search_os/AGENT_00_MEDIA_ACCESS_LIFECYCLE.md``.
"""

MEDIA_PERMISSIONS = frozenset({
    "media.view_all",
    "media.moderate",
    "media.revoke",
    "media.restore",
    "media.purge",
    "media.legal_hold",
})

# ---------------------------------------------------------------- lifecycle states

STATE_ACTIVE = "ACTIVE"
STATE_AUDIENCE_RESTRICTED = "AUDIENCE_RESTRICTED"
STATE_WITHDRAWN = "WITHDRAWN"
STATE_MODERATION_REMOVED = "MODERATION_REMOVED"
STATE_ACCOUNT_DEACTIVATED = "ACCOUNT_DEACTIVATED"
STATE_HELD = "HELD"
STATE_PURGE_PENDING = "PURGE_PENDING"
STATE_PURGED = "PURGED"

LIFECYCLE_STATES = (
    STATE_ACTIVE,
    STATE_AUDIENCE_RESTRICTED,
    STATE_WITHDRAWN,
    STATE_MODERATION_REMOVED,
    STATE_ACCOUNT_DEACTIVATED,
    STATE_HELD,
    STATE_PURGE_PENDING,
    STATE_PURGED,
)

# States in which bytes may still be handed to someone. An allowlist, not a
# denylist: a state added later is unretrievable until someone decides
# otherwise. The audio authority is written the same way and that is the reason
# it has never leaked a state it did not anticipate.
SERVABLE_STATES = frozenset({STATE_ACTIVE, STATE_AUDIENCE_RESTRICTED})

# States that require the public playback id to be gone at the provider. Every
# non-servable state except PURGED, which has no provider object left to revoke.
REVOCATION_REQUIRED_STATES = frozenset({
    STATE_WITHDRAWN,
    STATE_MODERATION_REMOVED,
    STATE_ACCOUNT_DEACTIVATED,
    STATE_HELD,
    STATE_PURGE_PENDING,
})

# PURGED is terminal: the bytes are gone, so no action leads out of it. A UI
# must say so rather than offering a restore that fails at the provider.
TERMINAL_STATES = frozenset({STATE_PURGED})

REASON_CODES = (
    "OWNER_DELETED",
    "OWNER_RESTRICTED_AUDIENCE",
    "ACCOUNT_DELETED",
    "ACCOUNT_SUSPENDED",
    "MODERATION_POLICY_VIOLATION",
    "MODERATION_SAFETY",
    "COPYRIGHT",
    "PRIVACY_REQUEST",
    "LEGAL_ORDER",
    "ABUSE_INVESTIGATION",
    "RETENTION_EXPIRED",
    "ADMIN_DECISION",
    "OTHER",
)

REASON_CODES_REQUIRING_NOTE = frozenset({"OTHER", "LEGAL_ORDER", "ABUSE_INVESTIGATION"})

# ---------------------------------------------------------------------- actions

ACTION_RESTRICT_AUDIENCE = "restrict_audience"
ACTION_WITHDRAW = "withdraw"
ACTION_MODERATION_REMOVE = "moderation_remove"
ACTION_DEACTIVATE_ACCOUNT = "deactivate_account"
ACTION_HOLD = "hold"
ACTION_RELEASE_HOLD = "release_hold"
ACTION_RESTORE = "restore"
ACTION_SCHEDULE_PURGE = "schedule_purge"
ACTION_CANCEL_PURGE = "cancel_purge"
ACTION_PURGE = "purge"

ACTION_PERMISSIONS = {
    ACTION_RESTRICT_AUDIENCE: "media.revoke",
    ACTION_WITHDRAW: "media.revoke",
    ACTION_MODERATION_REMOVE: "media.moderate",
    ACTION_DEACTIVATE_ACCOUNT: "media.revoke",
    ACTION_HOLD: "media.legal_hold",
    ACTION_RELEASE_HOLD: "media.legal_hold",
    ACTION_RESTORE: "media.restore",
    ACTION_SCHEDULE_PURGE: "media.purge",
    ACTION_CANCEL_PURGE: "media.purge",
    ACTION_PURGE: "media.purge",
}

# (action, from_state) -> to_state. Absence means refused -- never defaulted,
# never a denylist. Self-transitions are present on purpose so a retried request
# answers "changed: false" instead of 409; `available_actions` filters them out
# of menus.
TRANSITIONS = {
    (ACTION_RESTRICT_AUDIENCE, STATE_ACTIVE): STATE_AUDIENCE_RESTRICTED,
    (ACTION_RESTRICT_AUDIENCE, STATE_AUDIENCE_RESTRICTED): STATE_AUDIENCE_RESTRICTED,

    (ACTION_WITHDRAW, STATE_ACTIVE): STATE_WITHDRAWN,
    (ACTION_WITHDRAW, STATE_AUDIENCE_RESTRICTED): STATE_WITHDRAWN,
    (ACTION_WITHDRAW, STATE_WITHDRAWN): STATE_WITHDRAWN,

    # Moderation and account deactivation reach in from anywhere non-terminal:
    # a takedown must not be refused because the creator had already restricted
    # the audience, and an account deletion must sweep content in every state.
    (ACTION_MODERATION_REMOVE, STATE_ACTIVE): STATE_MODERATION_REMOVED,
    (ACTION_MODERATION_REMOVE, STATE_AUDIENCE_RESTRICTED): STATE_MODERATION_REMOVED,
    (ACTION_MODERATION_REMOVE, STATE_WITHDRAWN): STATE_MODERATION_REMOVED,
    (ACTION_MODERATION_REMOVE, STATE_ACCOUNT_DEACTIVATED): STATE_MODERATION_REMOVED,
    (ACTION_MODERATION_REMOVE, STATE_MODERATION_REMOVED): STATE_MODERATION_REMOVED,

    (ACTION_DEACTIVATE_ACCOUNT, STATE_ACTIVE): STATE_ACCOUNT_DEACTIVATED,
    (ACTION_DEACTIVATE_ACCOUNT, STATE_AUDIENCE_RESTRICTED): STATE_ACCOUNT_DEACTIVATED,
    (ACTION_DEACTIVATE_ACCOUNT, STATE_WITHDRAWN): STATE_ACCOUNT_DEACTIVATED,
    (ACTION_DEACTIVATE_ACCOUNT, STATE_ACCOUNT_DEACTIVATED): STATE_ACCOUNT_DEACTIVATED,

    # HELD is reachable from every non-terminal state including ACTIVE, because
    # a legal order does not wait for the content to have been removed first.
    # Entering HELD is itself a revocation (see REVOCATION_REQUIRED_STATES):
    # preserving evidence is not publishing it.
    (ACTION_HOLD, STATE_ACTIVE): STATE_HELD,
    (ACTION_HOLD, STATE_AUDIENCE_RESTRICTED): STATE_HELD,
    (ACTION_HOLD, STATE_WITHDRAWN): STATE_HELD,
    (ACTION_HOLD, STATE_MODERATION_REMOVED): STATE_HELD,
    (ACTION_HOLD, STATE_ACCOUNT_DEACTIVATED): STATE_HELD,
    (ACTION_HOLD, STATE_PURGE_PENDING): STATE_HELD,
    (ACTION_HOLD, STATE_HELD): STATE_HELD,

    # Releasing a hold lands on WITHDRAWN, never on ACTIVE. Whatever made the
    # content unavailable before the hold is not undone by the hold ending, and
    # a release must not be a backdoor to republication.
    (ACTION_RELEASE_HOLD, STATE_HELD): STATE_WITHDRAWN,

    (ACTION_RESTORE, STATE_AUDIENCE_RESTRICTED): STATE_ACTIVE,
    (ACTION_RESTORE, STATE_WITHDRAWN): STATE_ACTIVE,
    (ACTION_RESTORE, STATE_MODERATION_REMOVED): STATE_ACTIVE,
    (ACTION_RESTORE, STATE_ACCOUNT_DEACTIVATED): STATE_ACTIVE,
    (ACTION_RESTORE, STATE_PURGE_PENDING): STATE_WITHDRAWN,
    (ACTION_RESTORE, STATE_ACTIVE): STATE_ACTIVE,
    # No (ACTION_RESTORE, STATE_HELD): a hold is released, not restored through.
    # No (ACTION_RESTORE, STATE_PURGED): the bytes are gone.

    (ACTION_SCHEDULE_PURGE, STATE_WITHDRAWN): STATE_PURGE_PENDING,
    (ACTION_SCHEDULE_PURGE, STATE_MODERATION_REMOVED): STATE_PURGE_PENDING,
    (ACTION_SCHEDULE_PURGE, STATE_ACCOUNT_DEACTIVATED): STATE_PURGE_PENDING,
    (ACTION_SCHEDULE_PURGE, STATE_PURGE_PENDING): STATE_PURGE_PENDING,
    # No (ACTION_SCHEDULE_PURGE, STATE_ACTIVE): destruction is never one step
    # from a live asset, and no (…, STATE_HELD): a hold must be released first.

    (ACTION_CANCEL_PURGE, STATE_PURGE_PENDING): STATE_WITHDRAWN,
    (ACTION_CANCEL_PURGE, STATE_WITHDRAWN): STATE_WITHDRAWN,

    (ACTION_PURGE, STATE_PURGE_PENDING): STATE_PURGED,
}

# Actions that destroy bytes. Legal hold is checked against exactly this set --
# which is what makes "hold blocks destruction" and "hold does not grant access"
# the same rule rather than two rules that can drift apart. Revocation is
# deliberately absent: a held asset is revoked, not retrievable.
DESTRUCTIVE_ACTIONS = frozenset({ACTION_PURGE})

# Menu order: least to most destructive, so the nearest item is recoverable and
# "delete permanently" is furthest to reach.
ACTION_ORDER = (
    ACTION_RESTRICT_AUDIENCE,
    ACTION_WITHDRAW,
    ACTION_MODERATION_REMOVE,
    ACTION_DEACTIVATE_ACCOUNT,
    ACTION_RESTORE,
    ACTION_HOLD,
    ACTION_RELEASE_HOLD,
    ACTION_SCHEDULE_PURGE,
    ACTION_CANCEL_PURGE,
    ACTION_PURGE,
)

STEP_UP_TTL_SECONDS = 300

# ------------------------------------------------------------ revocation states

REVOCATION_NOT_REQUIRED = "REVOCATION_NOT_REQUIRED"
REVOCATION_PENDING = "REVOCATION_PENDING"
REVOCATION_CONFIRMED = "REVOCATION_CONFIRMED"
REVOCATION_FAILED = "REVOCATION_FAILED"

REVOCATION_STATES = (
    REVOCATION_NOT_REQUIRED,
    REVOCATION_PENDING,
    REVOCATION_CONFIRMED,
    REVOCATION_FAILED,
)

# ------------------------------------------------------------- access decisions

ACCESS_PUBLIC = "public"
ACCESS_SIGNED = "signed"

# An AUDIENCE_RESTRICTED grant is bounded by this, not by the provider's idea of
# a sensible token lifetime. The existing signer in services/mux_live_service.py
# mints `exp = (now // 3600 + 6) * 3600`, i.e. five to six hours -- fine for a
# replay URL that only needs to outlive the asset's duration, far too long for a
# privacy window, because a token minted moments before a revocation stays good
# for most of a working day. Bucketing is kept (a token that changes on every
# poll restarts the player); the horizon is not.
RESTRICTED_TOKEN_TTL_SECONDS = 300
TOKEN_BUCKET_SECONDS = 60

PROVIDER_MUX = "mux"
PROVIDER_R2 = "r2"

# Whether revoking access at the origin actually stops a client retrieving the
# bytes. For Mux, deleting the playback id does: the edge answers 404 at once.
# For R2 it does not -- objects are served from a public bucket with
# `cache-control: public, max-age=31536000, immutable` and there is no
# edge-purge capability anywhere in this repository, so the origin can forget an
# object a cache will happily serve for a year. The audio authority states the
# same dependency and solves it by deleting the bytes, which is the only thing
# that reaches an edge.
#
# This map exists so the limitation is declared rather than discovered. A caller
# that revokes R2-hosted media must not report the media as inaccessible.
REVOCATION_REACHES_EDGE = {
    PROVIDER_MUX: True,
    PROVIDER_R2: False,
}


class AuthorityError(Exception):
    """A refusal with a stable machine-readable code.

    ``error_code`` is what the clients read: ``pulseApi`` in the native app
    ignores ``code``, so a refusal that only set ``code`` would arrive as a
    generic failure and every distinct reason would collapse into one.
    """

    def __init__(self, error_code, message, status=400):
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.status = status


class AccessDecision:
    """The answer to "may this viewer retrieve these bytes right now".

    Falsy when refused, so ``if not decide_media_access(...)`` is the safe
    shape and a caller that forgets to inspect ``policy`` still fails closed.
    """

    __slots__ = ("granted", "policy", "ttl_seconds", "reason_code", "message")

    def __init__(self, granted, policy=None, ttl_seconds=0, reason_code="", message=""):
        self.granted = bool(granted)
        self.policy = policy
        self.ttl_seconds = int(ttl_seconds or 0)
        self.reason_code = reason_code
        self.message = message

    def __bool__(self):
        return self.granted

    def __repr__(self):  # pragma: no cover - diagnostics only
        if self.granted:
            return "<AccessDecision GRANT %s ttl=%ds>" % (self.policy, self.ttl_seconds)
        return "<AccessDecision REFUSE %s>" % (self.reason_code,)


def normalize_state(value):
    """Map a stored ``lifecycle_state`` onto a known state.

    Unrecognised and empty values become ``ACTIVE`` because that is what a row
    written before the column existed means. That default is only safe because
    it is never the sole gate: ``is_retrievable`` also reads the legacy status
    columns, so a Reel whose ``status`` is already ``'deleted'`` is refused even
    with no lifecycle state to speak for it. Backfill belongs in a migration,
    not in an inference here -- inferring would let a typo resurrect removed
    content.
    """
    state = str(value or "").strip().upper()
    return state if state in LIFECYCLE_STATES else STATE_ACTIVE


def normalize_revocation_state(value):
    state = str(value or "").strip().upper()
    return state if state in REVOCATION_STATES else REVOCATION_NOT_REQUIRED


def state_from_legacy(row):
    """Read the canonical state out of the columns production actually writes.

    Nothing has written ``lifecycle_state`` yet -- the column does not exist on
    any media table outside the audio subsystem, which is a large part of why
    this defect was possible -- so the mapping below is what the state machine
    runs on until the ledger is backfilled. It is also what keeps working
    afterwards: the old writers are not being removed, and two writers that
    disagree are worse than one that is merely old.

    The vocabulary has drifted by surface. Reels say ``status='deleted'``,
    videos say ``status='archived'``, and both mean "the owner took it down".
    A predicate that only knew one of them would under-report by the size of
    the other -- 12 archived videos and 1 deleted reel in production today.
    """
    row = row or {}
    explicit = str(row.get("lifecycle_state") or "").strip().upper()
    if explicit in LIFECYCLE_STATES:
        return explicit

    if str(row.get("purged_at") or "").strip():
        return STATE_PURGED
    if _truthy(row.get("legal_hold")):
        return STATE_HELD
    if str(row.get("purge_scheduled_at") or "").strip():
        return STATE_PURGE_PENDING

    status = str(row.get("status") or "").strip().lower()
    if status in {"deleted", "archived", "removed", "withdrawn"}:
        return STATE_WITHDRAWN

    if str(row.get("takedown_at") or "").strip() or _truthy(row.get("is_takedown")):
        return STATE_MODERATION_REMOVED
    if str(row.get("moderation_status") or "").strip().lower() in {"removed", "blocked", "rejected"}:
        return STATE_MODERATION_REMOVED

    if str(row.get("deleted_at") or "").strip():
        return STATE_WITHDRAWN
    if str(row.get("owner_status") or "").strip().lower() in {"deleted", "deactivated", "suspended"}:
        return STATE_ACCOUNT_DEACTIVATED

    # Reel audience lives on pulse_posts.visibility, where only 'public' is
    # everyone. 'followers', 'private' and 'reel_only' are all narrower, and
    # 'reel_only' in particular is content deliberately kept off the feed -- its
    # poster being publicly fetchable is the exact leak this module closes.
    visibility = str(row.get("visibility") or "").strip().lower()
    if visibility and visibility != "public":
        return STATE_AUDIENCE_RESTRICTED

    return STATE_ACTIVE


def _truthy(value):
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "t", "yes", "y"}
    return bool(value)


def is_retrievable(ref):
    """Whether bytes may be handed out for this provider ref at all.

    The state question only. Per-viewer authorisation is ``decide_media_access``;
    this is the part that must be true before the viewer is even considered, and
    it is what read paths can call directly.

    A failed revocation refuses here. That is the point of recording the failure:
    if the provider call did not land, the media is presumed still retrievable
    from the outside, and the one thing the application must not do is start
    handing the URL out again as though nothing happened.
    """
    ref = ref or {}
    if state_from_legacy(ref) not in SERVABLE_STATES:
        return False
    if normalize_revocation_state(ref.get("revocation_state")) in {
        REVOCATION_PENDING,
        REVOCATION_FAILED,
    }:
        return False
    return True


def requires_revocation(state):
    """Whether entering ``state`` obliges the provider's public address to die."""
    return normalize_state(state) in REVOCATION_REQUIRED_STATES


def revocation_reaches_edge(provider):
    """Whether revoking at the origin actually stops retrieval for ``provider``.

    False for R2. A caller must not promise a user that their media is
    inaccessible when the honest answer is that the origin forgot it and a cache
    did not.
    """
    return bool(REVOCATION_REACHES_EDGE.get(str(provider or "").strip().lower(), False))


def normalize_reason_code(value):
    code = str(value or "").strip().upper()
    if code not in REASON_CODES:
        raise AuthorityError(
            "media_reason_code_invalid",
            "Choose a reason: " + ", ".join(REASON_CODES) + ".",
            400,
        )
    return code


def validate_reason(action, reason_code, reason_note):
    """Return ``(code, note)`` or raise.

    A purge always needs a note, and so do the codes whose meaning is not
    carried by the code itself. ``LEGAL_ORDER`` and ``ABUSE_INVESTIGATION`` are
    in that set because they are the two that will later be read by someone
    asking which order, which investigation.
    """
    code = normalize_reason_code(reason_code)
    note = str(reason_note or "").strip()[:2000]
    if not note and (code in REASON_CODES_REQUIRING_NOTE or action in DESTRUCTIVE_ACTIONS):
        raise AuthorityError(
            "media_reason_note_required",
            "Add a short note explaining this decision.",
            400,
        )
    return code, note


def require_permission(actor, permission, permission_check):
    """Raise unless ``actor`` holds ``permission``.

    No identity is 401, a known identity without the grant is 403. Collapsing
    them tells an ordinary user the endpoint exists and tells a real admin
    missing one grant nothing useful.
    """
    if permission not in MEDIA_PERMISSIONS:
        raise AuthorityError("media_permission_unknown", "Unknown media permission.", 500)
    if not actor:
        raise AuthorityError(
            "media_authority_required",
            "Sign in with an account that has media lifecycle authority.",
            401,
        )
    if not permission_check(actor, permission):
        raise AuthorityError(
            "media_permission_denied",
            "This account does not hold the %s permission." % permission,
            403,
        )
    return actor


def granted_permissions(actor, permission_check):
    """Which media permissions this actor holds, one bool each.

    Every permission is reported explicitly, including the false ones: a map
    that omitted what the actor lacks is indistinguishable from a map built
    against an older, shorter permission list, and a client reads a missing key
    as "not granted" either way -- silently hiding a real grant instead of
    failing loudly.

    Advisory only. Each endpoint re-resolves the actor and re-checks, so a
    client that ignores this entirely gets identical refusals.
    """
    names = sorted(MEDIA_PERMISSIONS)
    if not actor:
        return {name: False for name in names}
    return {name: bool(permission_check(actor, name)) for name in names}


def plan_transition(action, current_state, *, expected_state=None, legal_hold=False):
    """Decide what ``action`` does to a ref in ``current_state``.

    Returns ``(new_state, changed)``. ``changed`` is False when the action was
    already applied, so a retried request answers 200 rather than erroring for
    work that succeeded.

    ``expected_state`` is the caller's view of the world, and a disagreement is
    a 409. This is the whole defence against the race where content becomes
    private between an authorisation check and a token being issued: the token
    issuer passes the state it authorised against, and if the stored state moved
    underneath it the issuance is refused instead of minting a credential for a
    decision that is no longer true.

    Legal hold is checked against ``DESTRUCTIVE_ACTIONS`` only -- it blocks
    destruction and says nothing about access, because retained evidence is not
    published evidence.
    """
    current = normalize_state(current_state)
    if expected_state:
        expected = normalize_state(expected_state)
        if expected != current:
            raise AuthorityError(
                "media_state_conflict",
                "This media is now %s, not %s. Reload and try again." % (current, expected),
                409,
            )
    if legal_hold and action in DESTRUCTIVE_ACTIONS:
        raise AuthorityError(
            "media_legal_hold",
            "This media is under legal hold and cannot be destroyed. Release the hold first.",
            409,
        )
    key = (action, current)
    if key not in TRANSITIONS:
        raise AuthorityError(
            "media_transition_refused",
            "Cannot %s media that is %s." % (action.replace("_", " "), current),
            409,
        )
    new_state = TRANSITIONS[key]
    return new_state, new_state != current


def plan_revocation(new_state):
    """The revocation state that must be written in the same transaction.

    Returning ``PENDING`` rather than nothing is the fail-safe: there is no
    window in which the lifecycle state says private and nothing is owed at the
    provider. A crash between the two writes would otherwise leave media public
    with no record that anyone intended otherwise, which is indistinguishable
    from today's behaviour.
    """
    return REVOCATION_PENDING if requires_revocation(new_state) else REVOCATION_NOT_REQUIRED


def available_actions(state, *, legal_hold=False, permissions=None):
    """Which actions a ref in ``state`` can be offered, in menu order.

    Read out of ``TRANSITIONS`` and ``ACTION_PERMISSIONS`` rather than restated,
    so a menu can neither offer an action ``plan_transition`` would refuse nor
    hide one the actor may take. Self-transitions are omitted: they exist for
    retry-safety on the endpoint, and offering "Restore" on something already
    active reads as a broken state display.
    """
    current = normalize_state(state)
    actions = []
    for action in ACTION_ORDER:
        if (action, current) not in TRANSITIONS:
            continue
        if TRANSITIONS[(action, current)] == current:
            continue
        if legal_hold and action in DESTRUCTIVE_ACTIONS:
            continue
        if permissions is not None and not permissions.get(permission_for_action(action)):
            continue
        actions.append(action)
    return actions


def permission_for_action(action):
    """Permission for ``action``, raising rather than defaulting.

    A ``.get()`` here would return None for an action someone added to
    ``ACTION_ORDER`` without a permission, and ``permissions.get(None)`` is
    falsy, so the action would silently vanish from every menu instead of
    failing. An unmapped action is a programming error and should say so.
    """
    try:
        return ACTION_PERMISSIONS[action]
    except KeyError:
        raise AuthorityError(
            "media_action_unmapped",
            "Action %r has no permission mapping." % (action,),
            500,
        )


def decide_media_access(
    *,
    ref,
    content_ref,
    viewer_may_view,
    thumbnail_signing_available=False,
    now=None,
):
    """WHO is requesting WHAT media for WHICH content under WHICH current state.

    Every argument is keyword-only and ``viewer_may_view`` has no default,
    because the one thing this function must never do is grant by omission.

    ``ref`` is a stored provider reference, never a playback id off the request.
    ``content_ref`` is the content the caller believes the media belongs to;
    a mismatch against the ref's own stored back-pointer is refused, which is
    what stops a viewer authorised for their own content from pointing the
    request at someone else's asset.

    None of the following is grounds to grant, and each is listed because it is
    something this codebase currently treats as sufficient:

    * a playback id exists -- every read path's present logic
    * a poster URL exists -- three separate string-concatenation sites
    * the viewer once saw it -- nothing revokes on an audience narrowing
    * the URL is syntactically valid -- an assumption written into the share path
    * the asset exists at the provider -- 41 assets exist with no application row
      at all, and they serve 200 right now
    """
    ref = ref or {}
    state = state_from_legacy(ref)

    if state not in SERVABLE_STATES:
        return AccessDecision(
            False,
            reason_code="media_state_not_servable",
            message="This media is no longer available.",
        )

    revocation = normalize_revocation_state(ref.get("revocation_state"))
    if revocation == REVOCATION_PENDING:
        return AccessDecision(
            False,
            reason_code="media_revocation_pending",
            message="This media is being withdrawn.",
        )
    if revocation == REVOCATION_FAILED:
        # Failure never re-opens access. The media is presumed still retrievable
        # from outside; handing out the URL would add a first-party leak to a
        # provider-side one.
        return AccessDecision(
            False,
            reason_code="media_revocation_failed",
            message="This media is unavailable.",
        )

    if not _refs_agree(ref, content_ref):
        return AccessDecision(
            False,
            reason_code="media_content_mismatch",
            message="This media does not belong to that content.",
        )

    if not viewer_may_view:
        return AccessDecision(
            False,
            reason_code="media_audience_refused",
            message="You do not have access to this media.",
        )

    if state == STATE_ACTIVE:
        return AccessDecision(True, policy=ACCESS_PUBLIC, ttl_seconds=0)

    # AUDIENCE_RESTRICTED. A signed grant is only honest if every surface the
    # playback id addresses can be signed -- and the only signer in this
    # repository mints `aud: "v"`, video. There is no `aud: "t"` issuer, so a
    # restricted asset served signed would have a permanently broken poster that
    # no code can fix: a public Reel given a signed-only configuration becomes
    # unavailable rather than private. Refusing here is the loud version of that
    # failure; falling back to the public URL would be the silent version, and
    # the silent version defeats the entire scheme.
    if not thumbnail_signing_available:
        return AccessDecision(
            False,
            reason_code="media_signing_incomplete",
            message="This media is unavailable.",
        )

    return AccessDecision(True, policy=ACCESS_SIGNED, ttl_seconds=restricted_token_ttl(now=now))


def _refs_agree(ref, content_ref):
    """Whether a provider ref belongs to the content the caller named.

    An unknown back-pointer is accepted and a *known, different* one is refused.
    That asymmetry is deliberate: 41 provider assets have no application row of
    any kind, so requiring a match would make orphan media undecidable rather
    than merely unattributed -- and the state checks above have already run.
    What must never pass is a ref that demonstrably belongs elsewhere.
    """
    content_ref = content_ref or {}
    want_type = str(content_ref.get("content_type") or "").strip().lower()
    want_id = content_ref.get("content_id")
    have_type = str(ref.get("content_type") or "").strip().lower()
    have_id = ref.get("content_id")
    if not have_type or have_id in (None, "", 0):
        return True
    if not want_type or want_id in (None, "", 0):
        return False
    return have_type == want_type and str(have_id) == str(want_id)


def restricted_token_ttl(*, now=None):
    """Seconds a restricted-media token may live, bucketed.

    Bucketed so a status poll does not mint a new URL and restart the player,
    short so a token minted just before a revocation is not still working hours
    later. ``now`` is injected rather than read so the bucket boundary is
    testable without waiting for one.
    """
    ttl = max(TOKEN_BUCKET_SECONDS, int(RESTRICTED_TOKEN_TTL_SECONDS))
    if now is None:
        return ttl
    elapsed = int(now) % TOKEN_BUCKET_SECONDS
    return ttl - elapsed


def unavailable_media_payload(reason_state=None):
    """What a client gets in place of media it may no longer have.

    Every URL is blank and the poster goes with them. A poster is not a
    thumbnail here -- for a restricted or removed Reel it is the creator's own
    frame, published to the surface the audience setting existed to keep it off.
    It drops with the caption, the title and the author name: all four or none.
    The native share path already reasons this way; this is the same rule for
    every other surface.
    """
    return {
        "media_unavailable": True,
        "media_unavailable_state": normalize_state(reason_state) if reason_state else STATE_WITHDRAWN,
        "playback_id": "",
        "playback_url": "",
        "poster_url": "",
        "thumbnail_url": "",
        "preview_url": "",
        "download_url": "",
        "caption": "",
        "title": "",
        "author_name": "",
    }


# ------------------------------------------------------- reconciliation classes

CLASS_HEALTHY = "HEALTHY"
CLASS_PRIVACY_DEFECT = "PRIVACY_DEFECT"
CLASS_AVAILABILITY_DEFECT = "AVAILABILITY_DEFECT"
CLASS_ORPHAN_DEFECT = "ORPHAN_DEFECT"
CLASS_POTENTIALLY_HEALTHY = "POTENTIALLY_HEALTHY"
CLASS_INVESTIGATE = "INVESTIGATE"
CLASS_UNKNOWN = "UNKNOWN"

# Provider statuses that mean the asset is broken, not protected.
PROVIDER_BROKEN_STATUSES = frozenset({"errored"})


def classify_reconciliation(*, lifecycle_state, provider_status, wire_accessible, has_application_row):
    """Classify one provider ref against what the wire actually answered.

    Provider state is consulted *before* the wire result, and that ordering is
    the entire lesson of this investigation. HTTP 412 on a Mux stream and 400 on
    its poster were previously read as "signed playback, token missing" and
    therefore as privacy success. Probing assets whose state was independently
    known from the API showed 412/400 is what an ``errored`` asset returns --
    ingest that never received enough video, or an input that failed to
    download. Counting those as protected inflated the safe population by every
    broken asset in it.

    So a broken asset is ``INVESTIGATE``, never ``HEALTHY``. The same applies to
    an ``mp4_support: "none"`` asset answering 404 on ``medium.mp4`` while its
    HLS stream returns 200 on the same playback id: a disabled derivative is a
    distribution setting, not an access control.

    ``wire_accessible`` must come from a probe, not from a URL existing. The
    caller is responsible for probing negative controls in the same run -- a
    playback id that never existed, and a well-formed random one, both of which
    must answer 404 -- so that a change in provider semantics shows up as a
    control failure instead of as a fleet-wide false "healthy".
    """
    state = normalize_state(lifecycle_state)
    status = str(provider_status or "").strip().lower()

    if status in PROVIDER_BROKEN_STATUSES:
        return CLASS_INVESTIGATE

    if not has_application_row:
        # Reachable bytes that no application row points at. Unreachable by any
        # revocation job that keys off application state, which is why the ledger
        # has to be written at creation rather than derived later.
        return CLASS_ORPHAN_DEFECT if wire_accessible else CLASS_POTENTIALLY_HEALTHY

    servable = state in SERVABLE_STATES

    if servable and wire_accessible:
        return CLASS_HEALTHY
    if servable and not wire_accessible:
        # Public content that cannot be played is a quality bug. Kept as its own
        # class so it never reads as a privacy win.
        return CLASS_AVAILABILITY_DEFECT
    if not servable and wire_accessible:
        return CLASS_PRIVACY_DEFECT
    if state == STATE_PURGED:
        return CLASS_HEALTHY
    if not servable and not wire_accessible:
        # "Private and unreachable" is only healthy once the provider confirms
        # the playback id is gone. A status code alone is what caused the
        # misreading above, so the honest answer without that confirmation is
        # "probably".
        return CLASS_POTENTIALLY_HEALTHY
    return CLASS_UNKNOWN
