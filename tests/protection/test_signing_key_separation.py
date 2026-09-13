"""One root secret must not mean one signing key.

`COINPILOTX_SECRET_KEY` signed five unrelated credential families: the Flask
session cookie, the mobile bearer access token, messenger media URLs, password
reset links, and the arithmetic captcha. Five purposes on one key means you
cannot rotate one without rotating all five, and the costs are not comparable.
Measured, by reading what each family does on a key change:

    family              rotation cost              how it recovers
    ------------------  -------------------------  --------------------------
    Flask session       every web user logged out,  re-login, and only that
                        permanently
    mobile access       <= 15 minutes               automatic (refresh + replay)
    messenger media     <= 15 minutes               automatic (URLs re-mint)
    password reset      <= 1h of pending links      request another reset
    captcha             one request                 retry

The asymmetry runs the opposite way to intuition, and that is the finding. The
bearer key is nearly free to rotate on its own: the refresh token is a random
string hashed with a plain SHA-256, it does not involve this secret, and
`/api/mobile/auth/refresh` accepts it without a bearer, so a phone recovers by
itself inside fifteen minutes. But while the two shared a key, rotating the
cheap one forced a permanent logout of every web session -- the cookie is
client-side signed, has no server-side row to migrate, and lives ten years. The
coupling taxed precisely the operation you most want to perform quickly.

WHAT THIS FILE PROVES, AND WHAT IT DELIBERATELY DOES NOT
--------------------------------------------------------
The separation property is proved against `services/signing_keys.py`, which is
pure and importable. The *wiring* is proved by parsing `bot.py` rather than
importing it, for the reason the sibling file gives: importing boots a 124k-line
monolith whose secret guard is a boot-time `raise`.

Source assertions are weak evidence in general, so the two that carry real
weight are written to fail on the specific mistakes that are easy to make here
rather than on cosmetic drift -- the root signing something again, and the
legacy reset hash being computed from `webhook_app.secret_key`.

The Flask fallback semantics are proved with a real cookie round-trip on a real
Flask app. That is an external-library behaviour this migration bets on, and
"Flask 3.1 added SECRET_KEY_FALLBACKS" read from a changelog is not the same
claim as "this app will not log its users out on deploy". Every such test is
paired with a negative control, because a round-trip that would succeed without
the fallback proves nothing about the fallback.

    python tests/protection/test_signing_key_separation.py
"""

from __future__ import annotations

import ast
import hashlib
import hmac
import os
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

BOT = (REPO / "bot.py").read_text(encoding="utf-8")

from services import signing_keys  # noqa: E402

ROOT = "test-root-secret-not-a-real-one"


def _function_code(name):
    """One top-level function's source, with its docstring removed.

    The docstring has to go. The first draft of
    `test_the_legacy_reset_hash_does_not_read_the_rotated_attribute` read a
    fixed-size window of characters and failed against correct code, because
    that function's docstring *explains the trap by naming it* -- the test was
    matching its own explanation. Widening or narrowing the window would have
    made the failure go away without making the test correct: any comment or
    docstring that discusses the forbidden expression would trip it, and,
    worse, the reverse mistake is silent. A prose mention could just as easily
    satisfy an assertion that a required expression *is* present, and then the
    test passes while the code does the wrong thing.

    So the comparison is against executable code only.
    """
    start = BOT.index(f"def {name}(")
    rest = BOT[start:]
    source = rest[:rest.index("\ndef ", 1)]
    fn = ast.parse(source).body[0]
    first = fn.body[0]
    if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)):
        fn.body = fn.body[1:]
    # `ast.unparse` also normalises away comments, which is the same argument.
    return ast.unparse(fn)


def _clear_overrides():
    """Drop every per-purpose override from the environment.

    The overrides are read on every `derive()` call by design, so a stray one in
    the developer's shell or in `.env.local` would silently replace a derived
    key and make the separation tests below pass for the wrong reason.
    """
    for name in signing_keys.OVERRIDE_ENV.values():
        os.environ.pop(name, None)


# --- 1. The keys must actually be separate -----------------------------------

def test_every_purpose_gets_a_different_key():
    _clear_overrides()
    keys = {p: signing_keys.derive(ROOT, p) for p in signing_keys.PURPOSES}
    assert len(signing_keys.PURPOSES) == 5, "a purpose was added or removed"
    assert len(set(keys.values())) == 5, (
        f"two purposes derive to the same key, which re-couples their rotation: "
        f"{keys}"
    )


def test_no_derived_key_equals_the_root():
    """Otherwise one family is still signing with the shared secret."""
    _clear_overrides()
    for purpose in signing_keys.PURPOSES:
        assert signing_keys.derive(ROOT, purpose) != ROOT, purpose


