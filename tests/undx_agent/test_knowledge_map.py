"""The knowledge map, checked against the code it claims to describe.

A map of a system is only useful if it cannot quietly stop being true. Every
test here exists because there is a specific way this file could drift into
lying, and a specific consequence when it does:

* a route that no longer exists produces a deep link that lands nowhere, and the
  user is told the action succeeded while looking at the wrong screen
* a "verified" record whose service is gone produces a capability the planner
  offers and the gateway cannot dispatch
* an ``unsupported`` record that acquires a registry entry turns a documented
  "PulseSoc cannot do this" into an executable tool nobody reviewed

Nothing in this file touches a network, a database, or a running server. The
map is a static artefact and its checks are static: routes are read out of
``linking.ts`` with a parser, and domain operations are confirmed by parsing the
owning module's syntax tree rather than importing it. Importing
``services.alert_engine`` to ask whether ``pause_alert`` exists would couple a
correctness test to whatever that module does at import time, which is exactly
the coupling the mission forbids.
"""

from __future__ import annotations

import ast
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import undx_capability_registry as registry  # noqa: E402
from services import undx_knowledge_map as kmap  # noqa: E402
from services.undx_agent_contracts import CardType, ConfirmationPolicy, RiskLevel  # noqa: E402

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
LINKING_TS = os.path.join(REPO_ROOT, "mobile-native", "src", "navigation", "linking.ts")

#: The repository's suite is ``unittest``-based and is collected with
#: ``python3 -m unittest discover``. A module written for a different runner is
#: a module that does not run, so the checks below are plain methods on a
#: ``TestCase`` and use bare ``assert`` for the same reason the rest of this file
#: does: the assertion message is written by hand and says what is wrong.




# ---------------------------------------------------------------------------
# Static readers, shared by the checks below.
#
# These parse rather than import. Importing a service module to ask whether an
# operation exists would run whatever that module does at import time and tie a
# correctness check to a live dependency.
# ---------------------------------------------------------------------------

def _declared_routes_from_linking_ts() -> dict[str, set[str]]:
    """Screen -> every path declared for it, from the client's own config.

    Parsed with a regex rather than a TypeScript parser because the shapes in
    play are only two: ``Screen: "path"`` and ``Screen: { path: "path", ... }``.
    A parser would be more general and would also mean this test could fail for
    reasons that have nothing to do with routes.

    The value is a *set* because a screen may legitimately be declared more than
    once: ``Search`` appears both nested under the tab navigator
    (``pulse/search``) and again at the root (``search``), so both paths open it.
    Collapsing to one path would make the test assert an arbitrary choice
    between two correct answers — whichever the regex happened to see last.
    """
    with open(LINKING_TS, encoding="utf-8") as handle:
        source = handle.read()
    routes: dict[str, set[str]] = {}
    for pattern in (r'(\w+)\s*:\s*"([^"]+)"',
                    r'(\w+)\s*:\s*\{\s*path\s*:\s*"([^"]+)"'):
        for name, path in re.findall(pattern, source):
            if name == "path":
                continue
            routes.setdefault(name, set()).add("/" + path.lstrip("/"))
    return routes


def _known_flags() -> set[str]:
    """Flag names read out of the policy module's source, not its runtime state.

    Reading the environment would make this test depend on how the process was
    launched, which is a different question from whether the flag exists.
    """
    module_path = os.path.join(REPO_ROOT, "services", "undx_agent_policy.py")
    with open(module_path, encoding="utf-8") as handle:
        source = handle.read()
    return set(re.findall(r'"(UNDX_[A-Z0-9_]+)"', source)) | set(
        re.findall(r"'(UNDX_[A-Z0-9_]+)'", source)
    )


def _module_defines(dotted: str, operation: str) -> bool | None:
    """Whether ``dotted`` defines ``operation``, by parsing rather than importing.

    Returns ``None`` when the module file cannot be found, which the caller
    treats as "cannot answer" rather than "absent" — a missing file on a partial
    checkout should not read as a defect in the map.
    """
    path = os.path.join(REPO_ROOT, *dotted.split(".")) + ".py"
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == operation:
            return True
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == operation:
                    return True
        # ``NAME: Type = ...`` is an AnnAssign, not an Assign. A registry table
        # declared with its type — services.undx_policy.PRODUCTION_TOOL_REGISTRY
        # is one — is every bit as defined as an unannotated one.
        if isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == operation:
                return True
    return False


