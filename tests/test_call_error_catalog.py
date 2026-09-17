"""Every call error code the engine raises must be named in ERROR_CATALOG.

`_err(message, status, code)` routes through `_error_details`, which looks `code`
up in `ERROR_CATALOG` and falls back to a single tuple when it misses:

    ("UNKNOWN_ERROR", "Call could not start", ...)

The fallback is silent. A code with no entry still returns 4xx/5xx with a
plausible-looking body, so nothing fails and nothing logs a warning — the fault
just loses its name on the way out. Thirteen codes were in that state when this
test was written. Nine are written literally at their `_err` call site
(`not_callee`, `invalid_call_type`, `invalid_transition`, `transition_conflict`,
`unsupported_control`, `missing_device_id`, `missing_token`, `invalid_live_role`,
`agora_token_builder_missing`). The other four arrive as a variable — `missing`
and `denied` from `_conversation_access`, `unauthenticated` and `invalid` from
`register_voip_token` — and those are the expensive ones, because
`_conversation_access` guards nearly every call route, so the commonest failure
on the surface answered UNKNOWN_ERROR while `missing_conversation` and
`forbidden` sat in the catalog looking like they covered it.

`not_callee` is how it surfaced: CallScreen acknowledged ringing on outgoing
calls too, which the backend refuses with 403 `not_callee`, and the log line read
`error_code=UNKNOWN_ERROR`. An always-present failure wearing the name of a
generic backend error is worse than no log at all, because it trains you to
ignore the one code that would have named a real fault.

This is a static audit rather than a behavioural test on purpose: the failure is
a *missing* mapping, and you cannot reach every `_err` call site from a request
without standing up most of the call state machine. Parsing the module is both
cheaper and more complete.
"""

import ast
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENGINE = os.path.join(ROOT, "services", "pulsesoc_communications_engine.py")


def _module():
    with open(ENGINE, "r", encoding="utf-8") as handle:
        return ast.parse(handle.read()), handle