def test_derivation_is_stable_across_calls_and_processes():
    """A key that is not a pure function of the root is a per-worker key.

    The Procfile runs multiple gunicorn workers, each importing `bot`
    separately. If derivation involved a salt, a timestamp or any process state,
    worker A would mint tokens worker B rejects -- which is the exact failure the
    root's own boot guard exists to prevent, reintroduced one layer down.
    """
    _clear_overrides()
    first = [signing_keys.derive(ROOT, p) for p in signing_keys.PURPOSES]
    second = [signing_keys.derive(ROOT, p) for p in signing_keys.PURPOSES]
    assert first == second
    # Recomputed here from the documented scheme rather than asserted against a
    # frozen literal, so that a *deliberate* scheme change (bumping the version
    # in the label) is a one-line edit, while an accidental one still fails.
    expected = hmac.new(
        ROOT.encode("utf-8"), b"pulsesoc/key/v1/session", hashlib.sha256
    ).hexdigest()
    assert signing_keys.derive(ROOT, signing_keys.SESSION) == expected


def test_a_different_root_changes_every_key():
    _clear_overrides()
    a = {signing_keys.derive(ROOT, p) for p in signing_keys.PURPOSES}
    b = {signing_keys.derive(ROOT + "x", p) for p in signing_keys.PURPOSES}
    assert not (a & b), "a derived key survived a root change"


# --- 2. Independent rotation, which is the entire point ----------------------

def test_overriding_one_purpose_leaves_the_other_four_untouched():
    """The mechanism the whole split exists to provide.

    Rotating the mobile bearer key must not change the session key, because
    changing the session key is what logs every web user out permanently.
    """
    _clear_overrides()
    before = {p: signing_keys.derive(ROOT, p) for p in signing_keys.PURPOSES}
    os.environ[signing_keys.OVERRIDE_ENV[signing_keys.MOBILE_ACCESS]] = "rotated-value"
    try:
        after = {p: signing_keys.derive(ROOT, p) for p in signing_keys.PURPOSES}
    finally:
        _clear_overrides()
    assert after[signing_keys.MOBILE_ACCESS] == "rotated-value", (
        "the per-purpose override did not take effect, so a single family "
        "cannot be rotated and the split buys nothing operationally."
    )
    for purpose in signing_keys.PURPOSES:
        if purpose == signing_keys.MOBILE_ACCESS:
            continue
        assert after[purpose] == before[purpose], (
            f"rotating the mobile key also changed {purpose}. That is the "
            f"coupling this module was written to remove."
        )
    # And the environment really is clean again, or every later test in this
    # file inherits a rotated key and proves nothing.
    assert signing_keys.derive(ROOT, signing_keys.MOBILE_ACCESS) == \
        before[signing_keys.MOBILE_ACCESS]


def test_an_override_is_read_per_call_not_captured_at_import():
    """Otherwise setting the variable appears to work and does nothing."""
    _clear_overrides()
    name = signing_keys.OVERRIDE_ENV[signing_keys.CAPTCHA]
    derived = signing_keys.derive(ROOT, signing_keys.CAPTCHA)
    os.environ[name] = "late-bound"
    try:
        assert signing_keys.derive(ROOT, signing_keys.CAPTCHA) == "late-bound"
    finally:
        _clear_overrides()
    assert signing_keys.derive(ROOT, signing_keys.CAPTCHA) == derived


def test_a_blank_override_falls_through_rather_than_signing_with_empty():
    """An unset Railway variable arrives as "" far more often than as absent.

    Treating it as an override would make every key the empty string, which is
    a valid HMAC key -- so it would not raise, it would silently sign every
    token in the system with a publicly-known secret.
    """
    _clear_overrides()
    derived = signing_keys.derive(ROOT, signing_keys.SESSION)
    for blank in ("", "   ", "\n"):
        os.environ[signing_keys.OVERRIDE_ENV[signing_keys.SESSION]] = blank
        try:
            assert signing_keys.derive(ROOT, signing_keys.SESSION) == derived, (
                f"a blank override ({blank!r}) was treated as a real key"
            )
        finally:
            _clear_overrides()


# --- 3. Failing loudly beats deriving something plausible --------------------

def test_an_unknown_purpose_is_refused():
    """A typo must not mint a sixth key family that verifies against nothing."""
    _clear_overrides()
    for bad in ("sessions", "Session", "mobile_access", ""):
        try:
            signing_keys.derive(ROOT, bad)
        except ValueError:
            continue
        raise AssertionError(f"derive() accepted unknown purpose {bad!r}")