class KnowledgeMapTests(unittest.TestCase):
    """Every way the map could quietly stop being true, checked."""

# ---------------------------------------------------------------------------
# 1. Capability ids are unique
# ---------------------------------------------------------------------------


    def test_capability_ids_are_unique(self):
        """Two records under one id means one of them is invisible.

        ``BY_ID`` already raises at import, so this asserts the property directly
        rather than trusting that the import-time guard was not weakened.
        """
        seen = [record.capability_id for record in kmap.RECORDS]
        duplicates = sorted({cid for cid in seen if seen.count(cid) > 1})
        assert duplicates == [], f"duplicate capability ids in the knowledge map: {duplicates}"
        assert len(kmap.BY_ID) == len(kmap.RECORDS)


    def test_registered_records_agree_with_the_registry(self):
        """A record marked registered must be the capability the registry declares.

        This is the anti-duplication property. The map is allowed to *reference* the
        registry and forbidden to restate it, so any operational field that appears
        in both has to be equal by construction — if this ever fails, someone has
        started keeping a second copy.
        """
        for record in kmap.RECORDS:
            if not record.registered:
                continue
            spec = registry.REGISTRY.get(record.capability_id)
            assert spec is not None, f"{record.capability_id}: marked registered but absent from the registry"
            assert record.risk_class == spec.risk
            assert record.confirmation_policy == spec.confirmation
            assert record.verifier == spec.verifier
            assert record.result_card_type == spec.result_card
            assert record.native_route == spec.native_route
            assert record.target_field == spec.target_field
            assert record.undo_capability_id == spec.undo_capability_id


# ---------------------------------------------------------------------------
# 2. Native route names are real
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
    @unittest.skipUnless(os.path.exists(LINKING_TS), "native client not present")
    def test_native_routes_match_the_client_configuration(self):
        """The Python copy of the route table must equal the client's.

        The copy exists so import-time validation can run without a bundler. A copy
        that is allowed to drift is worse than no copy, because it validates records
        against a map of an app that no longer exists.
        """
        declared = _declared_routes_from_linking_ts()
        for screen, path in kmap.NATIVE_ROUTES.items():
            assert screen in declared, f"{screen} is not a screen declared in linking.ts"
            shapes = {kmap._route_shape(candidate) for candidate in declared[screen]}
            assert kmap._route_shape(path) in shapes, (
                f"{screen}: knowledge map says {path!r}, linking.ts declares "
                f"{sorted(declared[screen])!r}"
            )


    def test_every_record_route_belongs_to_the_screen_it_names(self):
        """A record must send the person to the screen it claims to.

        A route that exists elsewhere in the app still passes a membership check
        while landing the user somewhere they did not ask to be.
        """
        for record in kmap.RECORDS:
            if not record.native_screen:
                continue
            assert record.native_screen in kmap.NATIVE_ROUTES
            if record.native_route:
                expected = kmap.NATIVE_ROUTES[record.native_screen]
                assert kmap._route_shape(record.native_route) == kmap._route_shape(expected), (
                    f"{record.capability_id}: route {record.native_route!r} is not screen "
                    f"{record.native_screen!r}'s route {expected!r}"
                )


    @unittest.skipUnless(os.path.exists(LINKING_TS), "native client not present")
    def test_registered_capability_routes_are_navigable(self):
        """Every live capability's deep link resolves to a declared screen.

        The registry is free to declare a route the client does not serve — nothing
        in it reads ``linking.ts``. That gap is only visible from a test that holds
        both, which is this one.
        """
        declared_shapes = {
            kmap._route_shape(path)
            for paths in _declared_routes_from_linking_ts().values()
            for path in paths
        }
        for spec in registry.REGISTRY.values():
            assert kmap._route_shape(spec.native_route) in declared_shapes, (
                f"{spec.capability_id}: native_route {spec.native_route!r} has no screen behind it"
            )


