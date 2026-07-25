"""R3.3 slice: effective-access override for the Premium upsell promo cards.

R3.3 closes the last known suspension-blind *presentation* surface: the two
``premium_visibility_engine.prompt_html`` upsell cards (dashboard shell + creator
analytics). Previously the card copy was driven purely by ownership
(``is_premium_user``), so a suspended owner was shown "Premium active" /
"...enabled across PulseSoc" — a usable-access claim the gates would then deny.

The fix is a single additive, backward-compatible parameter ``is_premium_override``
on ``contextual_prompt``/``prompt_html``:

    override is None  -> legacy ownership computation, byte-for-byte unchanged
    override is bool  -> caller-supplied *effective* (account-hold-aware) flag

bot.py passes the effective flag from ``_effective_premium_access`` at both call
sites; under flag off/shadow that value equals ownership, so the rendered HTML is
identical to legacy. This suite proves the engine-level contract directly (the two
bot.py call sites are additionally verified by byte-compilation + inspection, as
bot.py is not importable in this hermetic sandbox).

    python -m pytest tests/business_os/test_premium_visibility_effective_override.py
    python tests/business_os/test_premium_visibility_effective_override.py   # no pytest
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import premium_visibility_engine as pve  # noqa: E402

# An owner by legacy ownership rules (active premium_status).
OWNER = {"user_id": 400, "premium_status": "active"}
# A non-owner.
FREE = {"user_id": 401, "premium_status": "inactive"}


# 1 -- no override: legacy ownership drives the card (owner -> "Premium active") -----
def test_no_override_owner_shows_active():
    p = pve.contextual_prompt("creator", OWNER)
    assert p["is_premium"] is True
    assert p["title"] == "Premium active"


# 2 -- no override: non-owner sees the upsell variant ------------------------------
def test_no_override_free_shows_upsell():
    p = pve.contextual_prompt("creator", FREE)
    assert p["is_premium"] is False
    assert p["title"] != "Premium active"


# 3 -- override False on an OWNER: card downgrades to upsell (suspended-owner case) --
def test_override_false_owner_downgrades():
    # This is the exact suspended-owner scenario: ownership True, effective False.
    p = pve.contextual_prompt("creator", OWNER, is_premium_override=False)
    assert p["is_premium"] is False
    assert p["title"] != "Premium active"
    # ownership input dict is untouched
    assert OWNER["premium_status"] == "active"


# 4 -- override True on a FREE user: card shows active (effective-grant case) --------
def test_override_true_free_upgrades():
    p = pve.contextual_prompt("creator", FREE, is_premium_override=True)
    assert p["is_premium"] is True
    assert p["title"] == "Premium active"


# 5 -- prompt_html threads the override into rendered HTML --------------------------
def test_prompt_html_reflects_override():
    active_html = pve.prompt_html("dashboard", OWNER)  # legacy -> active
    held_html = pve.prompt_html("dashboard", OWNER, is_premium_override=False)
    assert "Premium active" in active_html
    assert "Premium active" not in held_html
    # the non-owner/held variant surfaces the upsell CTA instead
    assert "Explore Premium" in held_html


# 6 -- override None is identical to omitting it (byte-for-byte legacy) --------------
def test_none_override_is_legacy_identical():
    for surface in ("dashboard", "creator", "profile", "messenger"):
        for u in (OWNER, FREE):
            assert (pve.prompt_html(surface, u)
                    == pve.prompt_html(surface, u, is_premium_override=None))


# --- standalone runner ------------------------------------------------------
def _run_standalone():
    tests = [
        test_no_override_owner_shows_active,
        test_no_override_free_shows_upsell,
        test_override_false_owner_downgrades,
        test_override_true_free_upgrades,
        test_prompt_html_reflects_override,
        test_none_override_is_legacy_identical,
    ]
    passed = 0
    for t in tests:
        t()
        print(f"PASS  {t.__name__}")
        passed += 1
    print(f"\n{passed}/{len(tests)} tests passed")
    return passed == len(tests)


if __name__ == "__main__":
    ok = _run_standalone()
    raise SystemExit(0 if ok else 1)