def test_an_empty_root_is_refused():
    _clear_overrides()
    for empty in ("", None):
        try:
            signing_keys.derive(empty, signing_keys.SESSION)  # type: ignore[arg-type]
        except (ValueError, AttributeError):
            continue
        raise AssertionError(f"derive() accepted empty root {empty!r}")


def test_the_override_table_covers_every_purpose():
    """A purpose missing from the table cannot be rotated independently."""
    assert set(signing_keys.OVERRIDE_ENV) == set(signing_keys.PURPOSES)
    assert len(set(signing_keys.OVERRIDE_ENV.values())) == len(signing_keys.PURPOSES), (
        "two purposes share an override variable, so rotating one rotates both."
    )
    for name in signing_keys.OVERRIDE_ENV.values():
        assert name.startswith("PULSESOC_") and name.endswith("_SECRET"), name


# --- 4. The deploy must not log anybody out ----------------------------------

def _session_round_trip(sign_with, read_with, fallbacks):
    """Serialise a session under one key and try to read it under another.

    Uses Flask's real session interface rather than a reimplementation, because
    the claim under test is about Flask's behaviour, not about ours.
    """
    from flask import Flask

    writer = Flask(__name__)
    writer.secret_key = sign_with
    interface = writer.session_interface
    serializer = interface.get_signing_serializer(writer)
    cookie = serializer.dumps({"account_user_id": 8})

    reader = Flask(__name__)
    reader.secret_key = read_with
    reader.config["SECRET_KEY_FALLBACKS"] = fallbacks
    try:
        return reader.session_interface.get_signing_serializer(reader).loads(cookie)
    except Exception:
        return None


def test_a_cookie_signed_with_the_old_root_still_opens_after_the_split():
    """The claim that makes this migration safe to deploy.

    Without this, switching `app.secret_key` to the derived key logs out every
    web user at the moment of deploy, with no recovery but re-login.
    """
    derived = signing_keys.derive(ROOT, signing_keys.SESSION)
    opened = _session_round_trip(ROOT, derived, [ROOT])
    assert opened == {"account_user_id": 8}, (
        "an existing session cookie did not survive the key split. Every logged-in "
        "web user would be signed out by this deploy."
    )


def test_the_fallback_is_what_makes_that_work():
    """Negative control. Without it the test above could be passing for free.

    If Flask ignored `secret_key` entirely, or if the derived key happened to
    equal the root, the previous test would pass while proving nothing.
    """
    derived = signing_keys.derive(ROOT, signing_keys.SESSION)
    assert _session_round_trip(ROOT, derived, []) is None, (
        "a cookie signed with the old root opened under the derived key with no "
        "fallback configured, so that test is not measuring the fallback."
    )


def test_new_cookies_are_signed_with_the_derived_key_not_the_root():
    """A fallback that is also the signing key is not a migration, it is a no-op.

    The fallback exists so old cookies keep opening; it must not mean new
    cookies keep being signed with the shared root, or the split never
    completes and the session key can never actually be rotated.
    """
    derived = signing_keys.derive(ROOT, signing_keys.SESSION)
    # A reader holding only the derived key must accept what the new app signs.
    assert _session_round_trip(derived, derived, []) == {"account_user_id": 8}
    # And a reader holding only the root must not.
    assert _session_round_trip(derived, ROOT, []) is None


# --- 5. bot.py must be wired to all of it ------------------------------------

def test_every_override_variable_is_named_in_env_example():
    """The runtime, the tests and the template must not disagree on a spelling.

    An override is read by name and falls through silently when absent, so a
    typo in the template is not a broken deploy -- it is an operator setting a
    variable, seeing the service restart cleanly, and believing a key was
    rotated when nothing changed. There is no error to notice.
    """
    template = (REPO / ".env.example").read_text(encoding="utf-8")
    for purpose, name in signing_keys.OVERRIDE_ENV.items():
        assert f"\n{name}=" in template, (
            f"{name} (the override for {purpose!r}) is missing from .env.example, "
            f"so setting it is undiscoverable and misspelling it is silent."
        )


def test_bot_derives_all_five_keys_from_the_root():
    for purpose_const, key_name in (
        ("SESSION", "COINPILOTX_SESSION_KEY"),
        ("MOBILE_ACCESS", "COINPILOTX_MOBILE_ACCESS_KEY"),
        ("MESSENGER_MEDIA", "COINPILOTX_MESSENGER_MEDIA_KEY"),
        ("PASSWORD_RESET", "COINPILOTX_PASSWORD_RESET_KEY"),
        ("CAPTCHA", "COINPILOTX_CAPTCHA_KEY"),
    ):
        line = (
            f"{key_name} = _signing_keys.derive("
            f"COINPILOTX_SECRET_KEY, _signing_keys.{purpose_const})"
        )
        assert line in BOT, f"bot.py no longer derives {key_name}: expected {line!r}"