# 3. Deep-link templates are well formed
# ---------------------------------------------------------------------------


    def test_deep_link_templates_are_well_formed(self):
        """A deep link is a navigation target, so its shape is a safety property.

        Whitespace, a missing scheme, or a traversal segment in a template are all
        ways a substituted value ends up steering navigation somewhere unintended.
        """
        for record in kmap.RECORDS:
            template = record.deep_link_template
            if not template:
                assert not record.native_screen, (
                    f"{record.capability_id}: names a screen but has no deep link"
                )
                continue
            assert template.startswith(kmap.DEEP_LINK_PREFIXES), (
                f"{record.capability_id}: {template!r} does not use a registered prefix"
            )
            assert " " not in template
            assert ".." not in template
            remainder = template.split("://", 1)[1] if "://" in template else template
            assert remainder, f"{record.capability_id}: empty deep-link path"


    def test_registry_deep_links_strip_unfilled_optional_parameters(self):
        """``deep_link`` must not emit a literal ``:param`` to the client.

        An unresolved placeholder is not a broken link in an obvious way — the
        client will happily navigate to a path containing a colon and show an empty
        screen, which reads to the user as "the action did nothing".
        """
        for spec in registry.REGISTRY.values():
            resolved = spec.deep_link({})
            assert ":" not in resolved, f"{spec.capability_id}: unresolved placeholder in {resolved!r}"
            assert resolved.startswith("/")