def _catalog_keys(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "ERROR_CATALOG":
                    return {
                        key.value
                        for key in node.value.keys
                        if isinstance(key, ast.Constant) and isinstance(key.value, str)
                    }
    raise AssertionError("ERROR_CATALOG is not a module-level dict literal any more")


# Not every `_err` code is written as a literal, and forbidding that would be the
# wrong fix — `access` is the honest way to forward a decision another module
# made. What a computed code loses is *auditability*, so each one is declared
# here with the closed set of values it can take, keyed by the source expression
# rather than a line number so the mapping survives edits above it.
#
# These four are why this matters: `access` comes from
# `comm_service._conversation_access`, which returns "missing"/"denied"/"blocked"
# — not the "missing_conversation"/"forbidden" codes the catalog already had. So
# the single most-travelled failure on the call surface, "that conversation isn't
# yours", answered UNKNOWN_ERROR while a plausible-looking entry sat nearby
# untouched. A literals-only audit cannot see that; it reports the file clean.
COMPUTED_CODES = {
    # pulse_communications_v2.service._conversation_access; "ok" is excluded at
    # every call site by `if access != "ok"`.
    "access": {"missing", "denied", "blocked"},
    # rtc_provider() is hard-coded to "agora"; the f-string is a seam for a
    # second provider, so both halves are pinned below by _provider_is_agora.
    "f'{selected_provider}_token_failed'": {"agora_token_failed"},
    # pulsesoc_voip_push.register_voip_token's own failure statuses.
    "str(result.get('status') or 'invalid')": {"unauthenticated", "invalid"},
}


def _err_code_nodes(tree):
    """Every `_err` code argument, positional or keyword."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = getattr(func, "id", None) or getattr(func, "attr", None)
        if name != "_err":
            continue
        found = None
        if len(node.args) >= 3:
            found = node.args[2]
        for kw in node.keywords:
            if kw.arg == "code":
                found = kw.value
        if found is None:
            continue  # relies on the `code="error"` default
        yield node, found


def _raised_codes(tree):
    """Codes reachable through `_err`, literal and computed alike.

    Computed expressions are resolved through COMPUTED_CODES. An expression that
    is not declared there is returned separately so
    `test_computed_codes_are_declared` names it instead of letting it shrink the
    audited set in silence.
    """
    codes = set()
    undeclared = []
    for node, found in _err_code_nodes(tree):
        if isinstance(found, ast.Constant) and isinstance(found.value, str):
            codes.add(found.value)
            continue
        source = ast.unparse(found)
        if source in COMPUTED_CODES:
            codes |= COMPUTED_CODES[source]
        else:
            undeclared.append((getattr(node, "lineno", 0), source))
    return codes, undeclared


class CallErrorCatalogTest(unittest.TestCase):
    def setUp(self):
        self.tree, _ = _module()

    def test_every_raised_code_has_a_catalog_entry(self):
        """MUTATION: delete any entry from ERROR_CATALOG.

        The fallback tuple makes a missing entry invisible at runtime, so this is
        the only place the omission can be caught.
        """
        catalog = _catalog_keys(self.tree)
        raised, _ = _raised_codes(self.tree)
        missing = sorted(raised - catalog)
        self.assertEqual(
            missing,
            [],
            "these _err codes fall through to UNKNOWN_ERROR and lose their name "
            "in the logs and in the client: %s" % ", ".join(missing),
        )

    def test_computed_codes_are_declared(self):
        """A new computed code must be declared before it can be raised.

        Without this, `_err(msg, 400, some_variable)` would silently shrink the
        set the test above checks, and the suite would stay green while coverage
        quietly fell. Declaring the value set is cheap; discovering a year later
        that a whole route family answers UNKNOWN_ERROR is not.
        """
        _, undeclared = _raised_codes(self.tree)
        self.assertEqual(
            undeclared,
            [],
            "undeclared computed _err codes (line, expression): %s — add the "
            "closed set of values it can take to COMPUTED_CODES" % undeclared,
        )

    def test_declared_computed_codes_still_exist(self):
        """COMPUTED_CODES must not outlive its call sites.

        A stale entry is worse than a missing one: it keeps injecting values into
        the audited set, so `test_every_raised_code_has_a_catalog_entry` keeps
        vouching for catalog entries nothing reaches any more, and a real gap
        opening elsewhere is masked by the noise.
        """
        live = {ast.unparse(found) for _, found in _err_code_nodes(self.tree)}
        stale = sorted(set(COMPUTED_CODES) - live)
        self.assertEqual(stale, [], "COMPUTED_CODES entries with no call site: %s" % stale)

    def test_the_provider_token_code_matches_the_only_provider(self):
        """Pins both halves of `f"{selected_provider}_token_failed"`.

        The f-string is a seam for a second RTC provider. If one is ever added,
        `rtc_provider()` stops returning "agora", this fails, and whoever added it
        is told to catalog `<provider>_token_failed` — rather than shipping a
        provider whose token failures are nameless.
        """
        with open(ENGINE, "r", encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn('return "agora"', source, "rtc_provider() no longer returns only agora")
        self.assertEqual(COMPUTED_CODES["f'{selected_provider}_token_failed'"], {"agora_token_failed"})

    def test_the_audit_can_actually_fail(self):
        """Positive control.

        `_raised_codes` returning an empty set would make the audit vacuous — it
        would pass against a catalog with every entry deleted. Pin that it finds a
        real, non-trivial number of codes, and that a code known to have been
        missing is now present.
        """
        catalog = _catalog_keys(self.tree)
        raised, _ = _raised_codes(self.tree)
        self.assertGreater(len(raised), 15, "the _err scan found almost nothing; it is probably broken")
        self.assertIn("not_callee", raised, "the code that exposed this class must still be raised")
        self.assertIn("not_callee", catalog)
        # The computed arm needs its own control: if COMPUTED_CODES stopped being
        # consulted, the literal arm alone would still clear every assertion
        # above while the four costliest codes went back to UNKNOWN_ERROR.
        self.assertIn("denied", raised, "computed codes are no longer being resolved")
        self.assertIn("denied", catalog)

    def test_catalog_entries_are_complete_tuples(self):
        """Each entry unpacks into exactly four fields.

        `_error_details` destructures the tuple directly, so a three- or
        five-element entry is a TypeError raised on the *error* path — the path
        that runs when something has already gone wrong.
        """
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "ERROR_CATALOG" for t in node.targets
            ):
                for key, value in zip(node.value.keys, node.value.values):
                    self.assertIsInstance(value, ast.Tuple, "%s is not a tuple" % key.value)
                    self.assertEqual(
                        len(value.elts),
                        4,
                        "%s has %d fields, expected 4 "
                        "(error_code, title, description, remediation)" % (key.value, len(value.elts)),
                    )


class EngineDictHygieneTest(unittest.TestCase):
    """A second silent-fault audit of the same module, for the same reason.

    Python accepts a duplicate key in a dict literal without a warning: the last
    wins and any earlier value is evaluated and thrown away. `_serialize_call`
    carried `"agora": agora_config_status()` twice, so every call serialization —
    which is every status poll — called it an extra time and discarded the
    result. Harmless in effect, invisible in review, and indistinguishable from
    an intentional override; the reason to pin it is that the *next* one might
    shadow a key that matters.
    """

    def test_no_dict_literal_repeats_a_key(self):
        tree, _ = _module()
        offenders = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            seen = set()
            for key in node.keys:
                if not (isinstance(key, ast.Constant) and isinstance(key.value, str)):
                    continue
                if key.value in seen:
                    offenders.append((getattr(node, "lineno", 0), key.value))
                seen.add(key.value)
        self.assertEqual(
            offenders,
            [],
            "duplicate keys (line, key) — the earlier value is computed and "
            "discarded: %s" % offenders,
        )


if __name__ == "__main__":
    unittest.main()
