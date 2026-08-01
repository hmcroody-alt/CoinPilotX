"""The canonical, machine-readable map of what PulseSoc *is*.

``undx_capability_registry`` answers a narrow question: which actions may UNDX
execute right now. This module answers the wider one that has to be settled
before any new capability can be written: what does the product contain, which
screen shows it, which backend route serves it, which domain service owns the
operation, and — the part that decides whether an agent may touch it at all —
what is actually true about ownership, reversibility and verification.

Three properties make this a map rather than a document.

**It is one source, three views.** ``agent_capability_view``,
``product_knowledge_view`` and ``native_navigation_view`` are all projections of
``RECORDS``. Hand-maintaining three lists guarantees they diverge; deriving them
guarantees they cannot.

**It does not restate the registry.** For every capability that is already live,
the record carries only the map-specific fields, and reads risk, confirmation,
verifier, result card, native route, target field and undo from the registered
``CapabilitySpec``. If someone changes a capability's risk class in the registry,
this map changes with it. There is no second copy to forget.

**"Verified" is a claim about executed code, not about reading the source.** A
record may only be ``verified`` if it resolves to a registered capability whose
executor and verifier both exist and are exercised by the test suite. Everything
found by reading the codebase — however carefully — is
``implemented_unverified`` at best. That distinction is the whole point: an
agent that treats "I found a route that looks right" as "this works" will
eventually send a message to the wrong person and report success.

The negative statuses are load-bearing too. ``service_missing`` means the
behaviour exists only inside a request handler and has no callable domain
operation, so wiring it to UNDX would mean putting raw database access in the
agent runtime. That is precisely the shortcut this file exists to prevent: the
record says the service is missing, and the fix is to write the service.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from services.undx_agent_contracts import CardType, ConfirmationPolicy, RiskLevel
from services import undx_capability_registry as _registry


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------


class ImplementationStatus:
    """How much is known about a capability, ordered from most to least proven.

    The boundary that matters is between ``VERIFIED`` and everything below it.
    Only ``VERIFIED`` may be read as "this executes correctly", and only code
    that a test drives end to end earns it.
    """

    #: Registered, executable, and exercised by the test suite through its verifier.
    VERIFIED = "verified"
    #: The code exists and reads correctly, but nothing proves it runs. Source
    #: reading is not evidence of execution.
    IMPLEMENTED_UNVERIFIED = "implemented_unverified"
    #: Part of the behaviour exists; a named piece is absent or unsafe.
    PARTIALLY_IMPLEMENTED = "partially_implemented"
    #: The behaviour lives inside a request handler with no callable domain
    #: operation behind it. Wiring this to UNDX requires writing a service first.
    SERVICE_MISSING = "service_missing"
    #: No implementation exists anywhere in the product.
    UNSUPPORTED = "unsupported"
    #: Implemented, but deliberately kept out of agent reach.
    INTENTIONALLY_DISABLED = "intentionally_disabled"

    ALL = frozenset({
        VERIFIED, IMPLEMENTED_UNVERIFIED, PARTIALLY_IMPLEMENTED,
        SERVICE_MISSING, UNSUPPORTED, INTENTIONALLY_DISABLED,
    })

    #: Statuses that must never resolve to a registered, executable capability.
    #: An ``unsupported`` record that appears in the registry is not a
    #: documentation error — it is a promise the runtime cannot keep.
    NOT_EXECUTABLE = frozenset({SERVICE_MISSING, UNSUPPORTED, INTENTIONALLY_DISABLED})


class ReadinessClass:
    """The verdict on whether a capability may be wired to the agent.

    These are not severities. Each one names a different *missing thing*, and
    therefore a different next action.
    """

    #: Owner-scoped, has a domain operation, sets a desired state, and can be
    #: read back. Nothing blocks implementation.
    READY_TO_WIRE = "READY TO WIRE"
    #: The behaviour is only reachable through a request handler. Write the
    #: service before writing the capability.
    DOMAIN_SERVICE_REQUIRED = "DOMAIN SERVICE REQUIRED"
    #: The existing path lets a caller reach or learn about a row they do not
    #: own. Fix the authorization before exposing it.
    AUTHORIZATION_DEFECT = "AUTHORIZATION DEFECT"
    #: The endpoint flips current state instead of setting a desired one, so a
    #: retry undoes the action. Unsafe for an agent at any risk class.
    TOGGLE_HAZARD = "TOGGLE HAZARD"
    #: No independent read exists to confirm the write landed.
    VERIFIER_REQUIRED = "VERIFIER REQUIRED"
    #: Only meaningful with the screen the user is looking at, which the server
    #: does not have until the trusted native context envelope exists.
    NATIVE_CONTEXT_REQUIRED = "NATIVE CONTEXT REQUIRED"
    #: Should not be an agent capability.
    UNSUPPORTED = "UNSUPPORTED"

    ALL = frozenset({
        READY_TO_WIRE, DOMAIN_SERVICE_REQUIRED, AUTHORIZATION_DEFECT,
        TOGGLE_HAZARD, VERIFIER_REQUIRED, NATIVE_CONTEXT_REQUIRED, UNSUPPORTED,
    })


class AuthorizationScope:
    """Whose data the operation reaches, as the code actually enforces it."""

    #: Query is scoped by the caller's id in the same statement that loads the row.
    SELF_ONLY = "self_account_only"
    #: Acts on another account, but the caller's id bounds the edge being changed.
    DIRECTED_AT_OTHER_USER = "directed_at_other_user"
    #: Membership in a conversation or group is checked before access.
    MEMBERSHIP_SCOPED = "membership_scoped"
    #: Public data, no ownership question.
    PUBLIC = "public"
    #: The row is loaded by global id before any ownership check, so a caller
    #: learns whether an id exists regardless of whether they may act on it.
    EXISTENCE_ORACLE = "existence_oracle_defect"
    #: Nothing in the path scopes the query.
    UNSCOPED = "unscoped_defect"
    #: Requires an elevated role.
    PRIVILEGED = "privileged_role"

    ALL = frozenset({
        SELF_ONLY, DIRECTED_AT_OTHER_USER, MEMBERSHIP_SCOPED, PUBLIC,
        EXISTENCE_ORACLE, UNSCOPED, PRIVILEGED,
    })


# ---------------------------------------------------------------------------
# Native routes
# ---------------------------------------------------------------------------

#: Screen name to declared path, mirroring ``mobile-native/src/navigation/linking.ts``.
#:
#: This is a copy, and a copy is a liability, so a test parses ``linking.ts`` and
#: fails if the two disagree. The copy exists because import-time validation has
#: to run in Python with no bundler available: a record naming a screen that does
#: not exist would otherwise produce a deep link that silently lands nowhere.
NATIVE_ROUTES: dict[str, str] = {
    "Dashboard": "/pulse/dashboard",
    "Home": "/pulse",
    "Search": "/pulse/search",
    "Saved": "/pulse/saved",
    "Groups": "/pulse/groups",
    "Live": "/pulse/live",
    "Reels": "/pulse/reels",
    "Status": "/pulse/status",
    "Messenger": "/pulse/messages",
    "Notifications": "/pulse/notifications",
    "PulseAI": "/pulse/ai",
    "Profile": "/pulse/profile",
    "Marketplace": "/pulse/marketplace",
    "Settings": "/pulse/settings",
    "UserDashboard": "/dashboard",
    "DashboardComposeAlias": "/pulse/compose",
    "Music": "/pulse/music",
    "CameraStudio": "/pulse/camera/:mode?",
    "Call": "/pulse/calls/:callId?",
    "Chat": "/pulse/messages/:conversationId",
    "NewChat": "/pulse/messages/new",
    "PostDetail": "/pulse/post/:postId",
    "MarketplaceCreateGateway": "/pulse/marketplace/create",
    "ReelDetail": "/pulse/reels/:reelId",
    "StatusDetail": "/pulse/status/:statusId",
    "MarketplaceDetail": "/pulse/marketplace/:listingId",
    "SellerStore": "/pulse/seller-store",
    "BuyerOrders": "/pulse/orders",
    "BuyerOrderDetail": "/pulse/orders/:orderId",
    "BuyerPurchases": "/pulse/purchases",
    "MerchantApply": "/pulse/merchant/apply",
    "MerchantDashboard": "/pulse/merchant/dashboard",
    "MerchantProfile": "/pulse/merchant/:sellerId",
    "GroupDetail": "/pulse/groups/:groupSlug",
    "LiveDetail": "/pulse/live/:liveId",
    "LiveScheduleGateway": "/pulse/live/schedule",
    "LiveEventCreateGateway": "/pulse/live/events/create",
    "Events": "/pulse/events",
    "EventDetail": "/pulse/events/:eventId",
    "ProfileEdit": "/pulse/profile/edit",
    "ProfileDetail": "/pulse/profile/:profileKey",
    "Premium": "/pulse/premium",
    "CreatorStudio": "/pulse/creator-studio",
    "ContentPlanner": "/pulse/content-planner",
    "Courses": "/pulse/courses",
    "CourseDetail": "/pulse/courses/:courseId",
    "GrowthCenter": "/pulse/growth",
    "IntelligenceCenter": "/pulse/intelligence/:subsystem?",
    "UndxActionCenter": "/pulse/undx/actions",
    "AlertManagement": "/pulse/alerts/:alertId?",
    "CryptoAlertManagement": "/pulse/crypto/alerts",
    "AccountCenter": "/pulse/settings/:section",
    "AccountDevices": "/pulse/settings/devices",
    "AccountHealth": "/pulse/account-health",
    "SafetyHub": "/pulse/safety/:section?",
    "TrustSafety": "/pulse/help",
    "TrustSafetySupport": "/pulse/support",
    "VerificationCenter": "/pulse/verification/:track?",
    "ActivityInbox": "/pulse/activity/:category?",
    "NotificationPreferences": "/pulse/settings/notifications",
}

#: The deep-link schemes the client registers. Mirrors ``linking.prefixes``.
DEEP_LINK_PREFIXES = ("pulsesoc://", "https://pulsesoc.com")

_PARAM = re.compile(r":([A-Za-z_][A-Za-z0-9_]*)\??")


def _route_shape(path: str) -> str:
    """A path with its parameter names erased, for comparing two declarations.

    ``/pulse/alerts/:alert_id`` and ``/pulse/alerts/:alertId`` name the same
    destination; only the argument key differs, and the registry's argument key
    is the one that has to match its own field. Comparing shapes lets a record
    reuse a screen without being forced into the client's parameter spelling.
    """
    return _PARAM.sub(":*", path.rstrip("/") or "/")


#: A trailing ``:name?`` segment, which the client may or may not supply.
_OPTIONAL_TAIL = re.compile(r"/:[A-Za-z_][A-Za-z0-9_]*\?$")


def _route_shapes(path: str) -> frozenset[str]:
    """Every shape a single declaration legitimately matches.

    An optional trailing parameter is exactly that. ``linking.ts`` declares
    ``VerificationCenter`` as ``pulse/verification/:track?`` and
    ``nativeRouteActions.ts`` navigates to it as ``/pulse/verification``; both name
    the same screen, and a record that uses the shorter spelling is not sending the
    user somewhere else. Comparing single shapes rejected that pair, which made the
    map refuse a record that was correct.

    Expanding the optional tails rather than deleting them is what keeps the check
    strict: a *required* parameter still has to appear on both sides, so a record
    that names ``/pulse/settings`` for a screen declared ``/pulse/settings/:section``
    is still caught.
    """
    shapes = {_route_shape(path)}
    trimmed = path.rstrip("/")
    while True:
        stripped = _OPTIONAL_TAIL.sub("", trimmed)
        if stripped == trimmed:
            break
        trimmed = stripped
        shapes.add(_route_shape(trimmed))
    return frozenset(shapes)


#: Every declared route, by shape, for membership tests.
_ROUTE_SHAPES = {shape for path in NATIVE_ROUTES.values() for shape in _route_shapes(path)}


def _segments_match(candidate: str, declared: str) -> bool:
    """Whether one concrete shape satisfies another, segment by segment."""
    left = candidate.strip("/").split("/")
    right = declared.strip("/").split("/")
    if len(left) != len(right):
        return False
    return all(a == b or ":*" in (a, b) for a, b in zip(left, right))


def _route_matches(candidate: str, declared: str) -> bool:
    """Whether ``candidate`` is a path the screen declared as ``declared`` serves.

    Screens are declared as patterns and records name concrete paths, so string
    comparison was the wrong instrument: ``/pulse/settings/language-region`` is
    served by ``AccountCenter``'s ``/pulse/settings/:section``, and refusing it made
    the map reject a record whose route the router does in fact handle.

    Segment count still has to agree, so ``/pulse/settings`` does not pass for
    ``/pulse/settings/:section`` — a required parameter is required.
    """
    return any(_segments_match(c, d)
               for c in _route_shapes(candidate)
               for d in _route_shapes(declared))


#: Screens declared at a literal path, which therefore own it outright.
_LITERAL_OWNERS = {path.rstrip("/"): screen
                   for screen, path in NATIVE_ROUTES.items() if ":" not in path}


# ---------------------------------------------------------------------------
# The record
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProductCapabilityRecord:
    """One thing PulseSoc can do, described well enough to decide about it.

    A record is not a promise that the capability works. It is a statement of
    what was found, how it was found, and what would have to be true before an
    agent could be allowed to invoke it.
    """

    product_area: str
    resource_type: str
    capability_id: str
    description: str
    supported_intents: tuple[str, ...]
    native_screen: str
    native_route: str
    backend_route: str
    domain_service: str
    domain_operation: str
    authentication_required: bool
    authorization_scope: str
    owner_field: str
    target_field: str
    risk_class: str
    confirmation_policy: str
    input_schema: tuple[tuple[str, str], ...]
    output_schema: tuple[tuple[str, str], ...]
    verifier: str
    result_card_type: str
    deep_link_template: str
    undo_capability_id: str
    feature_flag: str
    implementation_status: str
    evidence: tuple[str, ...]
    known_limitations: tuple[str, ...] = ()
    #: The operation flips current state instead of setting a desired one, so a
    #: retry after a timeout undoes the first call. Declared as a field rather
    #: than inferred from ``known_limitations`` prose: a limitation may mention a
    #: toggle in order to say the capability must *avoid* one — social.follow's
    #: does exactly that — and a classifier that greps for the word cannot tell
    #: the warning apart from the defect it warns against.
    toggle_semantics: bool = False
    #: The target cannot be resolved without state only the client holds (what
    #: is on screen, what the device is playing). Blocked on Stage 8.
    requires_native_context: bool = False
    #: No read exists that could confirm the write independently, so no verifier
    #: can be written for it no matter how the capability is shaped.
    read_back_missing: bool = False
    #: Set when the record's operational fields come from a registered
    #: ``CapabilitySpec`` rather than from this file.
    registered: bool = False

    def __post_init__(self) -> None:
        # Import-time failure, deliberately. A malformed record discovered when a
        # planner consults the map is a malformed record discovered in production.
        cid = self.capability_id
        if self.implementation_status not in ImplementationStatus.ALL:
            raise ValueError(f"{cid}: unknown implementation_status {self.implementation_status!r}")
        if self.authorization_scope not in AuthorizationScope.ALL:
            raise ValueError(f"{cid}: unknown authorization_scope {self.authorization_scope!r}")
        if self.risk_class not in RiskLevel.ALL:
            raise ValueError(f"{cid}: unknown risk_class {self.risk_class!r}")
        if self.confirmation_policy not in ConfirmationPolicy.ALL:
            raise ValueError(f"{cid}: unknown confirmation_policy {self.confirmation_policy!r}")
        if self.result_card_type and self.result_card_type not in CardType.ALL:
            raise ValueError(f"{cid}: unknown result_card_type {self.result_card_type!r}")
        if not self.capability_id or not self.description:
            raise ValueError(f"{cid or '<blank>'}: capability_id and description are required")
        if not self.evidence:
            raise ValueError(f"{cid}: every record must cite the source it was read from")

        if self.native_screen:
            if self.native_screen not in NATIVE_ROUTES:
                raise ValueError(f"{cid}: native_screen {self.native_screen!r} is not a declared screen")
            declared_route = NATIVE_ROUTES[self.native_screen]
            if self.native_route:
                # A screen declared at a literal path owns that path outright, so a
                # record naming it must name that screen — otherwise a route claimed
                # by a catch-all pattern would be filed against the catch-all while
                # the user lands somewhere more specific.
                owner = _LITERAL_OWNERS.get(self.native_route.rstrip("/"))
                if owner and owner != self.native_screen:
                    raise ValueError(
                        f"{cid}: native_route {self.native_route!r} belongs to screen "
                        f"{owner!r}, not {self.native_screen!r}"
                    )
                # Catching this pair rather than only checking membership is what
                # stops a record from naming a screen the user would not land on:
                # a route that exists somewhere in the app still sends the person
                # to the wrong place if it is not the route this screen serves.
                if not owner and not _route_matches(self.native_route, declared_route):
                    raise ValueError(
                        f"{cid}: native_route {self.native_route!r} is not the route "
                        f"declared for screen {self.native_screen!r} ({declared_route!r})"
                    )

        if self.deep_link_template:
            if not self.deep_link_template.startswith(DEEP_LINK_PREFIXES):
                raise ValueError(f"{cid}: deep_link_template must use a registered prefix")
            if " " in self.deep_link_template:
                raise ValueError(f"{cid}: deep_link_template must not contain whitespace")

        # A claim of "verified" is a claim about a registered, executable
        # capability. Nothing this file asserts on its own can earn it.
        if self.implementation_status == ImplementationStatus.VERIFIED and not self.registered:
            raise ValueError(
                f"{cid}: only a registered capability may be marked verified; source "
                f"reading yields implemented_unverified at best"
            )
        if self.is_write and self.implementation_status == ImplementationStatus.VERIFIED and not self.verifier:
            raise ValueError(f"{cid}: a verified write must name the verifier that reads it back")
        # Nothing that is not executable may claim a real target the runtime
        # would try to reach.
        if self.implementation_status in ImplementationStatus.NOT_EXECUTABLE and self.registered:
            raise ValueError(f"{cid}: status {self.implementation_status!r} contradicts registration")

    @property
    def is_write(self) -> bool:
        return RiskLevel.is_write(self.risk_class)

    @property
    def is_consequential(self) -> bool:
        return self.risk_class == RiskLevel.CONSEQUENTIAL_WRITE

    @property
    def is_executable(self) -> bool:
        """Whether the runtime can actually dispatch this today."""
        return self.registered and self.capability_id in _registry.REGISTRY


RECORDS: list[ProductCapabilityRecord] = []


def _add(record: ProductCapabilityRecord) -> ProductCapabilityRecord:
    RECORDS.append(record)
    return record


def _live(
    capability_id: str,
    *,
    product_area: str,
    resource_type: str,
    backend_route: str,
    domain_service: str,
    domain_operation: str,
    authorization_scope: str,
    owner_field: str,
    native_screen: str,
    output_schema: tuple[tuple[str, str], ...],
    feature_flag: str,
    evidence: tuple[str, ...],
    known_limitations: tuple[str, ...] = (),
) -> ProductCapabilityRecord:
    """Build a record for a capability the registry already owns.

    Everything operational — risk, confirmation, verifier, card, route, target
    field, undo, argument schema — is read from the ``CapabilitySpec`` rather
    than restated. That is the difference between a map that tracks the system
    and a map that describes what the system looked like when someone last
    edited it. If a capability's risk class is raised in the registry, this
    record reports the new one on the next import, with no edit here.
    """
    spec = _registry.REGISTRY.get(capability_id)
    if spec is None:
        raise ValueError(f"{capability_id}: knowledge map claims a capability the registry does not declare")
    return _add(ProductCapabilityRecord(
        product_area=product_area,
        resource_type=resource_type,
        capability_id=spec.capability_id,
        description=spec.description,
        supported_intents=tuple(spec.intents),
        native_screen=native_screen,
        native_route=spec.native_route,
        backend_route=backend_route,
        domain_service=domain_service,
        domain_operation=domain_operation,
        authentication_required=spec.requires_authentication,
        authorization_scope=authorization_scope,
        owner_field=owner_field,
        target_field=spec.target_field,
        risk_class=spec.risk,
        confirmation_policy=spec.confirmation,
        input_schema=tuple((item.name, item.kind) for item in spec.fields),
        output_schema=output_schema,
        verifier=spec.verifier,
        result_card_type=spec.result_card,
        deep_link_template=f"pulsesoc://{spec.native_route.lstrip('/')}",
        undo_capability_id=spec.undo_capability_id,
        feature_flag=feature_flag,
        implementation_status=ImplementationStatus.VERIFIED,
        evidence=evidence,
        known_limitations=known_limitations,
        registered=True,
    ))


def _mapped(
    capability_id: str,
    *,
    product_area: str,
    resource_type: str,
    description: str,
    supported_intents: tuple[str, ...],
    implementation_status: str,
    evidence: tuple[str, ...],
    risk_class: str = RiskLevel.READ_ONLY,
    confirmation_policy: str = ConfirmationPolicy.NEVER,
    authorization_scope: str = AuthorizationScope.SELF_ONLY,
    native_screen: str = "",
    native_route: str = "",
    backend_route: str = "",
    domain_service: str = "",
    domain_operation: str = "",
    authentication_required: bool = True,
    owner_field: str = "",
    target_field: str = "",
    input_schema: tuple[tuple[str, str], ...] = (),
    output_schema: tuple[tuple[str, str], ...] = (),
    verifier: str = "",
    result_card_type: str = "",
    undo_capability_id: str = "",
    feature_flag: str = "",
    known_limitations: tuple[str, ...] = (),
    toggle_semantics: bool = False,
    requires_native_context: bool = False,
    read_back_missing: bool = False,
) -> ProductCapabilityRecord:
    """Build a record for something found by reading the source.

    The deep link is derived from the route rather than typed, so a record
    cannot carry a link that disagrees with the screen it names.
    """
    route = native_route or (NATIVE_ROUTES.get(native_screen, "") if native_screen else "")
    return _add(ProductCapabilityRecord(
        product_area=product_area,
        resource_type=resource_type,
        capability_id=capability_id,
        description=description,
        supported_intents=supported_intents,
        native_screen=native_screen,
        native_route=route,
        backend_route=backend_route,
        domain_service=domain_service,
        domain_operation=domain_operation,
        authentication_required=authentication_required,
        authorization_scope=authorization_scope,
        owner_field=owner_field,
        target_field=target_field,
        risk_class=risk_class,
        confirmation_policy=confirmation_policy,
        input_schema=input_schema,
        output_schema=output_schema,
        verifier=verifier,
        result_card_type=result_card_type,
        deep_link_template=f"pulsesoc://{route.lstrip('/')}" if route else "",
        undo_capability_id=undo_capability_id,
        feature_flag=feature_flag,
        implementation_status=implementation_status,
        evidence=evidence,
        known_limitations=known_limitations,
        toggle_semantics=toggle_semantics,
        requires_native_context=requires_native_context,
        read_back_missing=read_back_missing,
        registered=False,
    ))


# Shorthands, so a record's substance is not buried under enum paths.
_READ = RiskLevel.READ_ONLY
_WRITE = RiskLevel.REVERSIBLE_WRITE
_GRAVE = RiskLevel.CONSEQUENTIAL_WRITE
_NEVER = ConfirmationPolicy.NEVER
_CONTEXTUAL = ConfirmationPolicy.CONTEXTUAL
_ALWAYS = ConfirmationPolicy.ALWAYS
_VERIFIED = ImplementationStatus.VERIFIED
_UNVERIFIED = ImplementationStatus.IMPLEMENTED_UNVERIFIED
_PARTIAL = ImplementationStatus.PARTIALLY_IMPLEMENTED
_NO_SERVICE = ImplementationStatus.SERVICE_MISSING
_NONE = ImplementationStatus.UNSUPPORTED
_DISABLED = ImplementationStatus.INTENTIONALLY_DISABLED
_SELF = AuthorizationScope.SELF_ONLY
_OTHER = AuthorizationScope.DIRECTED_AT_OTHER_USER
_MEMBER = AuthorizationScope.MEMBERSHIP_SCOPED
_PUBLIC = AuthorizationScope.PUBLIC
_ORACLE = AuthorizationScope.EXISTENCE_ORACLE
_UNSCOPED = AuthorizationScope.UNSCOPED
_PRIVILEGED = AuthorizationScope.PRIVILEGED


# ===========================================================================
# 1. Crypto alerts — the one pack that is fully live
# ===========================================================================
#
# These nine records are the only ones in this file entitled to ``verified``,
# because they are the only ones the runtime executes and the test suite drives
# through a real verifier. Every operational field below is read from the
# registry; what these records add is the part the registry does not carry —
# which service owns the operation and where the ownership check happens.

_ALERT_EVIDENCE = (
    "services/alert_engine.py",
    "services/undx_verification.py",
    "tests/undx_agent/test_crypto_alert_pack.py",
)
_ALERT_OUT = (("alert_id", "int"), ("symbol", "str"), ("condition", "str"),
              ("threshold", "float"), ("status", "str"))

_live(
    "crypto.alerts.list",
    product_area="Crypto alerts", resource_type="alert_rule",
    backend_route="GET /api/pulse/crypto/alerts",
    domain_service="services.alert_engine", domain_operation="list_alert_rules",
    authorization_scope=_SELF, owner_field="user_id",
    native_screen="CryptoAlertManagement", output_schema=_ALERT_OUT,
    feature_flag="UNDX_AGENT_READS_ENABLED",
    evidence=_ALERT_EVIDENCE + ("services/alert_engine.py:581 list_alert_rules",),
)
_live(
    "crypto.alerts.get",
    product_area="Crypto alerts", resource_type="alert_rule",
    backend_route="GET /api/pulse/crypto/alerts/<alert_id>",
    domain_service="services.alert_engine", domain_operation="get_alert_rule",
    authorization_scope=_SELF, owner_field="user_id",
    native_screen="AlertManagement", output_schema=_ALERT_OUT,
    feature_flag="UNDX_AGENT_READS_ENABLED",
    evidence=_ALERT_EVIDENCE + ("services/alert_engine.py:633 get_alert_rule",),
)
_live(
    "crypto.alerts.create",
    product_area="Crypto alerts", resource_type="alert_rule",
    backend_route="POST /api/pulse/crypto/alerts",
    domain_service="services.alert_engine", domain_operation="create_alert_rule",
    authorization_scope=_SELF, owner_field="user_id",
    native_screen="CryptoAlertManagement", output_schema=_ALERT_OUT,
    feature_flag="UNDX_AGENT_WRITES_ENABLED",
    evidence=_ALERT_EVIDENCE + ("services/alert_engine.py:510 create_alert_rule",
                                "services/undx_verification.py:107 crypto_alert_exists"),
    known_limitations=(
        "Undo needs the created row's id, which only exists once the write is "
        "verified; undo_arguments returns None when it is not, so the card "
        "withholds the button rather than sending a delete with a blank target.",
    ),
)
_live(
    "crypto.alerts.pause",
    product_area="Crypto alerts", resource_type="alert_rule",
    backend_route="POST /api/pulse/crypto/alerts/<alert_id>/pause",
    domain_service="services.alert_engine", domain_operation="pause_alert",
    authorization_scope=_SELF, owner_field="user_id",
    native_screen="AlertManagement", output_schema=_ALERT_OUT,
    feature_flag="UNDX_AGENT_WRITES_ENABLED",
    evidence=_ALERT_EVIDENCE + ("services/alert_engine.py:646 pause_alert",
                                "services/undx_verification.py:72 crypto_alert_status"),
)
_live(
    "crypto.alerts.resume",
    product_area="Crypto alerts", resource_type="alert_rule",
    backend_route="POST /api/pulse/crypto/alerts/<alert_id>/resume",
    domain_service="services.alert_engine", domain_operation="resume_alert",
    authorization_scope=_SELF, owner_field="user_id",
    native_screen="AlertManagement", output_schema=_ALERT_OUT,
    feature_flag="UNDX_AGENT_WRITES_ENABLED",
    evidence=_ALERT_EVIDENCE + ("services/alert_engine.py:650 resume_alert",),
)
_live(
    "crypto.alerts.update",
    product_area="Crypto alerts", resource_type="alert_rule",
    backend_route="PATCH /api/pulse/crypto/alerts/<alert_id>",
    domain_service="services.alert_engine", domain_operation="update_alert_rule",
    authorization_scope=_SELF, owner_field="user_id",
    native_screen="AlertManagement", output_schema=_ALERT_OUT,
    feature_flag="UNDX_AGENT_WRITES_ENABLED",
    evidence=_ALERT_EVIDENCE + ("services/alert_engine.py:673 update_alert_rule",
                                "services/undx_verification.py:155 crypto_alert_threshold"),
    known_limitations=(
        "No undo: reversing an update needs the prior threshold, which the "
        "runtime does not capture before the write.",
    ),
)
_live(
    "crypto.alerts.delete",
    product_area="Crypto alerts", resource_type="alert_rule",
    backend_route="DELETE /api/pulse/crypto/alerts/<alert_id>",
    domain_service="services.alert_engine", domain_operation="delete_alert",
    authorization_scope=_SELF, owner_field="user_id",
    native_screen="CryptoAlertManagement", output_schema=_ALERT_OUT,
    feature_flag="UNDX_AGENT_WRITES_ENABLED",
    evidence=_ALERT_EVIDENCE + ("services/alert_engine.py:654 delete_alert",
                                "services/undx_verification.py:200 crypto_alert_deleted"),
    known_limitations=("Destructive and not reversible; confirmation is always required.",),
)

# ===========================================================================
# 2. Notifications
# ===========================================================================

_live(
    "notifications.preference.read",
    product_area="Notifications", resource_type="notification_preference",
    backend_route="GET /api/pulse/settings/notifications",
    domain_service="services.pulsesoc_notification_system",
    domain_operation="get_preferences",
    authorization_scope=_SELF, owner_field="user_id",
    native_screen="NotificationPreferences",
    output_schema=(("category", "str"), ("push", "bool")),
    feature_flag="UNDX_AGENT_READS_ENABLED",
    evidence=("services/pulsesoc_notification_system.py:1016 get_preferences",
              "tests/undx_agent/test_end_to_end.py"),
)
_live(
    "notifications.preference.update",
    product_area="Notifications", resource_type="notification_preference",
    backend_route="POST /api/pulse/settings/notifications",
    domain_service="services.pulsesoc_notification_system",
    domain_operation="update_preferences",
    authorization_scope=_SELF, owner_field="user_id",
    native_screen="NotificationPreferences",
    output_schema=(("category", "str"), ("push", "bool")),
    feature_flag="UNDX_AGENT_WRITES_ENABLED",
    evidence=("services/pulsesoc_notification_system.py:1027 update_preferences",
              "services/undx_verification.py:230 notification_preference_value",
              "tests/undx_agent/test_end_to_end.py"),
    known_limitations=(
        "Undoes itself only through an argument map that negates `push`; "
        "replaying the stored arguments would re-apply the change.",
    ),
)

_mapped(
    "notifications.feed.list",
    product_area="Notifications", resource_type="notification",
    description="List the account's notification feed.",
    supported_intents=("show my notifications", "what did I miss"),
    native_screen="Notifications",
    backend_route="GET /api/pulse/notifications",
    domain_service="services.pulsesoc_notification_system",
    domain_operation="list_notifications",
    authorization_scope=_SELF, owner_field="user_id",
    output_schema=(("notification_id", "int"), ("category", "str"), ("read", "bool")),
    implementation_status=_UNVERIFIED,
    evidence=("services/pulsesoc_notification_system.py",),
    known_limitations=("Category vocabulary must map through undx_agent_tools.CATEGORY_ALIASES; "
                       "storage keys and app-surface words are not the same strings.",),
)
_mapped(
    "notifications.feed.mark_read",
    product_area="Notifications", resource_type="notification",
    description="Mark notifications as read.",
    supported_intents=("mark my notifications read",),
    risk_class=_WRITE, confirmation_policy=_NEVER,
    native_screen="Notifications",
    backend_route="POST /api/pulse/notifications/read",
    domain_service="services.pulsesoc_notification_system",
    domain_operation="mark_read",
    authorization_scope=_SELF, owner_field="user_id", target_field="notification_id",
    implementation_status=_PARTIAL,
    evidence=("services/pulsesoc_notification_system.py",),
    known_limitations=(
        "No read-back exists that reports a single notification's read flag, so "
        "the write cannot be verified. Irreversible in practice: there is no "
        "mark-unread.",
    ),
)

# ===========================================================================
# 3. Authentication
# ===========================================================================
#
# Authentication is mapped so that it is explicitly *out of scope*, not merely
# absent. An agent that can start a login flow, rotate a session, or reset a
# password is an agent that can be talked into account takeover by a crafted
# message. These records exist to make that a declared decision.

_mapped(
    "auth.session.describe",
    product_area="Authentication", resource_type="session",
    description="Report who the current session belongs to.",
    supported_intents=("who am I signed in as",),
    backend_route="internal: api_account_user() -> require_account() -> account_user_id()",
    domain_service="bot", domain_operation="api_account_user",
    authorization_scope=_SELF, owner_field="user_id",
    output_schema=(("user_id", "int"), ("username", "str")),
    implementation_status=_UNVERIFIED,
    evidence=("bot.py api_account_user / require_account / account_user_id",),
    known_limitations=("Resolves from a session cookie or an HMAC mobile bearer; the "
                       "agent runtime receives the resolved user_id, never the credential.",),
)
_mapped(
    "auth.login",
    product_area="Authentication", resource_type="session",
    description="Sign a person in.",
    supported_intents=("log me in",),
    risk_class=_GRAVE, confirmation_policy=_ALWAYS,
    authorization_scope=_PUBLIC, authentication_required=False,
    implementation_status=_DISABLED,
    evidence=("bot.py login routes",),
    known_limitations=("Deliberately unreachable from the agent. Credential entry must "
                       "never pass through a language model's argument set.",),
)
_mapped(
    "auth.password.reset",
    product_area="Authentication", resource_type="credential",
    description="Begin or complete a password reset.",
    supported_intents=("reset my password",),
    risk_class=_GRAVE, confirmation_policy=_ALWAYS,
    authorization_scope=_SELF, owner_field="user_id",
    implementation_status=_DISABLED,
    evidence=("bot.py password reset routes",),
    known_limitations=("Account-recovery surface; an injected instruction that reached it "
                       "would be a takeover primitive.",),
)
_mapped(
    "auth.session.revoke_all",
    product_area="Authentication", resource_type="session",
    description="Sign out every device.",
    supported_intents=("sign me out everywhere",),
    risk_class=_GRAVE, confirmation_policy=_ALWAYS,
    native_screen="AccountDevices",
    authorization_scope=_SELF, owner_field="user_id",
    implementation_status=_DISABLED,
    evidence=("services/pulse_settings_routes.py device sessions",),
    known_limitations=("Would terminate the caller's own session mid-conversation, "
                       "leaving the receipt unreadable.",),
)

# ===========================================================================
# 4. User profiles
# ===========================================================================

_mapped(
    "profile.self.read",
    product_area="User profiles", resource_type="profile",
    description="Read the signed-in account's own profile.",
    supported_intents=("show my profile", "what is my bio"),
    native_screen="Profile",
    backend_route="GET /api/pulse/profile",
    domain_service="", domain_operation="",
    authorization_scope=_SELF, owner_field="user_id",
    output_schema=(("user_id", "int"), ("username", "str"), ("bio", "str")),
    implementation_status=_NO_SERVICE,
    evidence=("services/pulse_feed_engine.py — no profile payload operation is defined",),
    known_limitations=("Profile reads are assembled inside request handlers. There is no "
                       "profile_payload or equivalent in services/, so nothing callable "
                       "returns a profile for a given user_id.",),
)
_mapped(
    "profile.other.read",
    product_area="User profiles", resource_type="profile",
    description="Read another account's public profile.",
    supported_intents=("show me @handle", "who is this person"),
    native_screen="ProfileDetail",
    backend_route="GET /api/pulse/profile/<profile_key>",
    domain_service="", domain_operation="",
    authorization_scope=_PUBLIC, target_field="profile_key",
    output_schema=(("user_id", "int"), ("username", "str"), ("is_private", "bool")),
    result_card_type=CardType.PROFILE_RESULT,
    implementation_status=_NO_SERVICE,
    evidence=("mobile-native/src/api/profileTarget.ts",
              "mobile-native/src/navigation/linking.ts:47 profileNavigationParams"),
    known_limitations=("Privacy gating on a private account is applied by the payload "
                       "builder, not by the route; an agent surfacing this must not "
                       "assume the caller may see every field returned.",),
)
_mapped(
    "profile.self.update",
    product_area="User profiles", resource_type="profile",
    description="Change the account's own display name, bio or links.",
    supported_intents=("change my bio", "update my display name"),
    risk_class=_WRITE, confirmation_policy=_CONTEXTUAL,
    native_screen="ProfileEdit",
    backend_route="POST /api/pulse/profile/edit",
    domain_service="", domain_operation="",
    authorization_scope=_SELF, owner_field="user_id", target_field="user_id",
    implementation_status=_NO_SERVICE,
    evidence=("services/pulse_settings_routes.py profile edit handler",),
    known_limitations=(
        "The update is written inside the request handler; there is no callable "
        "operation taking (user_id, changes). A capability would have to reach "
        "the database directly from the runtime, which is not permitted.",
    ),
)

# ===========================================================================
# 5. Feed posts
# ===========================================================================

_live(
    "feed.posts.list",
    product_area="Feed posts", resource_type="post",
    native_screen="Home",
    backend_route="GET /api/pulse/feed",
    domain_service="services.feed_intelligence_service", domain_operation="list_posts",
    authorization_scope=_SELF, owner_field="user_id",
    output_schema=(("post_id", "int"), ("author_id", "int"), ("body", "str")),
    feature_flag="UNDX_AGENT_READS_ENABLED",
    evidence=("services/feed_intelligence_service.py:list_posts",
              "tests/undx_agent/test_feed_intelligence_pack.py"),
)
_live(
    "feed.posts.get",
    product_area="Feed posts", resource_type="post",
    native_screen="PostDetail",
    backend_route="GET /api/pulse/post/<post_id>",
    domain_service="services.feed_intelligence_service", domain_operation="get_post",
    authorization_scope=_SELF, owner_field="user_id",
    output_schema=(("post_id", "int"), ("author_id", "int"), ("body", "str")),
    feature_flag="UNDX_AGENT_READS_ENABLED",
    evidence=("services/feed_intelligence_service.py:get_post",
              "tests/undx_agent/test_feed_intelligence_pack.py",
              "mobile-native/src/navigation/linking.ts PostDetail"),
)
_live(
    "feed.post.performance.summary",
    product_area="Feed posts", resource_type="post_performance",
    native_screen="PostDetail", backend_route="UNDX governed tool",
    domain_service="services.feed_intelligence_service", domain_operation="post_performance_summary",
    authorization_scope=_SELF, owner_field="user_id",
    output_schema=(("post_id", "int"), ("views", "int"), ("reactions", "int"),
                   ("comments", "int"), ("shares", "int"), ("saves", "int")),
    feature_flag="UNDX_AGENT_READS_ENABLED",
    evidence=("services/feed_intelligence_service.py:post_performance_summary",
              "tests/undx_agent/test_feed_intelligence_pack.py"),
)
_live(
    "feed.comments.summary",
    product_area="Comments", resource_type="comment_summary",
    native_screen="PostDetail", backend_route="UNDX governed tool",
    domain_service="services.feed_intelligence_service", domain_operation="summarize_post_comments",
    authorization_scope=_SELF, owner_field="user_id",
    output_schema=(("post_id", "int"), ("comment_count", "int"),
                   ("participant_count", "int"), ("summary", "str")),
    feature_flag="UNDX_AGENT_READS_ENABLED",
    evidence=("services/feed_intelligence_service.py:summarize_post_comments",
              "tests/undx_agent/test_feed_intelligence_pack.py"),
)
_mapped(
    "feed.posts.create",
    product_area="Feed posts", resource_type="post",
    description="Publish a post to the feed.",
    supported_intents=("post this", "share this to my feed"),
    risk_class=_GRAVE, confirmation_policy=_ALWAYS,
    native_screen="DashboardComposeAlias",
    backend_route="POST /api/pulse/post",
    domain_service="services.pulse_feed_engine", domain_operation="create_post",
    authorization_scope=_SELF, owner_field="user_id", target_field="post_id",
    implementation_status=_PARTIAL,
    evidence=("services/pulse_feed_engine.py:839 create_post",),
    known_limitations=(
        "Publishing is visible to other people the moment it lands, so it is "
        "consequential regardless of whether a delete exists. Composition from a "
        "language model also needs the draft-confirmation card, not a plain "
        "action confirmation.",
    ),
)
_live(
    "feed.posts.delete",
    product_area="Feed posts", resource_type="post",
    native_screen="PostDetail",
    backend_route="DELETE /api/pulse/posts/<post_id>",
    domain_service="services.pulse_feed_engine", domain_operation="delete_owned_post",
    authorization_scope=_SELF, owner_field="user_id",
    output_schema=(("post_id", "int"), ("deleted", "bool"), ("changed", "bool")),
    feature_flag="UNDX_AGENT_WRITES_ENABLED",
    evidence=("services/pulse_feed_engine.py:delete_owned_post",
              "services/undx_verification.py:feed_post_deleted",
              "tests/undx_agent/test_feed_intelligence_pack.py"),
    known_limitations=(
        "Execution remains behind the global UNDX write kill switch and requires "
        "a one-time confirmation bound to the authenticated owner's post id.",
    ),
)

# ===========================================================================
# 6. Reels
# ===========================================================================

_mapped(
    "reels.list",
    product_area="Reels", resource_type="reel",
    description="Read the reels surface.",
    supported_intents=("show me reels",),
    native_screen="Reels",
    backend_route="GET /api/pulse/reels",
    domain_service="", domain_operation="",
    authorization_scope=_PUBLIC,
    output_schema=(("reel_id", "int"), ("author_id", "int")),
    result_card_type=CardType.CONTENT_RESULT,
    implementation_status=_NO_SERVICE,
    evidence=("services/ contains no reels module; reel_ranking_engine.py ranks, it does not serve",),
    known_limitations=("Reels are served from request handlers. No callable operation "
                       "returns reels for a viewer.",),
)
_live(
    "reels.get",
    product_area="Reels", resource_type="reel",
    native_screen="ReelDetail",
    backend_route="GET /api/pulse/reels/feed",
    domain_service="services.content_graph_intelligence_service", domain_operation="get_reel",
    authorization_scope=_SELF, owner_field="user_id",
    output_schema=(("reel_id", "int"), ("creator_id", "int")),
    feature_flag="UNDX_AGENT_READS_ENABLED",
    evidence=("services/content_graph_intelligence_service.py:get_reel",),
)
_mapped(
    "reels.publish",
    product_area="Reels", resource_type="reel",
    description="Publish a reel.",
    supported_intents=("post this reel",),
    risk_class=_GRAVE, confirmation_policy=_ALWAYS,
    native_screen="CameraStudio",
    domain_service="", domain_operation="",
    authorization_scope=_SELF, owner_field="user_id", target_field="reel_id",
    implementation_status=_NONE,
    evidence=("mobile-native/src/navigation/linking.ts CameraStudio",),
    known_limitations=("Requires a capture pipeline the agent has no access to. Media "
                       "the agent did not see must not be published on its say-so.",),
)

# ===========================================================================
# 7. Statuses
# ===========================================================================

_mapped(
    "statuses.list",
    product_area="Statuses", resource_type="status",
    description="Read the viewer's own status ring.",
    supported_intents=("show my status ring", "what statuses are up"),
    native_screen="Status",
    backend_route="GET /api/pulse/status/rail",
    domain_service="", domain_operation="",
    authorization_scope=_SELF, owner_field="user_id",
    result_card_type=CardType.CONTENT_RESULT,
    implementation_status=_PARTIAL,
    evidence=(
        "bot.py:36808 /api/pulse/status/rail",
        "bot.py:36283 /pulse/status renders a page",
    ),
    known_limitations=(
        "The only JSON status endpoint is the rail, which returns the viewer's "
        "own status rail rather than a general listing, so it cannot answer 'show "
        "me X's statuses'. The record previously named GET /api/pulse/status, "
        "which is a rendered page.",
    ),
)
_mapped(
    "statuses.get",
    product_area="Statuses", resource_type="status",
    description="Read one status.",
    supported_intents=("open that status",),
    native_screen="StatusDetail",
    domain_service="", domain_operation="",
    authorization_scope=_MEMBER, target_field="status_id",
    implementation_status=_NO_SERVICE,
    evidence=(
        "bot.py:33324 /pulse/status/<status_id> renders a page",
    ),
    known_limitations=(
        "No JSON read of a single status exists; that route renders HTML. Status visibility is "
        "audience-scoped, so any read built later must not assume the caller is in the audience "
        "just because the id resolves.",
    ),
)
_mapped(
    "statuses.create",
    product_area="Statuses", resource_type="status",
    description="Post a status.",
    supported_intents=("set my status",),
    risk_class=_GRAVE, confirmation_policy=_ALWAYS,
    native_screen="Status",
    authorization_scope=_SELF, owner_field="user_id", target_field="status_id",
    implementation_status=_NO_SERVICE,
    evidence=("bot.py status creation handler",),
    known_limitations=("Written inside the request handler; no callable operation exists.",),
)

for _capability, _area, _resource, _screen, _operation, _output in (
    ("reels.search", "Reels", "reel", "Reels", "list_reels", (("reel_id", "int"),)),
    ("reels.performance.summary", "Reels", "reel", "ReelDetail", "reel_performance", (("reel_id", "int"), ("reactions", "int"))),
    ("reels.comments.summary", "Reels", "comment", "ReelDetail", "reel_comment_summary", (("reel_id", "int"), ("summary", "str"))),
    ("reels.save", "Reels", "reel", "ReelDetail", "set_reel_saved", (("reel_id", "int"), ("saved", "bool"))),
    ("reels.unsave", "Reels", "reel", "ReelDetail", "set_reel_saved", (("reel_id", "int"), ("saved", "bool"))),
    ("reels.like", "Reels", "reel", "ReelDetail", "set_reel_liked", (("reel_id", "int"), ("liked", "bool"))),
    ("reels.unlike", "Reels", "reel", "ReelDetail", "set_reel_liked", (("reel_id", "int"), ("liked", "bool"))),
    ("status.list", "Statuses", "status", "Status", "list_statuses", (("status_id", "int"),)),
    ("status.get", "Statuses", "status", "StatusDetail", "get_status", (("status_id", "int"),)),
    ("status.viewer.summary", "Statuses", "status_view", "StatusDetail", "status_viewer_summary", (("status_id", "int"), ("viewer_count", "int"))),
    ("status.reaction.summary", "Statuses", "status_reaction", "StatusDetail", "status_reaction_summary", (("status_id", "int"), ("reaction_counts", "dict"))),
    ("profile.get", "User profiles", "profile", "Profile", "get_profile", (("user_id", "int"),)),
    ("profile.activity.summary", "User profiles", "profile", "Profile", "profile_activity_summary", (("user_id", "int"), ("posts", "int"))),
    ("profile.relationship.summary", "User profiles", "relationship", "Profile", "profile_relationship_summary", (("user_id", "int"), ("followers", "int"))),
    ("profile.preferences.update", "User profiles", "profile_preference", "Settings", "update_profile_preferences", (("user_id", "int"), ("preferred_language", "str"))),
):
    _live(
        _capability, product_area=_area, resource_type=_resource, native_screen=_screen,
        backend_route="canonical in-process service",
        domain_service="services.content_graph_intelligence_service", domain_operation=_operation,
        authorization_scope=_SELF, owner_field="user_id", output_schema=_output,
        feature_flag="UNDX_AGENT_READS_ENABLED",
        evidence=(f"services/content_graph_intelligence_service.py:{_operation}",),
        known_limitations=(
            ("Undo is not offered because the prior language value is not stored in the "
             "operation arguments; the user can explicitly select another supported language."),
        ) if _capability == "profile.preferences.update" else (),
    )

# ===========================================================================
# 8. Comments
# ===========================================================================

_live(
    "comments.list",
    product_area="Comments", resource_type="comment",
    native_screen="PostDetail",
    backend_route="GET /api/pulse/post/<post_id>/comments",
    domain_service="services.feed_intelligence_service", domain_operation="list_post_comments",
    authorization_scope=_SELF, owner_field="user_id",
    output_schema=(("comment_id", "int"), ("author_id", "int"), ("body", "str")),
    feature_flag="UNDX_AGENT_READS_ENABLED",
    evidence=("services/feed_intelligence_service.py:list_post_comments",
              "tests/undx_agent/test_feed_intelligence_pack.py"),
)
_mapped(
    "comments.create",
    product_area="Comments", resource_type="comment",
    description="Leave a comment.",
    supported_intents=("comment on this", "reply to that post"),
    risk_class=_GRAVE, confirmation_policy=_ALWAYS,
    native_screen="PostDetail",
    backend_route="POST /api/pulse/post/<post_id>/comments",
    domain_service="services.pulse_feed_engine", domain_operation="add_comment",
    authorization_scope=_SELF, owner_field="user_id", target_field="comment_id",
    implementation_status=_PARTIAL,
    evidence=("services/pulse_feed_engine.py:1429 add_comment",),
    known_limitations=(
        "Publishes text authored by a language model under the user's name, in "
        "public, attached to someone else's content. Needs draft confirmation "
        "showing the exact body, not a summary of it.",
    ),
)
_mapped(
    "comments.delete",
    product_area="Comments", resource_type="comment",
    description="Delete one of the account's own comments.",
    supported_intents=("delete my comment",),
    risk_class=_GRAVE, confirmation_policy=_ALWAYS,
    native_screen="PostDetail",
    domain_service="", domain_operation="",
    authorization_scope=_SELF, owner_field="user_id", target_field="comment_id",
    implementation_status=_NO_SERVICE,
    evidence=("services/pulse_feed_engine.py — no comment deletion operation is defined",),
    known_limitations=("No delete_comment exists in services/. Not reversible once written.",),
)

# ===========================================================================
# 9. Reactions
# ===========================================================================

_live(
    "feed.posts.like",
    product_area="Reactions", resource_type="reaction",
    native_screen="PostDetail",
    backend_route="POST /api/pulse/post/<post_id>/react",
    domain_service="services.feed_intelligence_service", domain_operation="set_post_like",
    authorization_scope=_SELF, owner_field="user_id",
    output_schema=(("post_id", "int"), ("liked", "bool"), ("changed", "bool")),
    feature_flag="UNDX_AGENT_WRITES_ENABLED",
    evidence=("services/feed_intelligence_service.py:set_post_like",
              "services/feed_intelligence_service.py:get_post_like",
              "tests/undx_agent/test_feed_reaction_write_pack.py"),
)
_live(
    "feed.posts.unlike",
    product_area="Reactions", resource_type="reaction",
    native_screen="PostDetail",
    backend_route="POST /api/pulse/post/<post_id>/react",
    domain_service="services.feed_intelligence_service", domain_operation="set_post_like",
    authorization_scope=_SELF, owner_field="user_id",
    output_schema=(("post_id", "int"), ("liked", "bool"), ("changed", "bool")),
    feature_flag="UNDX_AGENT_WRITES_ENABLED",
    evidence=("services/feed_intelligence_service.py:set_post_like",
              "services/feed_intelligence_service.py:get_post_like",
              "tests/undx_agent/test_feed_reaction_write_pack.py"),
)

# ===========================================================================
# 10. Saved content
# ===========================================================================
#
# Saved content is the closest thing to a ready capability pack outside crypto
# alerts: authorization is genuinely owner-scoped everywhere. It is blocked on
# two specific things, and naming them precisely is what makes Stage 7 a bounded
# piece of work rather than an exploration.

_SAVED_TOGGLE = (
    "The save endpoints default to a toggle: `if want_saved is None: "
    "want_saved = not currently_saved`. Called twice with the same arguments — "
    "which is exactly what a retry after a timeout does — the second call "
    "unsaves what the first saved. A capability must always send an explicit "
    "desired state, and the domain operation must refuse a None."
)

_live(
    "saved.items.list",
    product_area="Saved content", resource_type="saved_item",
    native_screen="Saved",
    backend_route="GET /api/pulse/saved",
    domain_service="services.saved_content_service",
    domain_operation="list_saved_items",
    authorization_scope=_SELF, owner_field="user_id",
    output_schema=(("item_id", "int"), ("content_type", "str"), ("saved_at", "str")),
    feature_flag="UNDX_AGENT_READS_ENABLED",
    evidence=("services/saved_content_service.py:list_saved_items",
              "tests/undx_agent/test_saved_content_pack.py"),
)
_live(
    "saved.post.set",
    product_area="Saved content", resource_type="saved_item",
    native_screen="PostDetail",
    backend_route="POST /api/pulse/posts/<post_id>/save",
    domain_service="services.saved_content_service", domain_operation="set_post_saved",
    authorization_scope=_SELF, owner_field="user_id",
    output_schema=(("post_id", "int"), ("saved", "bool"), ("changed", "bool")),
    feature_flag="UNDX_AGENT_WRITES_ENABLED",
    evidence=("services/saved_content_service.py:set_post_saved",
              "services/saved_content_service.py:get_post_saved",
              "tests/undx_agent/test_saved_post_write_pack.py"),
)
_mapped(
    "saved.reel.set",
    product_area="Saved content", resource_type="saved_item",
    description="Save or unsave a reel.",
    supported_intents=("save this reel",),
    risk_class=_WRITE, confirmation_policy=_NEVER,
    native_screen="ReelDetail",
    backend_route="POST /api/pulse/reels/<reel_id>/save",
    authorization_scope=_SELF, owner_field="user_id", target_field="reel_id",
    undo_capability_id="saved.reel.set",
    implementation_status=_NO_SERVICE,
    evidence=("bot.py:78411 reel save handler",),
    known_limitations=(_SAVED_TOGGLE,),
    toggle_semantics=True,
)
_mapped(
    "saved.listing.set",
    product_area="Saved content", resource_type="saved_item",
    description="Save or unsave a marketplace listing.",
    supported_intents=("save this listing",),
    risk_class=_WRITE, confirmation_policy=_NEVER,
    native_screen="MarketplaceDetail",
    backend_route="POST /api/pulse/marketplace/<listing_id>/save",
    authorization_scope=_SELF, owner_field="user_id", target_field="listing_id",
    undo_capability_id="saved.listing.set",
    implementation_status=_NO_SERVICE,
    evidence=("bot.py:83106 marketplace save handler",),
    known_limitations=(_SAVED_TOGGLE,),
    toggle_semantics=True,
)

# ===========================================================================
# 11. Social relationships
# ===========================================================================
#
# This is the area most people would assume is easy — follow, unfollow, block —
# and it is the area with the most defects. Every finding below is a reason a
# capability written today would misbehave in a way the user could not see.

_live(
    "social.follow",
    product_area="Social relationships", resource_type="follow_edge",
    native_screen="ProfileDetail",
    backend_route="POST /api/pulse/follow",
    domain_service="services.social_relationship_service", domain_operation="set_following",
    authorization_scope=_OTHER, owner_field="user_id",
    output_schema=(("target_user_id", "int"), ("following", "bool"), ("changed", "bool")),
    feature_flag="UNDX_AGENT_WRITES_ENABLED",
    evidence=("services/social_relationship_service.py:set_following",
              "services/social_relationship_service.py:is_following",
              "tests/undx_agent/test_social_relationship_write_pack.py"),
)
_live(
    "social.unfollow",
    product_area="Social relationships", resource_type="follow_edge",
    native_screen="ProfileDetail",
    backend_route="POST /api/pulse/follow",
    domain_service="services.social_relationship_service", domain_operation="set_following",
    authorization_scope=_OTHER, owner_field="user_id",
    output_schema=(("target_user_id", "int"), ("following", "bool"), ("changed", "bool")),
    feature_flag="UNDX_AGENT_WRITES_ENABLED",
    evidence=("services/social_relationship_service.py:set_following",
              "services/social_relationship_service.py:is_following",
              "tests/undx_agent/test_social_relationship_write_pack.py"),
)
_live(
    "social.followers.list",
    product_area="Social relationships", resource_type="follow_edge",
    native_screen="ProfileDetail",
    backend_route="GET /api/pulse/profile/<profile_key>/followers",
    domain_service="services.social_relationship_service", domain_operation="list_relationships",
    authorization_scope=_SELF, owner_field="user_id",
    output_schema=(("user_id", "int"), ("username", "str")),
    feature_flag="UNDX_AGENT_READS_ENABLED",
    evidence=("services/social_relationship_service.py:list_relationships",
              "tests/undx_agent/test_social_relationship_pack.py"),
)
_mapped(
    "social.block.set",
    product_area="Social relationships", resource_type="block_edge",
    description="Block or unblock an account.",
    supported_intents=("block @handle", "unblock @handle"),
    risk_class=_GRAVE, confirmation_policy=_ALWAYS,
    native_screen="SafetyHub",
    backend_route="POST /api/pulse/settings/block",
    domain_service="services.pulse_settings_routes", domain_operation="",
    authorization_scope=_OTHER, owner_field="user_id", target_field="target_user_id",
    result_card_type=CardType.RELATIONSHIP_CHANGE_RECEIPT,
    implementation_status=_NO_SERVICE,
    evidence=("services/pulse_settings_routes.py:814 block handler",),
    known_limitations=(
        "Request-bound: the handler reads flask.request directly, so there is no "
        "operation taking (user_id, target_user_id, blocked). Blocking also "
        "severs follow edges and conversation access, which makes it "
        "consequential rather than reversible even though an unblock exists.",
    ),
)
_mapped(
    "social.block.read",
    product_area="Social relationships", resource_type="block_edge",
    description="Report whether an account is blocked.",
    supported_intents=("have I blocked @handle",),
    native_screen="SafetyHub",
    domain_service="services.pulse_settings_routes", domain_operation="is_blocked",
    authorization_scope=_OTHER, owner_field="user_id", target_field="target_user_id",
    implementation_status=_PARTIAL,
    evidence=("services/pulse_settings_routes.py:880 is_blocked",),
    known_limitations=(
        "is_blocked is symmetric: it returns true when either party blocked the "
        "other. Used as a verifier for social.block.set it would report success "
        "for a block that never landed, provided the other person had blocked "
        "the caller. A directed read is required.",
    ),
    read_back_missing=True,
)
_mapped(
    "social.mute.set",
    product_area="Social relationships", resource_type="mute_edge",
    description="Mute or unmute an account.",
    supported_intents=("mute @handle",),
    risk_class=_WRITE, confirmation_policy=_CONTEXTUAL,
    native_screen="SafetyHub",
    backend_route="POST /api/pulse/settings/mute",
    domain_service="services.pulse_settings_routes", domain_operation="",
    authorization_scope=_OTHER, owner_field="user_id", target_field="target_user_id",
    result_card_type=CardType.RELATIONSHIP_CHANGE_RECEIPT,
    implementation_status=_NO_SERVICE,
    evidence=("services/pulse_settings_routes.py:814 settings mute handler",),
    known_limitations=("Request-bound, same as block.",),
)
_mapped(
    "social.friend.accept",
    product_area="Social relationships", resource_type="friend_request",
    description="Accept a pending friend request.",
    supported_intents=("accept that friend request",),
    risk_class=_WRITE, confirmation_policy=_CONTEXTUAL,
    native_screen="ActivityInbox",
    backend_route="POST /api/pulse/friends/accept",
    authorization_scope=_OTHER, owner_field="user_id", target_field="request_id",
    result_card_type=CardType.RELATIONSHIP_CHANGE_RECEIPT,
    implementation_status=_NO_SERVICE,
    evidence=("bot.py:79780 friend accept handler",),
    known_limitations=("Guards on `AND status = 'pending'`, which is correct, but the "
                       "update is inline in the handler.",),
)
_mapped(
    "social.friend.decline",
    product_area="Social relationships", resource_type="friend_request",
    description="Decline a pending friend request.",
    supported_intents=("decline that friend request",),
    risk_class=_WRITE, confirmation_policy=_CONTEXTUAL,
    native_screen="ActivityInbox",
    backend_route="POST /api/pulse/friends/decline",
    authorization_scope=_UNSCOPED, owner_field="user_id", target_field="request_id",
    result_card_type=CardType.RELATIONSHIP_CHANGE_RECEIPT,
    implementation_status=_PARTIAL,
    evidence=("bot.py:79817 friend decline handler", "bot.py:79780 accept, for contrast"),
    known_limitations=(
        "Decline omits the `AND status = 'pending'` guard that accept has, so it "
        "will transition a request that is already accepted or already declined. "
        "An agent retrying a decline can therefore undo an acceptance.",
    ),
)
_mapped(
    "social.unfriend",
    product_area="Social relationships", resource_type="friend_edge",
    description="Remove a friend.",
    supported_intents=("unfriend @handle",),
    risk_class=_GRAVE, confirmation_policy=_ALWAYS,
    native_screen="ProfileDetail",
    authorization_scope=_OTHER, owner_field="user_id", target_field="target_user_id",
    implementation_status=_NONE,
    evidence=("bot.py — no unfriend route and no friend-edge deletion exist",),
    known_limitations=("Nothing removes a friend edge anywhere in the product, so there "
                       "is no undo and nothing to build a capability on.",),
)
_mapped(
    "social.close_friends.set",
    product_area="Social relationships", resource_type="close_friend_edge",
    description="Manage the close-friends list.",
    supported_intents=("add @handle to close friends",),
    risk_class=_WRITE, confirmation_policy=_CONTEXTUAL,
    native_screen="ProfileDetail",
    authorization_scope=_OTHER, owner_field="user_id", target_field="target_user_id",
    implementation_status=_NONE,
    evidence=("mobile-native i18n strings only; no route and no table writer",),
    known_limitations=("Exists as translated UI copy. Treating the presence of a string "
                       "as evidence of a feature is exactly the inference this map "
                       "is meant to prevent.",),
)

# ===========================================================================
# 12. Conversations
# ===========================================================================

_CONV_ORACLE = (
    "_conversation_access loads the conversation by global id before checking "
    "membership, so a caller learns whether an id exists whether or not they are "
    "in it. Any capability built on it inherits the oracle."
)

_live(
    "conversations.list",
    product_area="Conversations", resource_type="conversation",
    native_screen="Messenger",
    backend_route="GET /api/pulse/messages",
    domain_service="services.messenger_intelligence_service", domain_operation="list_my_conversations",
    authorization_scope=_SELF, owner_field="user_id",
    output_schema=(("conversation_id", "int"), ("title", "str"), ("unread", "int")),
    feature_flag="UNDX_AGENT_READS_ENABLED",
    evidence=("services/messenger_intelligence_service.py:list_my_conversations",
              "tests/undx_agent/test_messenger_read_pack.py"),
)
_live(
    "conversations.summarize",
    product_area="Conversations", resource_type="conversation_summary",
    native_screen="Chat",
    backend_route="UNDX governed tool",
    domain_service="services.messenger_intelligence_service",
    domain_operation="summarize_conversation",
    authorization_scope=_MEMBER, owner_field="user_id",
    output_schema=(("conversation_id", "int"), ("message_count", "int"), ("summary", "str")),
    feature_flag="UNDX_AGENT_READS_ENABLED",
    evidence=("services/messenger_intelligence_service.py:summarize_conversation",
              "tests/undx_agent/test_messenger_read_pack.py"),
)
_mapped(
    "conversations.get",
    product_area="Conversations", resource_type="conversation",
    description="Open one conversation.",
    supported_intents=("open my chat with @handle",),
    native_screen="Chat",
    backend_route="GET /api/pulse/messages/<conversation_id>",
    domain_service="pulse_communications_v2.service", domain_operation="_conversation_access",
    authorization_scope=_ORACLE, owner_field="user_id", target_field="conversation_id",
    result_card_type=CardType.CONVERSATION_RESULT,
    implementation_status=_PARTIAL,
    evidence=("pulse_communications_v2/service.py:865-890 _conversation_access",),
    known_limitations=(_CONV_ORACLE,),
)
_mapped(
    "conversations.mute",
    product_area="Conversations", resource_type="conversation",
    description="Mute a conversation for a chosen period.",
    supported_intents=("mute this chat", "mute that conversation for an hour"),
    risk_class=_WRITE, confirmation_policy=_CONTEXTUAL,
    native_screen="Chat",
    backend_route="POST /api/pulse/messages/<conversation_id>/control-center",
    domain_service="pulse_communications_v2.service",
    domain_operation="update_conversation_control_center",
    authorization_scope=_MEMBER, owner_field="user_id", target_field="conversation_id",
    input_schema=(("conversation_id", "int"), ("choice", "enum")),
    output_schema=(("conversation_id", "int"), ("muted_until", "str")),
    result_card_type=CardType.SETTING_CHANGE_RECEIPT,
    implementation_status=_UNVERIFIED,
    evidence=("pulse_communications_v2/service.py:3109 update_conversation_control_center",
              "pulse_communications_v2/service.py:693-711 _mute_until_for_choice"),
    known_limitations=(
        "The one genuinely wireable write in messaging: it sets a desired mute "
        "expiry from a named choice rather than toggling, and the expiry is "
        "readable back. Still needs a directed verifier and membership scoping "
        "that does not route through _conversation_access.",
    ),
)
_mapped(
    "conversations.archive",
    product_area="Conversations", resource_type="conversation",
    description="Archive a conversation.",
    supported_intents=("archive this chat",),
    risk_class=_GRAVE, confirmation_policy=_ALWAYS,
    native_screen="Messenger",
    domain_service="pulse_communications_v2.service", domain_operation="archive_conversation",
    authorization_scope=_MEMBER, owner_field="user_id", target_field="conversation_id",
    implementation_status=_PARTIAL,
    evidence=("pulse_communications_v2/service.py:2836 archive_conversation",
              "pulse_communications_v2/service.py:878 access check"),
    known_limitations=(
        "Archiving is a one-way lockout: no unarchive operation exists, and the "
        "archived conversation drops out of the list the agent can see, so the "
        "agent cannot even find it again to report on it.",
    ),
)
_mapped(
    "conversations.mark_read",
    product_area="Conversations", resource_type="conversation",
    description="Mark a conversation read.",
    supported_intents=("mark this chat as read",),
    risk_class=_WRITE, confirmation_policy=_NEVER,
    native_screen="Chat",
    domain_service="pulse_communications_v2.service", domain_operation="list_messages",
    authorization_scope=_MEMBER, owner_field="user_id", target_field="conversation_id",
    implementation_status=_PARTIAL,
    evidence=("pulse_communications_v2/service.py:2439 list_messages, which marks read",),
    known_limitations=(
        "Read state is mutated as a side effect of listing messages. An agent "
        "that merely reads a conversation to answer a question silently marks it "
        "read, which the user did not ask for and cannot undo.",
    ),
)

# ===========================================================================
# 13. Messages
# ===========================================================================

_live(
    "messages.list",
    product_area="Messages", resource_type="message",
    native_screen="Chat",
    backend_route="GET /api/pulse/messages/<conversation_id>/messages",
    domain_service="services.messenger_intelligence_service",
    domain_operation="list_conversation_messages",
    authorization_scope=_MEMBER, owner_field="user_id",
    output_schema=(("message_id", "int"), ("sender_user_id", "int"), ("body", "str")),
    feature_flag="UNDX_AGENT_READS_ENABLED",
    evidence=("services/messenger_intelligence_service.py:list_conversation_messages",
              "tests/undx_agent/test_messenger_read_pack.py"),
)
_live(
    "messages.search",
    product_area="Messages", resource_type="message",
    native_screen="Chat", backend_route="UNDX governed tool",
    domain_service="services.messenger_intelligence_service", domain_operation="search_messages",
    authorization_scope=_MEMBER, owner_field="user_id",
    output_schema=(("message_id", "int"), ("conversation_id", "int"), ("body", "str")),
    feature_flag="UNDX_AGENT_READS_ENABLED",
    evidence=("services/messenger_intelligence_service.py:search_messages",
              "tests/undx_agent/test_messenger_read_pack.py"),
)
_live(
    "messages.suggest",
    product_area="Messages", resource_type="reply_suggestion",
    native_screen="Chat", backend_route="UNDX governed tool",
    domain_service="services.messenger_intelligence_service", domain_operation="suggested_responses",
    authorization_scope=_MEMBER, owner_field="user_id",
    output_schema=(("suggestion_id", "int"), ("conversation_id", "int"), ("body", "str")),
    feature_flag="UNDX_AGENT_READS_ENABLED",
    evidence=("services/messenger_intelligence_service.py:suggested_responses",
              "tests/undx_agent/test_messenger_read_pack.py"),
)
_live(
    "messages.draft",
    product_area="Messages", resource_type="message_draft",
    native_screen="Chat", backend_route="UNDX governed tool",
    domain_service="services.messenger_intelligence_service", domain_operation="prepare_reply_draft",
    authorization_scope=_MEMBER, owner_field="user_id",
    output_schema=(("draft_id", "str"), ("conversation_id", "int"), ("body", "str"), ("send_enabled", "bool")),
    feature_flag="UNDX_AGENT_READS_ENABLED",
    evidence=("services/messenger_intelligence_service.py:prepare_reply_draft",
              "tests/undx_agent/test_messenger_read_pack.py"),
    known_limitations=("Draft creation does not send and cannot be promoted to send without a separately certified confirmation capability.",),
)
_mapped(
    "messages.send",
    product_area="Messages", resource_type="message",
    description="Send a message to a conversation.",
    supported_intents=("send them a message", "reply saying I'll be late"),
    risk_class=_GRAVE, confirmation_policy=_ALWAYS,
    native_screen="Chat",
    backend_route="POST /api/pulse/messages/<conversation_id>",
    domain_service="pulse_communications_v2.service", domain_operation="send_message",
    authorization_scope=_ORACLE, owner_field="user_id", target_field="conversation_id",
    input_schema=(("conversation_id", "int"), ("body", "str")),
    result_card_type=CardType.MESSAGE_DRAFT_CONFIRMATION,
    implementation_status=_PARTIAL,
    evidence=("pulse_communications_v2/service.py:1205 send_message",
              "pulse_communications_v2/service.py:1226 join_public=True"),
    known_limitations=(
        "send_message passes join_public=True, so sending to a public room the "
        "caller is not in silently joins them to it — a membership change the "
        "user never asked for, produced by what looks like a send.",
        "No get_message(user_id, message_id) exists, so there is no way to read "
        "back the message that was just sent. A send cannot currently be verified, "
        "and an unverifiable irreversible write is the worst class of agent action.",
    ),
)
_mapped(
    "messages.delete",
    product_area="Messages", resource_type="message",
    description="Delete a sent message.",
    supported_intents=("delete that message", "unsend it"),
    risk_class=_GRAVE, confirmation_policy=_ALWAYS,
    native_screen="Chat",
    domain_service="pulse_communications_v2.service", domain_operation="delete_message",
    authorization_scope=_ORACLE, owner_field="user_id", target_field="message_id",
    implementation_status=_PARTIAL,
    evidence=("pulse_communications_v2/service.py:3768 delete_message",),
    known_limitations=("Not reversible, and unverifiable for the same reason as send: no "
                       "caller-scoped single-message read exists.",),
)

# ===========================================================================
# 14. Voice messages
# ===========================================================================

_mapped(
    "voice_messages.send",
    product_area="Voice messages", resource_type="voice_message",
    description="Send a recorded voice message.",
    supported_intents=("send a voice note",),
    risk_class=_GRAVE, confirmation_policy=_ALWAYS,
    native_screen="Chat",
    domain_service="pulse_communications_v2.service", domain_operation="send_message",
    authorization_scope=_ORACLE, owner_field="user_id", target_field="conversation_id",
    implementation_status=_NONE,
    evidence=("mobile-native CameraStudio / audio capture surfaces",),
    known_limitations=("Requires device audio capture. The agent has no recording it could "
                       "attach, and must not send media it has not seen.",),
)
_mapped(
    "voice_messages.transcribe",
    product_area="Voice messages", resource_type="voice_message",
    description="Read the transcript of a received voice message.",
    supported_intents=("what did that voice note say",),
    native_screen="Chat",
    authorization_scope=_MEMBER, owner_field="user_id", target_field="message_id",
    implementation_status=_NONE,
    evidence=("no transcription operation found in pulse_communications_v2",),
)

# ===========================================================================
# 15. Audio calls  /  16. Video calls
# ===========================================================================
#
# Calls are the clearest example of a capability class that should stay out of
# reach for a reason other than "not built yet". Placing a call rings a physical
# device belonging to another person. There is no confirmation card that makes
# that safe to trigger from a sentence a model interpreted.

_mapped(
    "calls.audio.place",
    product_area="Audio calls", resource_type="call",
    description="Start an audio call.",
    supported_intents=("call them",),
    risk_class=_GRAVE, confirmation_policy=_ALWAYS,
    native_screen="Call",
    backend_route="POST /api/pulse/calls",
    domain_service="", domain_operation="",
    authorization_scope=_MEMBER, owner_field="user_id", target_field="conversation_id",
    implementation_status=_DISABLED,
    evidence=("mobile-native/src/navigation/linking.ts Call",),
    known_limitations=("Rings another person's device in real time. Held out of agent "
                       "reach deliberately, not for lack of a route.",),
)
_mapped(
    "calls.video.place",
    product_area="Video calls", resource_type="call",
    description="Start a video call.",
    supported_intents=("video call them",),
    risk_class=_GRAVE, confirmation_policy=_ALWAYS,
    native_screen="Call",
    domain_service="", domain_operation="",
    authorization_scope=_MEMBER, owner_field="user_id", target_field="conversation_id",
    implementation_status=_DISABLED,
    evidence=("mobile-native/src/navigation/linking.ts Call",),
    known_limitations=("As above, and additionally activates the camera.",),
)
_mapped(
    "calls.history.list",
    product_area="Audio calls", resource_type="call",
    description="List recent calls.",
    supported_intents=("who called me",),
    native_screen="Call",
    domain_service="", domain_operation="",
    authorization_scope=_SELF, owner_field="user_id",
    implementation_status=_NO_SERVICE,
    evidence=("pulse_communications_v2/service.py defines no call operation",),
    known_limitations=("Call signalling is not in the communications service module; no "
                       "callable history operation was found.",),
)

# ===========================================================================
# 17. Live sessions
# ===========================================================================

_mapped(
    "live.sessions.list",
    product_area="Live sessions", resource_type="live_session",
    description="List live sessions.",
    supported_intents=("who is live",),
    native_screen="Live",
    authorization_scope=_PUBLIC,
    output_schema=(("live_id", "int"), ("host_id", "int"), ("title", "str")),
    implementation_status=_NO_SERVICE,
    evidence=(
        "bot.py:42698 /pulse/live renders a page",
    ),
    known_limitations=(
        "No JSON listing of live sessions exists. The route behind the Live screen renders a "
        "page, so this is a navigation target only until a read is built.",
    ),
)
_mapped(
    "live.sessions.start",
    product_area="Live sessions", resource_type="live_session",
    description="Go live.",
    supported_intents=("start a live",),
    risk_class=_GRAVE, confirmation_policy=_ALWAYS,
    native_screen="LiveDetail",
    authorization_scope=_SELF, owner_field="user_id", target_field="live_id",
    implementation_status=_DISABLED,
    evidence=("mobile-native/src/navigation/linking.ts LiveScheduleGateway",),
    known_limitations=("Opens a broadcast from the device's camera to an audience.",),
)
_mapped(
    "live.schedule.create",
    product_area="Live sessions", resource_type="live_session",
    description="Schedule a live event.",
    supported_intents=("schedule a live for Friday",),
    risk_class=_WRITE, confirmation_policy=_ALWAYS,
    native_screen="LiveScheduleGateway",
    authorization_scope=_SELF, owner_field="user_id", target_field="live_id",
    implementation_status=_NO_SERVICE,
    evidence=("mobile-native/src/navigation/linking.ts LiveEventCreateGateway",),
    known_limitations=("Announces to followers on creation, so it is publicly visible even "
                       "though the session has not started.",),
)

# ===========================================================================
# 18. Search
# ===========================================================================

_mapped(
    "search.query",
    product_area="Search", resource_type="search_result",
    description="Search people, posts and listings.",
    supported_intents=("search for", "find the post about"),
    native_screen="Search",
    backend_route="GET /api/pulse/search",
    domain_service="services.pulse_search_engine", domain_operation="search",
    authorization_scope=_PUBLIC, target_field="query",
    input_schema=(("query", "str"),),
    output_schema=(("kind", "str"), ("id", "int"), ("title", "str")),
    result_card_type=CardType.SEARCH_RESULTS,
    implementation_status=_UNVERIFIED,
    evidence=("services/pulse_search_engine.py:26 search(query, items, limit)",
              "mobile-native/src/navigation/linking.ts Search"),
    known_limitations=(
        "search(query, items, limit) takes no viewer, so it cannot apply "
        "per-account visibility. Results are public-surface, and a search "
        "capability must never be used to resolve a target for a write without a "
        "second, owner-scoped check on the resolved id.",
    ),
)

# ===========================================================================
# 19. Music
# ===========================================================================

_mapped(
    "music.tracks.search",
    product_area="Music", resource_type="track",
    description="Find a track.",
    supported_intents=("find that song",),
    native_screen="Music",
    backend_route="GET /api/pulse/music/search",
    authorization_scope=_PUBLIC, target_field="query",
    result_card_type=CardType.SEARCH_RESULTS,
    implementation_status=_UNVERIFIED,
    evidence=("mobile-native/src/navigation/linking.ts Music",
              "mobile-native/src/core/attachedMusicAudioPolicy.ts"),
)
_mapped(
    "music.playback.control",
    product_area="Music", resource_type="playback",
    description="Play or pause audio.",
    supported_intents=("play that track",),
    risk_class=_WRITE, confirmation_policy=_NEVER,
    native_screen="Music",
    authorization_scope=_SELF, owner_field="user_id", target_field="track_id",
    implementation_status=_NONE,
    evidence=("mobile-native/src/core/attachedMusicAudioPolicy.ts — playback is client state",),
    known_limitations=("Playback lives entirely on the device. The server has no state to "
                       "write and no state to read back, so this can only ever be a "
                       "navigation deep link, not a verified capability.",),
)

# ===========================================================================
# 20. Privacy settings
# ===========================================================================

_mapped(
    "privacy.settings.read",
    product_area="Privacy settings", resource_type="privacy_setting",
    description="Read the account's privacy settings.",
    supported_intents=("is my account private",),
    native_screen="AccountCenter",
    native_route="/pulse/settings/:section",
    backend_route="GET /api/pulse/settings/privacy",
    domain_service="services.pulse_settings_routes", domain_operation="",
    authorization_scope=_SELF, owner_field="user_id",
    output_schema=(("key", "str"), ("value", "bool")),
    implementation_status=_NO_SERVICE,
    evidence=("services/pulse_settings_routes.py privacy handlers",),
    known_limitations=("Request-bound; reads flask.request rather than taking a user_id.",),
)
_mapped(
    "privacy.account_visibility.set",
    product_area="Privacy settings", resource_type="privacy_setting",
    description="Make the account public or private.",
    supported_intents=("make my account private",),
    risk_class=_GRAVE, confirmation_policy=_ALWAYS,
    native_screen="AccountCenter",
    native_route="/pulse/settings/:section",
    domain_service="services.pulse_settings_routes", domain_operation="",
    authorization_scope=_SELF, owner_field="user_id", target_field="setting_key",
    result_card_type=CardType.SETTING_CHANGE_RECEIPT,
    implementation_status=_NO_SERVICE,
    evidence=("services/pulse_settings_routes.py privacy handlers",),
    known_limitations=("Flipping to public exposes previously restricted content "
                       "immediately; the exposure is not undone by flipping back.",),
)

# ===========================================================================
# 21. Security settings
# ===========================================================================

_mapped(
    "security.devices.list",
    product_area="Security settings", resource_type="device_session",
    description="List signed-in devices.",
    supported_intents=("what devices am I signed in on",),
    native_screen="AccountDevices",
    backend_route="GET /api/pulse/settings/devices",
    domain_service="services.pulse_settings_routes", domain_operation="",
    authorization_scope=_SELF, owner_field="user_id",
    output_schema=(("device_id", "str"), ("last_seen", "str")),
    implementation_status=_NO_SERVICE,
    evidence=("services/pulse_settings_routes.py device listing",
              "mobile-native/src/navigation/linking.ts AccountDevices"),
    known_limitations=("Request-bound like the rest of pulse_settings_routes: the handler "
                       "reads flask.request rather than taking a user_id.",),
)
_mapped(
    "security.two_factor.set",
    product_area="Security settings", resource_type="security_setting",
    description="Turn two-factor authentication on or off.",
    supported_intents=("turn on 2FA",),
    risk_class=_GRAVE, confirmation_policy=_ALWAYS,
    native_screen="AccountCenter",
    native_route="/pulse/settings/:section",
    authorization_scope=_SELF, owner_field="user_id", target_field="setting_key",
    implementation_status=_DISABLED,
    evidence=("services/pulse_settings_routes.py security handlers",),
    known_limitations=("Disabling 2FA on an injected instruction is a takeover primitive. "
                       "Enrolment also requires a secret the agent must never handle.",),
)

# ===========================================================================
# 22. Account activity
# ===========================================================================

_mapped(
    "activity.inbox.list",
    product_area="Account activity", resource_type="activity_event",
    description="Read the activity inbox.",
    supported_intents=("what happened on my account",),
    native_screen="ActivityInbox",
    authorization_scope=_SELF, owner_field="user_id",
    input_schema=(("category", "enum"),),
    output_schema=(("event_id", "int"), ("category", "str"), ("occurred_at", "str")),
    implementation_status=_NO_SERVICE,
    evidence=(
        "bot.py — no route matching /pulse/activity exists under any spelling",
    ),
    known_limitations=(
        "Nothing serves the ActivityInbox screen's data. The record previously "
        "named GET /api/pulse/activity, inferred from the screen name. The "
        "activity category vocabulary is also distinct from the notification "
        "category vocabulary and must not be merged with it.",
    ),
)
_mapped(
    "activity.account_health.read",
    product_area="Account activity", resource_type="account_health",
    description="Read account standing and health.",
    supported_intents=("is my account in good standing",),
    native_screen="AccountHealth",
    authorization_scope=_SELF, owner_field="user_id",
    implementation_status=_NO_SERVICE,
    evidence=(
        "bot.py:9667 /dashboard/account/health renders a web dashboard page",
    ),
    known_limitations=(
        "Account health exists only as a rendered page on the web dashboard. Nothing returns it "
        "as data, so the native AccountHealth screen is a navigation target and not a readable "
        "capability.",
    ),
)

# ===========================================================================
# 23. Reporting
# ===========================================================================

_mapped(
    "reporting.submit",
    product_area="Reporting", resource_type="report",
    description="Report a post, account or message.",
    supported_intents=("report that post",),
    risk_class=_GRAVE, confirmation_policy=_ALWAYS,
    native_screen="SafetyHub",
    backend_route="POST /api/pulse/report",
    domain_service="services.pulse_feed_engine", domain_operation="report",
    authorization_scope=_SELF, owner_field="user_id", target_field="target_id",
    implementation_status=_PARTIAL,
    evidence=("services/pulse_feed_engine.py:1845 report",),
    known_limitations=(
        "Append-only, returns no report id, and cannot be withdrawn. With no id "
        "there is nothing to read back, so the write is unverifiable; and with no "
        "withdrawal there is no undo. A false report filed against a real person "
        "on a misread instruction cannot be taken back.",
    ),
)
_mapped(
    "reporting.status.read",
    product_area="Reporting", resource_type="report",
    description="Check the status of a report.",
    supported_intents=("what happened to my report",),
    native_screen="SafetyHub",
    authorization_scope=_SELF, owner_field="user_id", target_field="report_id",
    implementation_status=_NONE,
    evidence=("services/pulse_feed_engine.py:1845 report — returns no identifier",),
    known_limitations=("Nothing to read: report() hands back no id, so a status lookup has "
                       "no key to look up.",),
)

# ===========================================================================
# 24. Moderation
# ===========================================================================

_mapped(
    "moderation.queue.list",
    product_area="Moderation", resource_type="moderation_item",
    description="Read the moderation queue.",
    supported_intents=("show the moderation queue",),
    native_screen="SafetyHub",
    authorization_scope=_PRIVILEGED, owner_field="moderator_id",
    implementation_status=_DISABLED,
    evidence=("services/pulse_feed_engine.py moderation surfaces",),
    known_limitations=("Privileged role. An agent acting for a moderator would act on "
                       "other people's accounts, which no confirmation card covers.",),
)
_mapped(
    "moderation.action.apply",
    product_area="Moderation", resource_type="moderation_item",
    description="Take a moderation action on content or an account.",
    supported_intents=("take down that post",),
    risk_class=_GRAVE, confirmation_policy=_ALWAYS,
    native_screen="SafetyHub",
    authorization_scope=_PRIVILEGED, owner_field="moderator_id", target_field="target_id",
    implementation_status=_DISABLED,
    evidence=("services/pulse_feed_engine.py moderation surfaces",),
    known_limitations=(
        "Held out of agent reach on purpose. A moderation action is taken by a "
        "privileged account against content the account does not own, so the "
        "owner-scoped authorization the runtime enforces does not apply to it, "
        "and there is nothing for the policy engine to check the caller against. "
        "It is also irreversible in the direction that matters: a takedown the "
        "agent got wrong is not undone by a restore, because the content was "
        "already off the platform for the interval.",
    ),
)

# ===========================================================================
# 25. Advertising
# ===========================================================================

_mapped(
    "ads.campaigns.list",
    product_area="Advertising", resource_type="ad_campaign",
    description="List the advertiser's campaigns.",
    supported_intents=("show my campaigns",),
    native_screen="MerchantDashboard",
    domain_service="services.pulse_advertiser_portal", domain_operation="list_campaigns",
    authorization_scope=_SELF, owner_field="advertiser_id",
    output_schema=(("campaign_id", "int"), ("status", "str"), ("budget", "float")),
    implementation_status=_UNVERIFIED,
    evidence=("services/pulse_advertiser_portal.py:328 list_campaigns(conn, user_id)",),
    known_limitations=("Takes an injected connection rather than opening its own, so it is "
                       "not callable from the runtime as it stands.",),
)
_mapped(
    "ads.campaigns.pause",
    product_area="Advertising", resource_type="ad_campaign",
    description="Pause a campaign.",
    supported_intents=("pause my campaign",),
    risk_class=_WRITE, confirmation_policy=_ALWAYS,
    native_screen="MerchantDashboard",
    domain_service="services.pulse_advertiser_portal", domain_operation="campaign_action",
    authorization_scope=_SELF, owner_field="advertiser_id", target_field="campaign_id",
    implementation_status=_PARTIAL,
    evidence=("services/pulse_advertiser_portal.py:561 campaign_action(conn, user_id, campaign_id, action)",),
    known_limitations=(
        "Deliberately declares no undo. Resume is the obvious inverse and commits "
        "money, so pairing them would let one approval reserve budget repeatedly "
        "through a retry loop. Pausing stays a one-way action until a "
        "resume-without-reservation exists.",
        "campaign_action takes an injected connection, so it is not callable from "
        "the runtime as it stands.",
    ),
)
_mapped(
    "ads.campaigns.resume",
    product_area="Advertising", resource_type="ad_campaign",
    description="Resume a paused campaign.",
    supported_intents=("resume my campaign",),
    risk_class=_GRAVE, confirmation_policy=_ALWAYS,
    native_screen="MerchantDashboard",
    domain_service="services.pulse_advertiser_portal", domain_operation="campaign_action",
    authorization_scope=_SELF, owner_field="advertiser_id", target_field="campaign_id",
    implementation_status=_PARTIAL,
    evidence=("services/pulse_advertiser_portal.py:608-609 reserve_campaign_budget",),
    known_limitations=(
        "Resume calls reserve_campaign_budget, so it commits money. It reads like "
        "the harmless inverse of pause and is not: pausing and resuming in a "
        "retry loop reserves budget repeatedly. It must never be registered as "
        "the undo of pause.",
    ),
)

# ===========================================================================
# 26. Marketplace
# ===========================================================================

_mapped(
    "marketplace.listings.search",
    product_area="Marketplace", resource_type="listing",
    description="Browse or search marketplace listings.",
    supported_intents=("find a listing for",),
    native_screen="Marketplace",
    backend_route="GET /api/pulse/marketplace/search",
    authorization_scope=_PUBLIC, target_field="query",
    output_schema=(("listing_id", "int"), ("title", "str"), ("price", "float")),
    result_card_type=CardType.SEARCH_RESULTS,
    implementation_status=_UNVERIFIED,
    evidence=("mobile-native/src/navigation/linking.ts Marketplace, MarketplaceDetail",),
)
_mapped(
    "marketplace.orders.list",
    product_area="Marketplace", resource_type="order",
    description="List the account's orders.",
    supported_intents=("where is my order",),
    native_screen="BuyerOrders",
    backend_route="GET /api/pulse/orders",
    authorization_scope=_SELF, owner_field="user_id",
    output_schema=(("order_id", "int"), ("status", "str")),
    implementation_status=_UNVERIFIED,
    evidence=("mobile-native/src/navigation/linking.ts BuyerOrders, BuyerOrderDetail",),
)
_mapped(
    "marketplace.purchase",
    product_area="Marketplace", resource_type="order",
    description="Buy a listing.",
    supported_intents=("buy that",),
    risk_class=_GRAVE, confirmation_policy=_ALWAYS,
    native_screen="MarketplaceDetail",
    authorization_scope=_SELF, owner_field="user_id", target_field="listing_id",
    implementation_status=_DISABLED,
    evidence=("mobile-native marketplace checkout surfaces",),
    known_limitations=("Spends the user's money. Held out of agent reach; the agent may "
                       "deep-link the person to the listing and stop there.",),
)
_mapped(
    "marketplace.listing.create",
    product_area="Marketplace", resource_type="listing",
    description="Create a listing for sale.",
    supported_intents=("list this for sale",),
    risk_class=_GRAVE, confirmation_policy=_ALWAYS,
    native_screen="MarketplaceCreateGateway",
    authorization_scope=_SELF, owner_field="user_id", target_field="listing_id",
    implementation_status=_NO_SERVICE,
    evidence=("mobile-native/src/navigation/linking.ts MarketplaceCreateGateway",),
    known_limitations=("Publicly visible commercial offer created under the user's name.",),
)

# ===========================================================================
# 27. Premium
# ===========================================================================

_mapped(
    "premium.status.read",
    product_area="Premium", resource_type="subscription",
    description="Report whether the account has premium.",
    supported_intents=("am I on premium",),
    native_screen="Premium",
    authorization_scope=_SELF, owner_field="user_id",
    output_schema=(("tier", "str"), ("renews_at", "str")),
    implementation_status=_UNVERIFIED,
    evidence=("mobile-native/src/navigation/linking.ts Premium",
              "mobile-native/src/premium.ts"),
)
_mapped(
    "premium.checkout.start",
    product_area="Premium", resource_type="subscription",
    description="Begin a premium subscription.",
    supported_intents=("subscribe to premium",),
    risk_class=_GRAVE, confirmation_policy=_ALWAYS,
    native_screen="Premium",
    authorization_scope=_SELF, owner_field="user_id", target_field="tier",
    implementation_status=_DISABLED,
    evidence=("mobile-native/src/premium.ts:84-85 startPremiumCheckout",),
    known_limitations=(
        "startPremiumCheckout opens a Stripe URL automatically. An agent that "
        "called it would put a payment page in front of the user with no step "
        "the user had approved, which is a purchase flow initiated by a model.",
    ),
)

# ===========================================================================
# 28. Business tools
# ===========================================================================

_mapped(
    "business.creator_studio.read",
    product_area="Business tools", resource_type="creator_metric",
    description="Read creator-studio performance metrics.",
    supported_intents=("how did my posts do",),
    native_screen="CreatorStudio",
    authorization_scope=_SELF, owner_field="user_id",
    implementation_status=_NO_SERVICE,
    evidence=(
        "bot.py:9396 /dashboard/creator renders a page",
        "bot.py:6667 /api/dashboard/creator/state is the nearest JSON",
    ),
    known_limitations=(
        "The creator surface is a web dashboard. /api/dashboard/creator/state "
        "exists but is shaped for that dashboard rather than for the native "
        "CreatorStudio screen, so naming it as this capability's backing would be "
        "a guess of exactly the kind this map exists to prevent.",
    ),
)
_mapped(
    "business.content_planner.schedule",
    product_area="Business tools", resource_type="scheduled_post",
    description="Schedule a post for later.",
    supported_intents=("schedule this post for tomorrow",),
    risk_class=_GRAVE, confirmation_policy=_ALWAYS,
    native_screen="ContentPlanner",
    authorization_scope=_SELF, owner_field="user_id", target_field="scheduled_post_id",
    implementation_status=_NO_SERVICE,
    evidence=("mobile-native/src/navigation/linking.ts ContentPlanner, PostScheduler",),
    known_limitations=("Publishes without a person present at publication time, so the "
                       "confirmation has to cover a future public post.",),
)
_mapped(
    "business.merchant.apply",
    product_area="Business tools", resource_type="merchant_application",
    description="Apply for a merchant account.",
    supported_intents=("apply to be a seller",),
    risk_class=_GRAVE, confirmation_policy=_ALWAYS,
    native_screen="MerchantApply",
    authorization_scope=_SELF, owner_field="user_id", target_field="application_id",
    implementation_status=_DISABLED,
    evidence=("mobile-native/src/navigation/linking.ts MerchantApply",),
    known_limitations=("Submits identity and business information for review.",),
)

# ===========================================================================
# 29. Native navigation
# ===========================================================================
#
# Navigation is the safe fallback for every capability above that is not
# wireable: UNDX cannot archive the conversation, but it can put the person on
# the screen where they can. These records make that a declared capability with
# a validated route rather than an ad-hoc string the model composes.

_mapped(
    "navigation.deep_link",
    product_area="Native navigation", resource_type="route",
    description="Open a PulseSoc screen.",
    supported_intents=("take me to my saved items", "open settings"),
    native_screen="Home",
    authorization_scope=_SELF, owner_field="user_id", target_field="screen",
    input_schema=(("screen", "enum"),),
    output_schema=(("screen", "str"), ("deep_link", "str")),
    implementation_status=_UNVERIFIED,
    evidence=("mobile-native/src/navigation/linking.ts",
              "mobile-native/src/navigation/nativeRouteActions.ts canonicalNativeRoute"),
    known_limitations=(
        "Route parameters are substituted into a navigation target, so the value "
        "alphabet must stay restricted exactly as CapabilitySpec.deep_link does "
        "it; a free-text parameter would let a model put `..` in a route.",
    ),
)
_mapped(
    "navigation.undx_action_center",
    product_area="Native navigation", resource_type="route",
    description="Open the UNDX action centre.",
    supported_intents=("show what UNDX did",),
    native_screen="UndxActionCenter",
    authorization_scope=_SELF, owner_field="user_id",
    implementation_status=_UNVERIFIED,
    evidence=("mobile-native/src/navigation/linking.ts UndxActionCenter",),
)
_mapped(
    "navigation.settings_entry",
    product_area="Native navigation", resource_type="route",
    description="Open a specific settings destination by id.",
    supported_intents=("open notification settings",),
    native_screen="Settings",
    authorization_scope=_SELF, owner_field="user_id", target_field="settings_id",
    implementation_status=_UNVERIFIED,
    evidence=("mobile-native/src/navigation/linking.ts:25 settingsDeepLink",
              "mobile-native/src/settings/registry.ts findSettingsEntry"),
    known_limitations=("Resolved through the settings registry, so an unknown id lands on "
                       "the settings index rather than failing silently.",),
)

# ===========================================================================
# 30. UNDX memory and tasks
# ===========================================================================

_mapped(
    "undx.audit.list",
    product_area="UNDX memory and tasks", resource_type="audit_entry",
    description="Report what UNDX has done for this account.",
    supported_intents=("what did you do", "show my recent actions"),
    native_screen="UndxActionCenter",
    domain_service="services.undx_policy", domain_operation="PRODUCTION_TOOL_REGISTRY",
    authorization_scope=_SELF, owner_field="user_id",
    output_schema=(("capability_id", "str"), ("outcome", "str"), ("occurred_at", "str")),
    implementation_status=_UNVERIFIED,
    evidence=("services/undx_policy.py", "services/undx_architecture.py prepare_tool_operation",
              "tests/undx_agent/test_audit_durability.py"),
    known_limitations=("The ledger is written by the gateway; a read capability over it "
                       "must not be able to alter it.",),
)
_mapped(
    "undx.capabilities.describe",
    product_area="UNDX memory and tasks", resource_type="capability",
    description="Report which actions UNDX can perform.",
    supported_intents=("what can you do",),
    native_screen="UndxActionCenter",
    domain_service="services.undx_capability_registry", domain_operation="describe_for_model",
    authorization_scope=_SELF, owner_field="user_id",
    implementation_status=_UNVERIFIED,
    evidence=("services/undx_capability_registry.py:554 describe_for_model",),
    known_limitations=("Must describe the registry, not this map: the map lists things "
                       "UNDX cannot do, and reading it as an offer would promise them.",),
)
_mapped(
    "undx.tasks.schedule",
    product_area="UNDX memory and tasks", resource_type="task",
    description="Schedule an UNDX action for later.",
    supported_intents=("remind me to", "do this tomorrow"),
    risk_class=_GRAVE, confirmation_policy=_ALWAYS,
    native_screen="UndxActionCenter",
    authorization_scope=_SELF, owner_field="user_id", target_field="task_id",
    implementation_status=_NONE,
    evidence=("no scheduler exists in services/undx_*",),
    known_limitations=(
        "A deferred action executes with nobody watching, so the confirmation "
        "collected now has to cover a state the runtime cannot see yet. Out of "
        "scope until the verified-write path is proven for immediate actions.",
    ),
)

# ===========================================================================
# 31. Phase 3B personal intelligence
# ===========================================================================

for _capability_id, _area, _resource, _screen, _operation in (
    ("activity.daily_summary", "Activity", "activity_fact", "ActivityInbox", "activity_daily_summary"),
    ("notifications.inbox.list", "Notifications", "notification", "Notifications", "notifications_inbox"),
    ("notifications.explain", "Notifications", "notification", "Notifications", "notification_explain"),
    ("notifications.group_summary", "Notifications", "notification_group", "Notifications", "notification_group_summary"),
    ("search.global", "Search", "search_result", "Search", "search_global"),
    ("search.people", "Search", "profile", "Search", "search_people"),
    ("search.content", "Search", "content", "Search", "search_content"),
    ("search.messages", "Search", "message", "Messenger", "search_messages"),
    ("search.activity", "Search", "activity_fact", "ActivityInbox", "search_activity"),
    ("settings.inspect", "Privacy", "setting", "Settings", "settings_inspect"),
    ("settings.explain", "Privacy", "setting", "Settings", "settings_explain"),
    ("settings.recommend", "Privacy", "setting_recommendation", "Settings", "settings_recommend"),
    ("security.sessions.list", "Security", "session", "AccountDevices", "security_sessions"),
    ("security.activity.summary", "Security", "security_event", "AccountHealth", "security_activity_summary"),
    ("security.device.list", "Security", "device", "AccountDevices", "security_devices"),
    ("marketplace.search", "Marketplace", "listing", "Marketplace", "marketplace_search"),
    ("marketplace.listing.summary", "Marketplace", "listing", "MarketplaceDetail", "marketplace_listing_summary"),
    ("marketplace.order.status", "Marketplace", "order", "BuyerOrderDetail", "marketplace_order_status"),
    ("premium.status", "Premium", "premium_status", "Premium", "premium_status"),
    ("premium.entitlements", "Premium", "entitlement", "Premium", "premium_entitlements"),
    ("ads.performance.summary", "Ads", "campaign_metric", "IntelligenceCenter", "ads_performance_summary"),
    ("live.search", "Live", "live_session", "Live", "live_search"),
    ("live.summary", "Live", "live_session", "LiveDetail", "live_summary"),
    ("live.performance", "Live", "live_metric", "LiveDetail", "live_performance"),
    ("learning.search", "Learning", "course", "Courses", "learning_search"),
    ("learning.progress", "Learning", "course_progress", "Courses", "learning_progress"),
    ("memory.activity.inspect", "UNDX memory and tasks", "sourced_fact", "UndxActionCenter", "memory_activity_inspect"),
    ("groups.list", "Groups", "group", "Groups", "groups_list"),
    ("groups.search", "Groups", "group", "Groups", "groups_search"),
    ("events.upcoming", "Events", "event", "Events", "events_upcoming"),
    ("music.search", "Music", "music_track", "Music", "music_search"),
    ("account.health.summary", "Account health", "account_health_fact", "AccountHealth", "account_health_summary"),
    ("verification.status", "Verification", "verification_request", "VerificationCenter", "verification_status"),
    ("support.tickets.list", "Support", "support_ticket", "TrustSafetySupport", "support_tickets_list"),
    ("creator.analytics.summary", "Creator", "creator_metric", "CreatorStudio", "creator_analytics_summary"),
    # AccountCenter, not Settings: the registry routes both of these to
    # ``/pulse/settings/<section>``, which is AccountCenter's catch-all. Naming
    # ``Settings`` would have sent the user one level up from the screen that
    # actually shows the setting.
    ("localization.preferences", "Localization", "localization_preference", "AccountCenter", "localization_preferences"),
    ("presence.privacy.status", "Presence", "presence_preference", "AccountCenter", "presence_privacy_status"),
):
    _live(
        _capability_id,
        product_area=_area,
        resource_type=_resource,
        native_screen=_screen,
        backend_route="POST /api/pulse-ai/message",
        domain_service="services.undx_personal_intelligence_service",
        domain_operation=_operation,
        authorization_scope=_SELF,
        owner_field="user_id",
        output_schema=(
            ("source", "str"), ("timestamp", "str"), ("authorization_scope", "str"),
            ("native_route", "str"), ("confidence", "float"),
        ),
        feature_flag="UNDX_AGENT_READS_ENABLED",
        evidence=(
            f"services/undx_personal_intelligence_service.py:{_operation}",
            "tests/undx_agent/test_personal_intelligence_pack.py",
        ),
        known_limitations=("Read-only. Empty source tables produce empty results, never inferred facts.",),
    )

_live(
    "translation.content.translate",
    product_area="Localization", resource_type="translated_content",
    native_screen="UndxActionCenter",
    backend_route="POST /api/pulse/translations",
    domain_service="services.content_translation", domain_operation="translate_content",
    authorization_scope=_SELF, owner_field="user_id",
    output_schema=(("translated_text", "str"), ("source_language", "str"),
                   ("target_language", "str"), ("content_version", "str")),
    feature_flag="UNDX_AGENT_READS_ENABLED",
    evidence=("services/content_translation.py:translate_content",
              "services/translation_providers.py:GoogleAdvancedProvider",
              "tests/test_content_translation.py"),
    known_limitations=("Google credentials and TRANSLATION_ENABLED are required; drafts remain unsent.",),
)


# ---------------------------------------------------------------------------
# Indexes
# ---------------------------------------------------------------------------

BY_ID: dict[str, ProductCapabilityRecord] = {}
for _record in RECORDS:
    if _record.capability_id in BY_ID:
        # Duplicate ids are not a cosmetic problem. The agent view is keyed by
        # capability_id, so a duplicate silently drops one of the two records —
        # and the one that survives may be the permissive one.
        raise ValueError(f"duplicate knowledge-map capability id {_record.capability_id}")
    BY_ID[_record.capability_id] = _record

PRODUCT_AREAS: tuple[str, ...] = tuple(sorted({r.product_area for r in RECORDS}))


def get(capability_id: str) -> ProductCapabilityRecord | None:
    return BY_ID.get(capability_id)


# ---------------------------------------------------------------------------
# The three views
# ---------------------------------------------------------------------------
#
# Each is a projection of RECORDS. None of them holds state, and none may be
# edited independently — that is the entire reason they are functions and not
# module-level lists.


def agent_capability_view() -> list[dict[str, Any]]:
    """What UNDX may reason about, and what it must not offer.

    This deliberately includes the records UNDX *cannot* execute, marked
    ``executable=False``. Hiding them would leave the planner unable to
    distinguish "PulseSoc cannot do this" from "I have not been told about it",
    and the second produces confident invention. The gateway still refuses
    anything not in the registry, so listing them costs nothing and telling the
    user "PulseSoc has no unfollow" is better than a guess.
    """
    return [
        {
            "capability_id": r.capability_id,
            "product_area": r.product_area,
            "description": r.description,
            "supported_intents": list(r.supported_intents),
            "risk_class": r.risk_class,
            "confirmation_policy": r.confirmation_policy,
            "input_schema": [{"name": n, "type": t} for n, t in r.input_schema],
            "output_schema": [{"name": n, "type": t} for n, t in r.output_schema],
            "verifier": r.verifier,
            "undo_capability_id": r.undo_capability_id,
            "result_card_type": r.result_card_type,
            "implementation_status": r.implementation_status,
            "executable": r.is_executable,
            "feature_flag": r.feature_flag,
        }
        for r in sorted(RECORDS, key=lambda item: item.capability_id)
    ]


def product_knowledge_view() -> dict[str, list[dict[str, Any]]]:
    """What PulseSoc contains, grouped by product area.

    This is the view a person reads when deciding what to build next, so it
    leads with the honest fields: what is missing, and what is wrong with what
    exists.
    """
    view: dict[str, list[dict[str, Any]]] = {area: [] for area in PRODUCT_AREAS}
    for r in sorted(RECORDS, key=lambda item: (item.product_area, item.capability_id)):
        view[r.product_area].append({
            "capability_id": r.capability_id,
            "resource_type": r.resource_type,
            "description": r.description,
            "backend_route": r.backend_route,
            "domain_service": r.domain_service,
            "domain_operation": r.domain_operation,
            "authentication_required": r.authentication_required,
            "authorization_scope": r.authorization_scope,
            "owner_field": r.owner_field,
            "target_field": r.target_field,
            "implementation_status": r.implementation_status,
            "evidence": list(r.evidence),
            "known_limitations": list(r.known_limitations),
        })
    return view


def native_navigation_view() -> list[dict[str, Any]]:
    """Where each capability lives in the app.

    Only records that name a screen appear. A capability with no screen is not
    a navigation destination, and inventing one for it would produce a deep
    link that lands nowhere.
    """
    return [
        {
            "capability_id": r.capability_id,
            "product_area": r.product_area,
            "native_screen": r.native_screen,
            "native_route": r.native_route,
            "deep_link_template": r.deep_link_template,
            "target_field": r.target_field,
            "result_card_type": r.result_card_type,
        }
        for r in sorted(RECORDS, key=lambda item: (item.native_screen, item.capability_id))
        if r.native_screen
    ]


# ---------------------------------------------------------------------------
# Readiness
# ---------------------------------------------------------------------------
#
# The classification is computed, not typed. A record's readiness follows from
# the facts already recorded about it, so a record cannot be marked ready while
# still carrying a note that its service is missing.

def classify_readiness(record: ProductCapabilityRecord) -> str:
    """Decide whether a capability may be wired, and if not, what is missing.

    Order matters. A record can be blocked several ways at once, and the label
    has to name the thing that must be fixed *first*: there is no point writing
    a verifier for an operation whose authorization lets the caller reach rows
    they do not own.

    The mandated precedence is::

        AUTHORIZATION DEFECT -> DOMAIN SERVICE REQUIRED -> TOGGLE HAZARD
        -> VERIFIER REQUIRED -> NATIVE CONTEXT REQUIRED -> UNSUPPORTED
        -> READY TO WIRE

    This function previously tested ``UNSUPPORTED`` first, which inverted the two
    ends of that list. The consequence was not cosmetic: a capability that is both
    unsupported *and* carries an authorization defect classified as the milder of
    the two, so the defect left the matrix entirely. "Unsupported" reads as "we are
    not building this yet" and gets skimmed past; the defect it was hiding is a
    caller reaching rows they do not own, which stays true the day someone decides
    to support the capability after all. Severity has to survive being combined with
    inactivity, so ``UNSUPPORTED`` now sorts second-to-last — a statement about
    scheduling, made only once nothing more serious is true.

    ``TOGGLE HAZARD`` also sat above ``DOMAIN SERVICE REQUIRED`` here, against the
    mandate. Restoring the mandated order is worth naming, because both toggling
    records are also service-missing and therefore now classify as ``DOMAIN SERVICE
    REQUIRED``, leaving ``TOGGLE HAZARD`` empty in the matrix. The hazard does not
    thereby disappear: ``toggle_semantics`` is still on the record, still stated in
    ``known_limitations``, and ``test_a_toggle_is_never_recorded_as_a_desired_state_write``
    still fails if a toggling operation is registered or reaches ``READY TO WIRE``.
    The label names what must be built first, which for these two is the service;
    the toggle is a constraint on how that service must then be written.
    """
    if record.authorization_scope in (AuthorizationScope.EXISTENCE_ORACLE,
                                      AuthorizationScope.UNSCOPED,
                                      AuthorizationScope.PRIVILEGED):
        return ReadinessClass.AUTHORIZATION_DEFECT
    if record.implementation_status == ImplementationStatus.SERVICE_MISSING or (
        record.is_write and not record.domain_operation
    ):
        return ReadinessClass.DOMAIN_SERVICE_REQUIRED
    if record.toggle_semantics:
        return ReadinessClass.TOGGLE_HAZARD
    if record.is_write and not record.verifier:
        return ReadinessClass.VERIFIER_REQUIRED
    if record.read_back_missing:
        return ReadinessClass.VERIFIER_REQUIRED
    if record.requires_native_context:
        return ReadinessClass.NATIVE_CONTEXT_REQUIRED
    if record.implementation_status in (ImplementationStatus.UNSUPPORTED,
                                        ImplementationStatus.INTENTIONALLY_DISABLED):
        return ReadinessClass.UNSUPPORTED
    return ReadinessClass.READY_TO_WIRE


def readiness_matrix(product_areas: tuple[str, ...] | None = None) -> dict[str, list[dict[str, Any]]]:
    """The gate on write-capability implementation.

    Keyed by classification rather than by product area, because the question
    this answers is "what do I have to build first", and the answer groups by
    the missing thing, not by the feature it blocks.
    """
    wanted = set(product_areas or PRODUCT_AREAS)
    matrix: dict[str, list[dict[str, Any]]] = {label: [] for label in sorted(ReadinessClass.ALL)}
    for r in sorted(RECORDS, key=lambda item: item.capability_id):
        if r.product_area not in wanted:
            continue
        matrix[classify_readiness(r)].append({
            "capability_id": r.capability_id,
            "product_area": r.product_area,
            "risk_class": r.risk_class,
            "implementation_status": r.implementation_status,
            "blocking_notes": list(r.known_limitations),
        })
    return matrix


#: The three domains Stages 6 and 7 target. Named here so the matrix a reviewer
#: reads and the matrix a test asserts on are the same call.
STAGE_TARGET_AREAS = ("Social relationships", "Saved content", "Conversations", "Messages")


__all__ = [
    "ImplementationStatus", "ReadinessClass", "AuthorizationScope",
    "ProductCapabilityRecord", "RECORDS", "BY_ID", "PRODUCT_AREAS",
    "NATIVE_ROUTES", "DEEP_LINK_PREFIXES", "get",
    "agent_capability_view", "product_knowledge_view", "native_navigation_view",
    "classify_readiness", "readiness_matrix", "STAGE_TARGET_AREAS",
]


def _cli(argv: list[str]) -> int:
    """Emit a view as JSON, so the map is readable without a Python session.

    Deliberately a projection of the records above rather than a file checked in
    beside them. A committed JSON copy would be a fourth hand-maintained list,
    and the first edit to a record that forgot to regenerate it would leave a
    reviewer reading a map of an older system while every test still passed.
    """
    import argparse
    import json

    views = {
        "agent": agent_capability_view,
        "product": product_knowledge_view,
        "navigation": native_navigation_view,
        "readiness": lambda: readiness_matrix(),
        "readiness-stage-targets": lambda: readiness_matrix(STAGE_TARGET_AREAS),
    }
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("view", choices=sorted(views))
    args = parser.parse_args(argv)
    print(json.dumps(views[args.view](), indent=2, sort_keys=True, default=list))
    return 0


if __name__ == "__main__":  # pragma: no cover - a convenience entry point
    import sys as _sys

    raise SystemExit(_cli(_sys.argv[1:]))