def test_the_root_signs_nothing_directly_any_more():
    """The regression that would undo the split without removing any of it.

    Someone adds a sixth credential family, reaches for the variable that is
    already in scope, and the new family is coupled to all five again.
    """
    assert "hmac.new(COINPILOTX_SECRET_KEY.encode" not in BOT
    assert "COINPILOTX_SECRET_KEY.encode(" not in BOT, (
        "the root secret is being used as signing material somewhere. It is a "
        "derivation input only; every signer takes a derived key."
    )


def test_the_session_fallback_is_configured_at_both_app_constructions():
    """`webhook_app = Flask(...)` appears twice and the second wins.

    Configuring only one is a 50/50 bet on which block a future edit moves, and
    the losing outcome is a silent mass logout.
    """
    assert BOT.count("webhook_app.secret_key = COINPILOTX_SESSION_KEY") == 2
    assert BOT.count("SECRET_KEY_FALLBACKS=[COINPILOTX_SECRET_KEY]") == 2
    assert "webhook_app.secret_key = COINPILOTX_SECRET_KEY" not in BOT


def test_the_captcha_no_longer_rides_on_the_session_key():
    """It read `app.secret_key`, which now holds the derived session key.

    That would have kept working, which is what makes it worth a test: the
    captcha would have been silently re-coupled to session rotation instead of
    being independent, and nothing would have failed to say so.
    """
    for name in ("issue_login_challenge", "verify_login_challenge"):
        body = _function_code(name)
        assert "app.secret_key" not in body, (
            f"{name} still interpolates app.secret_key, so the captcha is tied "
            f"to the session key rather than to its own."
        )
        assert "COINPILOTX_CAPTCHA_KEY" in body, (
            f"{name} does not use the captcha key. Issue and verify must agree "
            f"or every captcha answer is rejected."
        )


def test_the_legacy_reset_hash_does_not_read_the_rotated_attribute():
    """The subtlest way this migration could fail, and only for its beneficiaries.

    `password_reset_token_hash_legacy` reproduces how existing rows were
    indexed. Its final fallback used to be `webhook_app.secret_key` -- which now
    holds the *derived session key*. Left as-is it would compute a hash that
    never indexed anything, so the fallback would match zero rows: broken in
    exactly the way it exists to prevent, and invisible except to users holding
    a link issued before the deploy.
    """
    body = _function_code("password_reset_token_hash_legacy")
    assert "webhook_app.secret_key" not in body, (
        "the legacy reset hash reads webhook_app.secret_key, which is now the "
        "derived session key, not the pre-split root. Pending reset links would "
        "silently stop resolving."
    )
    assert "COINPILOTX_SECRET_KEY" in body, (
        "the legacy reset hash no longer falls back to the root, so rows written "
        "before the split cannot be found."
    )


def test_pending_reset_links_are_looked_up_under_both_hashes():
    """The reset hash is the index, not a signature: changing it orphans rows."""
    body = _function_code("load_password_reset_record")
    assert "password_reset_token_hash(token)" in body
    assert "password_reset_token_hash_legacy(token)" in body, (
        "load_password_reset_record no longer tries the pre-split hash, so every "
        "reset link issued before this deploy is orphaned: the row is present "
        "and unexpired and the query returns nothing."
    )
    # New rows must be written under the new hash only, or the migration never
    # ends and the legacy branch can never be deleted.
    assert "password_reset_token_hash_legacy" not in _function_code("create_password_reset"), (
        "new reset rows are being indexed under the legacy hash."
    )


def test_mobile_tokens_are_minted_and_verified_with_the_same_derived_key():
    """Minting with one key and verifying with another is a total auth outage."""
    assert BOT.count("hmac.new(COINPILOTX_MOBILE_ACCESS_KEY.encode") == 2, (
        "expected exactly two mobile-access signing sites (mint and verify)."
    )


def test_messenger_media_uses_its_own_key_at_every_call_site():
    for fn in ("mint_access_token", "access_token_user_id", "access_token_state"):
        assert f"messenger_media_foundation.{fn}(COINPILOTX_MESSENGER_MEDIA_KEY" in BOT, fn
    assert "messenger_media_foundation.mint_access_token(COINPILOTX_SECRET_KEY" not in BOT


if __name__ == "__main__":
    import pathlib as _pathlib
    import sys as _sys

    _sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
    from _runner import run_module_tests

    raise SystemExit(run_module_tests(globals()))