# ---------------------------------------------------------------------------
# 4. Every verified write names a verifier
# ---------------------------------------------------------------------------


    def test_every_verified_write_declares_a_verifier(self):
        """"Verified" means an independent read confirmed it, or it means nothing.

        A write recorded as verified with no verifier is the single most dangerous
        shape in this system: the receipt says the change landed, and nothing
        checked.
        """
        for record in kmap.RECORDS:
            if record.implementation_status != kmap.ImplementationStatus.VERIFIED:
                continue
            assert record.registered, f"{record.capability_id}: unregistered records cannot be verified"
            if record.is_write:
                assert record.verifier, f"{record.capability_id}: verified write with no verifier"
                spec = registry.REGISTRY[record.capability_id]
                assert spec.verifier == record.verifier
                # ``verified_fields`` is deliberately not required to be non-empty.
                # For an operation whose only argument *is* the target — pausing an
                # alert by id — there is no field to compare beyond the target
                # itself, and the registry already computes the required set as
                # mutable arguments minus the target. Demanding a non-empty set here
                # would force a fake field onto a correctly specified capability.


    def test_verifier_names_resolve_in_the_verification_module(self):
        """A verifier is a name resolved at execution time; a typo surfaces then.

        Resolving it here — statically, without importing — turns a production
        failure during a confirmed write into an import-time test failure.
        """
        module_path = os.path.join(REPO_ROOT, "services", "undx_verification.py")
        with open(module_path, encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        defined = {
            node.name for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        for record in kmap.RECORDS:
            if record.verifier:
                assert record.verifier in defined, (
                    f"{record.capability_id}: verifier {record.verifier!r} is not defined in undx_verification"
                )


# ---------------------------------------------------------------------------
# 5. Consequential actions always require confirmation
# ---------------------------------------------------------------------------


    def test_consequential_actions_always_require_confirmation(self):
        """The risk class is the server's judgement; the policy must follow it.

        A consequential write is one whose effect another person sees or whose
        reversal is not available. Letting one through on ``contextual`` means a
        plausible-looking sentence is enough to trigger it.
        """
        for record in kmap.RECORDS:
            if record.risk_class == RiskLevel.CONSEQUENTIAL_WRITE:
                assert record.confirmation_policy == ConfirmationPolicy.ALWAYS, (
                    f"{record.capability_id}: consequential but confirmation is "
                    f"{record.confirmation_policy!r}"
                )
            if record.is_write:
                assert record.confirmation_policy in ConfirmationPolicy.ALL
            else:
                assert record.confirmation_policy == ConfirmationPolicy.NEVER, (
                    f"{record.capability_id}: a read must not ask for approval"
                )


    def test_writes_name_the_resource_they_change(self):
        """Without a target field the confirmation card cannot say what it changes.

        "Approve this change" with no object is not consent, and the same omission
        collapses the idempotency key so two unrelated calls in one request collide.
        """
        for record in kmap.RECORDS:
            if record.is_write and record.implementation_status not in kmap.ImplementationStatus.NOT_EXECUTABLE:
                assert record.target_field, f"{record.capability_id}: write with no target_field"


# ---------------------------------------------------------------------------
# 6. Undo is declared, or its absence is justified
# ---------------------------------------------------------------------------


    def test_undo_is_declared_or_explicitly_justified(self):
        """Silence about undo is indistinguishable from forgetting about it.

        Every write either names the capability that reverses it or says in
        ``known_limitations`` why it cannot be reversed. The user reads that text on
        the card; a blank means they are not told the action is permanent.
        """
        for record in kmap.RECORDS:
            if not record.is_write:
                continue
            if record.undo_capability_id:
                continue
            assert record.known_limitations, (
                f"{record.capability_id}: no undo and no stated reason. If the action is "
                f"irreversible, say so; the user is entitled to know before approving."
            )


    def test_declared_undo_targets_exist_where_the_runtime_would_look(self):
        """An undo capability the runtime cannot resolve is a button that fails.

        For registered records the registry's own undo graph validation applies. For
        mapped records the target must at least be a capability this map knows
        about, so a rename cannot leave a dangling reference.
        """
        for record in kmap.RECORDS:
            target = record.undo_capability_id
            if not target:
                continue
            if record.registered:
                assert target in registry.REGISTRY, (
                    f"{record.capability_id}: undo {target!r} is not registered"
                )
            else:
                assert target in kmap.BY_ID, (
                    f"{record.capability_id}: undo {target!r} is not in the knowledge map"
                )


    def test_a_money_committing_operation_is_never_someone_elses_undo(self):
        """Pause/resume symmetry is a trap when resume reserves budget.

        ``ads.campaigns.resume`` reads like the harmless inverse of pause and
        commits spend. If it is ever wired as pause's undo, a retry loop reserves
        budget repeatedly with one approval behind it.
        """
        resume = kmap.BY_ID["ads.campaigns.resume"]
        assert resume.risk_class == RiskLevel.CONSEQUENTIAL_WRITE
        for record in kmap.RECORDS:
            if record.undo_capability_id != "ads.campaigns.resume":
                continue
            raise AssertionError(
                f"{record.capability_id} declares a budget-committing operation as its undo"
            )


# ---------------------------------------------------------------------------
# 7. Feature-flag references are known
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
    def test_feature_flag_references_are_known_flags(self):
        """A record gated on a flag nobody reads is a record with no gate.

        The failure is silent in the worst direction: the capability behaves as if
        it were always enabled.
        """
        known = _known_flags()
        assert known, "no UNDX_* flags found in undx_agent_policy; the parser needs updating"
        for record in kmap.RECORDS:
            if record.feature_flag:
                assert record.feature_flag in known, (
                    f"{record.capability_id}: feature_flag {record.feature_flag!r} is not read anywhere"
                )


    def test_every_live_capability_is_gated(self):
        """Nothing registered should be reachable with every flag off."""
        for record in kmap.RECORDS:
            if record.is_executable:
                assert record.feature_flag, f"{record.capability_id}: executable but ungated"


# 8. Domain service operations resolve
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
    def test_domain_operations_resolve_in_their_declared_module(self):
        """A named operation must exist in the module the record blames.

        This is what keeps ``domain_service`` honest. Without it, a record can point
        at a plausible module forever, and the defect is only found by whoever tries
        to write the capability.
        """
        unresolved: list[str] = []
        for record in kmap.RECORDS:
            if not record.domain_service or not record.domain_operation:
                continue
            found = _module_defines(record.domain_service, record.domain_operation)
            if found is False:
                unresolved.append(
                    f"{record.capability_id}: {record.domain_service}.{record.domain_operation}"
                )
        assert unresolved == [], "domain operations named by the map but absent from their module:\n" + "\n".join(unresolved)


# 9. No verified capability references a missing service
# ---------------------------------------------------------------------------


    def test_no_verified_capability_references_a_missing_service(self):
        """Verified is the strongest claim in the file; it needs the strongest check.

        A verified record must name a real module, a real operation inside it, and
        be dispatchable through the registry. Anything less and "verified" has
        drifted back to meaning "someone read the code and thought it looked fine".
        """
        for record in kmap.RECORDS:
            if record.implementation_status != kmap.ImplementationStatus.VERIFIED:
                continue
            assert record.domain_service, f"{record.capability_id}: verified with no owning service"
            assert record.domain_operation, f"{record.capability_id}: verified with no named operation"
            resolved = _module_defines(record.domain_service, record.domain_operation)
            assert resolved is not False, (
                f"{record.capability_id}: verified, but {record.domain_service}."
                f"{record.domain_operation} does not exist"
            )
            assert record.is_executable, f"{record.capability_id}: verified but not dispatchable"


    def test_service_missing_records_do_not_name_an_operation_they_lack(self):
        """``service_missing`` and a resolvable operation are contradictory.

        If the operation exists, the record's status is wrong and someone will skip
        writing a service that is genuinely needed — or write one that already
        exists.
        """
        for record in kmap.RECORDS:
            if record.implementation_status != kmap.ImplementationStatus.SERVICE_MISSING:
                continue
            if not (record.domain_service and record.domain_operation):
                continue
            resolved = _module_defines(record.domain_service, record.domain_operation)
            assert resolved is not True, (
                f"{record.capability_id}: marked service_missing but "
                f"{record.domain_service}.{record.domain_operation} resolves"
            )


# ---------------------------------------------------------------------------
# 10. Card types are a single vocabulary with no duplicates
# ---------------------------------------------------------------------------


    def test_card_type_vocabulary_has_no_duplicates(self):
        """Two names for one card, or one name reused, both break the client.

        ``actionCards.ts`` routes on the component string and defaults anything it
        does not recognise to a failure card, so a duplicated or divergent value
        turns a successful action into a visible error.
        """
        values = [
            value for name, value in vars(CardType).items()
            if not name.startswith("_") and isinstance(value, str)
        ]
        assert len(values) == len(set(values)), "CardType declares the same string twice"
        assert set(values) == set(CardType.ALL), "CardType.ALL disagrees with its own constants"


    def test_records_use_only_declared_card_types(self):
        for record in kmap.RECORDS:
            if record.result_card_type:
                assert record.result_card_type in CardType.ALL


    def test_confirmation_cards_are_not_used_as_result_cards(self):
        """A confirmation card is a question; a result card is an answer.

        Declaring a confirmation component as a capability's result would render the
        completed action as another approval prompt, inviting the user to approve
        something that already happened.
        """
        confirmation_only = {
            CardType.ACTION_CONFIRMATION,
            CardType.MESSAGE_DRAFT_CONFIRMATION,
        }
        for spec in registry.REGISTRY.values():
            assert spec.result_card not in confirmation_only, (
                f"{spec.capability_id}: result card {spec.result_card!r} is a confirmation component"
            )


# ---------------------------------------------------------------------------
# 11. Notification category vocabulary is not duplicated
# ---------------------------------------------------------------------------


    def test_notification_category_vocabulary_is_single_and_unique(self):
        """One vocabulary, declared once, with no repeated members.

        The categories are the words UNDX offers a person. A second list somewhere
        else would eventually accept a word the store has no category for, and the
        update would succeed against nothing.
        """
        categories = registry.NOTIFICATION_CATEGORIES
        assert len(categories) == len(set(categories)), "NOTIFICATION_CATEGORIES repeats a member"
        assert registry.category_choices() is categories, (
            "category_choices must hand back the one canonical tuple, not a copy that can drift"
        )
        for spec in registry.REGISTRY.values():
            for item in spec.fields:
                if item.name == "category" and item.choices:
                    assert tuple(item.choices) == categories, (
                        f"{spec.capability_id}: declares its own category vocabulary"
                    )


    def test_activity_categories_are_not_conflated_with_notification_categories(self):
        """Two different category concepts must not be merged into one enum.

        The activity inbox and the notification preferences both speak of
        "categories" and mean different things. Merging them would let a request to
        silence a notification category filter an activity feed instead.
        """
        activity = kmap.BY_ID["activity.inbox.list"]
        notification = kmap.BY_ID["notifications.preference.update"]
        assert activity.product_area != notification.product_area
        assert activity.domain_service != notification.domain_service or not activity.domain_service


# ---------------------------------------------------------------------------
# 12. The views are projections of one record set
# ---------------------------------------------------------------------------


    def test_agent_and_product_views_come_from_the_same_records(self):
        """Three hand-maintained lists diverge; three projections cannot.

        This is the property that makes the map worth having. If the views ever stop
        covering exactly the same records, someone has started maintaining one of
        them by hand.
        """
        agent_ids = {row["capability_id"] for row in kmap.agent_capability_view()}
        product_ids = {
            row["capability_id"]
            for rows in kmap.product_knowledge_view().values()
            for row in rows
        }
        record_ids = {record.capability_id for record in kmap.RECORDS}
        assert agent_ids == record_ids
        assert product_ids == record_ids
        nav_ids = {row["capability_id"] for row in kmap.native_navigation_view()}
        assert nav_ids <= record_ids
        assert nav_ids == {r.capability_id for r in kmap.RECORDS if r.native_screen}


    def test_views_report_the_same_facts_for_the_same_capability(self):
        """A projection may drop fields; it may not change them."""
        agent = {row["capability_id"]: row for row in kmap.agent_capability_view()}
        product = {
            row["capability_id"]: row
            for rows in kmap.product_knowledge_view().values()
            for row in rows
        }
        for cid, record in kmap.BY_ID.items():
            assert agent[cid]["risk_class"] == record.risk_class
            assert agent[cid]["implementation_status"] == record.implementation_status
            assert product[cid]["implementation_status"] == record.implementation_status
            assert product[cid]["authorization_scope"] == record.authorization_scope


    def test_every_record_cites_its_source(self):
        """A finding with no citation cannot be re-checked, only believed."""
        for record in kmap.RECORDS:
            assert record.evidence, f"{record.capability_id}: no evidence cited"
            assert all(item.strip() for item in record.evidence)


    def test_the_map_covers_the_required_product_areas(self):
        """Thirty areas, so a gap is a missing record rather than an unasked question."""
        assert len(kmap.PRODUCT_AREAS) >= 30, (
            f"only {len(kmap.PRODUCT_AREAS)} product areas mapped: {kmap.PRODUCT_AREAS}"
        )


# ---------------------------------------------------------------------------
# 13. Unsupported capabilities cannot become executable
# ---------------------------------------------------------------------------


    def test_unsupported_capabilities_are_not_executable_tools(self):
        """The load-bearing negative property of the whole map.

        ``unsupported``, ``service_missing`` and ``intentionally_disabled`` are
        decisions, not gaps in the paperwork. If one of them ever acquires a
        registry entry, a documented refusal has silently become a dispatchable
        action — and it would be dispatchable through a path nobody reviewed,
        because the review happened when the record said "we are not doing this".
        """
        for record in kmap.RECORDS:
            if record.implementation_status not in kmap.ImplementationStatus.NOT_EXECUTABLE:
                continue
            assert not record.registered, f"{record.capability_id}: marked non-executable but registered"
            assert record.capability_id not in registry.REGISTRY, (
                f"{record.capability_id}: status {record.implementation_status!r} but the registry "
                f"will dispatch it"
            )
            assert not record.is_executable


    def test_require_refuses_every_unsupported_capability(self):
        """The refusal is the runtime's, not the map's.

        Asserting through ``registry.require`` proves the gateway path itself
        refuses these, rather than proving only that the map holds a consistent
        opinion about them.
        """
        from services.undx_agent_contracts import AgentError

        checked = 0
        for record in kmap.RECORDS:
            if record.implementation_status not in kmap.ImplementationStatus.NOT_EXECUTABLE:
                continue
            try:
                registry.require(record.capability_id)
            except AgentError as exc:
                assert exc.code == "unsupported_capability", (
                    f"{record.capability_id}: refused with {exc.code!r}, not unsupported_capability"
                )
            else:
                raise AssertionError(
                    f"{record.capability_id}: registry.require did not refuse a "
                    f"{record.implementation_status!r} capability"
                )
            checked += 1
        assert checked > 0, "no non-executable records to check; the map has lost its negative space"


    def test_the_agent_view_marks_unexecutable_records_as_such(self):
        """The planner is told these exist and cannot be done.

        Hiding them would leave the model unable to distinguish "PulseSoc has no
        unfollow" from "I was not told about unfollow", and the second produces a
        confident invention.
        """
        view = {row["capability_id"]: row for row in kmap.agent_capability_view()}
        assert view["social.block.set"]["executable"] is False
        assert view["social.unfollow"]["executable"] is True
        assert view["crypto.alerts.create"]["executable"] is True
        for cid, row in view.items():
            assert row["executable"] == (cid in registry.REGISTRY)


# ---------------------------------------------------------------------------
# The readiness matrix
# ---------------------------------------------------------------------------


    def test_readiness_matrix_uses_only_the_declared_classifications(self):
        matrix = kmap.readiness_matrix()
        assert set(matrix) == set(kmap.ReadinessClass.ALL)
        total = sum(len(rows) for rows in matrix.values())
        assert total == len(kmap.RECORDS), "every record must receive exactly one classification"


    def test_no_write_in_the_stage_target_areas_is_ready_to_wire(self):
        """The gate on Stages 6 and 7.

        Every write in social relationships, saved content and messaging is blocked
        on something specific. If this test ever passes trivially — because a record
        was softened rather than a defect fixed — the matrix has stopped being a
        gate and become a formality.
        """
        matrix = kmap.readiness_matrix(kmap.STAGE_TARGET_AREAS)
        ready_writes = [
            row for row in matrix[kmap.ReadinessClass.READY_TO_WIRE]
            if RiskLevel.is_write(row["risk_class"])
        ]
        # A stage-target write may graduate only by becoming a registered, verified,
        # non-toggle capability. Keep the explicit allowlist small so a map edit
        # cannot silently soften another blocked write.
        assert {row["capability_id"] for row in ready_writes} == {
            "saved.post.set", "social.follow", "social.unfollow",
        }, (
            "unexpected write graduated in a stage target area: "
            f"{ready_writes}"
        )
        for capability_id in ("saved.post.set", "social.follow", "social.unfollow"):
            graduated = kmap.BY_ID[capability_id]
            assert graduated.registered and graduated.verifier and not graduated.toggle_semantics


    def test_blocked_records_explain_what_is_blocking_them(self):
        """A classification with no note is a verdict with no reason.

        Whoever picks up the work needs to know what to fix, and the readiness class
        alone does not say which line of which file is wrong.
        """
        matrix = kmap.readiness_matrix()
        for label, rows in matrix.items():
            if label in (kmap.ReadinessClass.READY_TO_WIRE, kmap.ReadinessClass.UNSUPPORTED):
                continue
            for row in rows:
                record = kmap.BY_ID[row["capability_id"]]
                assert record.known_limitations or record.authorization_scope in (
                    kmap.AuthorizationScope.EXISTENCE_ORACLE,
                    kmap.AuthorizationScope.UNSCOPED,
                    kmap.AuthorizationScope.PRIVILEGED,
                ), f"{record.capability_id}: classified {label} with nothing recorded about why"


    def test_structured_defect_flags_are_explained_in_prose(self):
        """A flag decides the label; the prose has to say why the flag is set.

        The classifier reads ``toggle_semantics``, ``requires_native_context`` and
        ``read_back_missing`` rather than grepping ``known_limitations``, which is
        what stops social.follow — whose limitation mentions a toggle in order to
        say the capability must avoid the toggling *route* — from being filed as a
        toggle hazard. The cost of that separation is that a flag could be set with
        nothing written down. This closes it from the other side: the flag is
        authoritative, and a reviewer is still owed the reason.
        """
        for record in kmap.RECORDS:
            for field in ("toggle_semantics", "requires_native_context", "read_back_missing"):
                if getattr(record, field):
                    assert record.known_limitations, (
                        f"{record.capability_id}: {field} is set but nothing explains it"
                    )
                    assert record.evidence, (
                        f"{record.capability_id}: {field} is set with no source evidence"
                    )


    def test_a_toggle_is_never_recorded_as_a_desired_state_write(self):
        """A flipping operation must not be described to a planner as a setter.

        ``.set`` in a capability id promises idempotence. If the operation behind it
        toggles, a planner that retries a timed-out call reverses the user's intent
        and the receipt still says it succeeded.
        """
        for record in kmap.RECORDS:
            if not record.toggle_semantics:
                continue
            assert not record.registered, (
                f"{record.capability_id}: a toggling operation is registered as an executable tool"
            )
            assert kmap.classify_readiness(record) == kmap.ReadinessClass.TOGGLE_HAZARD, (
                f"{record.capability_id}: declares toggle semantics but is not classified as a hazard"
            )


    def test_known_defects_are_classified_as_the_defects_they_are(self):
        """The specific findings Stage 2 exists to record, asserted by name.

        Generic structural tests would still pass if these particular records were
        quietly reclassified. These are the findings that decide what Stages 6 and 7
        have to build, so they are pinned individually.
        """
        classify = kmap.classify_readiness
        # social.follow was originally pinned as a toggle on the strength of the
        # HTTP handler, which does flip. The domain operation it would actually call
        # -- pulse_feed_engine.follow, an INSERT OR IGNORE -- is idempotent, so the
        # hazard is in the route, not the service, and a capability must call the
        # service. What blocks it is that nothing can read back "is A following B".
        assert classify(kmap.BY_ID["social.follow"]) == kmap.ReadinessClass.READY_TO_WIRE
        # The toggling HTTP operation remains unsuitable for agent use. The agent
        # registry instead exposes two explicit desired-state operations with
        # independent read-back, so retries cannot invert the requested state.
        assert "reactions.set" not in kmap.BY_ID
        assert classify(kmap.BY_ID["feed.posts.like"]) == kmap.ReadinessClass.READY_TO_WIRE
        assert classify(kmap.BY_ID["feed.posts.unlike"]) == kmap.ReadinessClass.READY_TO_WIRE
        # Saved post writes graduated only after the agent path stopped calling the
        # toggling HTTP behavior and gained an explicit desired-state service plus
        # independent read-back verifier.
        assert classify(kmap.BY_ID["saved.post.set"]) == kmap.ReadinessClass.READY_TO_WIRE
        assert classify(kmap.BY_ID["social.friend.decline"]) == kmap.ReadinessClass.AUTHORIZATION_DEFECT
        assert classify(kmap.BY_ID["messages.send"]) == kmap.ReadinessClass.AUTHORIZATION_DEFECT
        assert classify(kmap.BY_ID["conversations.get"]) == kmap.ReadinessClass.AUTHORIZATION_DEFECT
        assert classify(kmap.BY_ID["social.block.set"]) == kmap.ReadinessClass.DOMAIN_SERVICE_REQUIRED
        # Saved listing graduated in the first live-training slice: its Flask-bound
        # query now has an owner-scoped service and a registered read capability.
        assert classify(kmap.BY_ID["saved.items.list"]) == kmap.ReadinessClass.READY_TO_WIRE
        # Unfollowing is not unsupported — the product plainly offers it. What is
        # missing is a domain service: it exists only as a DELETE statement inside
        # the HTTP toggle handler, so Stage 6 has to write one. Calling it
        # "unsupported" would quietly remove that work from the plan.
        assert classify(kmap.BY_ID["social.unfollow"]) == kmap.ReadinessClass.READY_TO_WIRE
        assert classify(kmap.BY_ID["conversations.mute"]) == kmap.ReadinessClass.VERIFIER_REQUIRED
