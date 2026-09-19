"""The website may promote the app, but only on the terms in the brief.

Every one of these assertions exists because breaking it is invisible in a
screenshot. A Smart App Banner on a legal page, a QR that still encodes last
year's listing, a dismissal that only lasts until the next navigation, a header
control that counts as a promotional surface and therefore hides itself for a
week after one unrelated dismissal -- all of those render fine and all of them
are the failure.

`app_links` owns where a link goes and is tested separately. This file owns the
other half: which surfaces exist, what they say, and when one is allowed to
appear.

Run: python3 -m pytest tests/test_app_promotion.py
"""

import json
import re
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services import app_links, app_promotion


POLICY_JS = (ROOT / "static" / "js" / "pulse_app_promotion.js").read_text()
PWA_JS = (ROOT / "static" / "js" / "pulse_pwa_install.js").read_text()
PROMO_CSS = (ROOT / "static" / "css" / "pulse_app_promotion.css").read_text()


def card(**kwargs):
    kwargs.setdefault("surface", app_promotion.SURFACE_DESKTOP_CARD)
    kwargs.setdefault("dismissible", True)
    return app_promotion.promotion_card_html(**kwargs)


def hrefs(html):
    return re.findall(r'href="([^"]*)"', html)


# ---------------------------------------------------------------------------
# The approved surfaces say the approved things
# ---------------------------------------------------------------------------


def test_the_card_carries_the_exact_approved_copy():
    html = card()
    assert ">PulseSoc for iPhone<" in html
    assert ">Create, message, go live and take PulseSoc anywhere.<" in html


def test_the_marketplace_note_carries_the_exact_approved_copy():
    html = app_promotion.marketplace_note_html()
    assert ">Marketplace lives in the app<" in html
    assert ">Scan to open or download PulseSoc.<" in html


def test_the_header_control_is_labelled_get_pulsesoc():
    html = app_promotion.header_control_html()
    assert ">Get PulseSoc<" in html
    assert html.startswith("<details")


def test_every_surface_links_to_the_one_app_store_url():
    for html in (
        card(),
        app_promotion.marketplace_note_html(),
        app_promotion.header_control_html(),
    ):
        store = [h for h in hrefs(html) if "apps.apple.com" in h]
        assert store == [app_links.app_store_url()]
        assert store == ["https://apps.apple.com/us/app/pulsesoc/id6777591572"]


def test_the_qr_is_rendered_and_described():
    html = card()
    assert app_links.app_store_qr_asset() in html
    assert "QR code that opens the PulseSoc listing on the App Store" in html
    assert "Scan with your iPhone camera." in html


# ---------------------------------------------------------------------------
# Smart App Banner scope -- the decision was "landing + search only"
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/", "/search", "/search?q=wallet"])
def test_the_banner_appears_on_the_two_approved_paths(path):
    meta = app_promotion.smart_app_banner_meta(path)
    assert 'name="apple-itunes-app"' in meta
    assert "app-id=6777591572" in meta


@pytest.mark.parametrize(
    "path",
    ["/about", "/privacy", "/terms", "/login", "/signup", "/pulse", "/pulse/feed",
     "/open/marketplace", "/pulse/marketplace/7", ""],
)
def test_the_banner_appears_nowhere_else(path):
    assert app_promotion.smart_app_banner_meta(path) == ""


def test_the_banner_app_argument_is_the_site_root_not_the_page():
    # iOS hands app-argument to the app as a destination. Naming this page
    # would be a promise the website cannot keep -- there is no app screen for
    # a marketing page, and the banner is shown whether or not the app exists.
    meta = app_promotion.smart_app_banner_meta("/search?q=anything")
    assert "app-argument=https://pulsesoc.com/" in meta
    assert "search" not in meta
    assert "q=anything" not in meta


def test_the_banner_id_is_read_out_of_the_app_store_url():
    assert app_promotion.app_store_app_id() in app_links.app_store_url()


# ---------------------------------------------------------------------------
# The APP marker follows the destination, not a list of labels
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("href", ["/open/marketplace", "/open/seller", "/open/home"])
def test_app_first_links_get_the_marker(href):
    assert app_promotion.nav_marker_html(href) != ""
    assert app_promotion.nav_marker_attrs(href) == ' data-app-promo-marketplace="1"'


@pytest.mark.parametrize(
    "href",
    ["/pulse/feed", "/pulse/marketplace", "/settings", "https://example.com/open/x",
     "", None],
)
def test_web_links_do_not_get_the_marker(href):
    assert app_promotion.nav_marker_html(href) == ""
    assert app_promotion.nav_marker_attrs(href) == ""


def test_the_pill_reads_as_a_sentence_to_a_screen_reader():
    # "APP" alone is meaningless in a list of navigation links.
    html = app_promotion.marketplace_pill_html()
    assert '<span aria-hidden="true">APP</span>' in html
    assert ">Opens in the PulseSoc iPhone app<" in html
    assert 'title="Opens in the PulseSoc iPhone app"' in html


def test_the_pill_does_not_name_a_screen_it_may_not_be_next_to():
    # The same marker lands on Seller Tools. An accessible name that says
    # "Marketplace" there is worse than no marker.
    assert "Marketplace" not in app_promotion.MARKETPLACE_PILL_TITLE


# ---------------------------------------------------------------------------
# Frequency policy
# ---------------------------------------------------------------------------


def test_the_header_control_is_not_an_arbitrated_surface():
    # It is navigation chrome: inert until pressed, never opens itself. If it
    # were arbitrated, one dismissal of an unrelated card would leave the site
    # with no discoverable route to the app for a week.
    assert app_promotion.SURFACE_HEADER not in app_promotion.SURFACE_POLICY
    assert app_promotion.SURFACE_HEADER not in app_promotion.ARBITRATED_SURFACES


def test_the_header_control_cannot_be_dismissed():
    html = app_promotion.header_control_html()
    assert "data-app-promo-dismiss" not in html


def test_the_interrupting_surface_is_once_per_session_and_the_furniture_is_not():
    assert app_promotion.SURFACE_POLICY[app_promotion.SURFACE_MARKETPLACE_NOTE][
        "oncePerSession"
    ] is True
    assert app_promotion.SURFACE_POLICY[app_promotion.SURFACE_DESKTOP_CARD][
        "oncePerSession"
    ] is False


def test_a_dismissal_is_remembered_for_seven_days():
    config = app_promotion.runtime_config()
    assert config["dismissMemoryDays"] == 7
    assert config["dismissMemoryMs"] == 7 * 24 * 60 * 60 * 1000


def test_the_browser_gets_exactly_the_arbitrated_surfaces():
    config = app_promotion.runtime_config()
    assert set(config["surfaces"]) == set(app_promotion.ARBITRATED_SURFACES)
    assert json.dumps(config)  # must survive the inline <script> serialization


def test_the_runtime_config_script_is_valid_json_in_one_assignment():
    script = app_promotion.runtime_config_script()
    payload = script.split("window.PULSE_APP_PROMOTION=", 1)[1].rsplit(";</script>", 1)[0]
    assert json.loads(payload) == app_promotion.runtime_config()


def test_every_uninvited_surface_ships_with_a_dismiss_control():
    for html in (card(), app_promotion.marketplace_note_html()):
        assert "data-app-promo-dismiss" in html
        assert "aria-label=" in html


def test_the_marketplace_note_ships_hidden():
    # It is an explanation of a gesture, not something a page opens with.
    assert " hidden>" in app_promotion.marketplace_note_html()


def test_the_sidebar_card_ships_visible_so_removing_it_costs_no_layout_shift():
    assert " hidden>" not in card()
    assert " hidden>" in card(hidden=True)


def test_the_two_promotion_scripts_stand_down_for_each_other():
    # Neither may ask for an install while the other is already asking.
    assert "PulseAppPromotion.hasVisibleSurface()" in PWA_JS
    assert "hasVisibleSurface" in POLICY_JS
    assert "data-pulse-pwa-install" in POLICY_JS


def test_a_dismissal_is_written_to_two_stores():
    # localStorage carries the week. sessionStorage is what survives private
    # browsing, where a dismissal would otherwise last zero navigations.
    assert "sessionStoragePrefix" in POLICY_JS
    assert app_promotion.STORAGE_PREFIX != app_promotion.SESSION_STORAGE_PREFIX


def test_the_marketplace_explanation_is_not_triggered_by_a_click():
    # A click on an app-first link navigates to /open/, which destroys the
    # page the explanation would have appeared on.
    assert "pointerenter" in POLICY_JS
    assert 'addEventListener("click", ' not in POLICY_JS.replace(
        'addEventListener("click", onDismissClick', ""
    ).replace('addEventListener("click", onActionClick', "")


def test_a_surface_the_page_css_hides_does_not_consume_the_budget():
    # `hidden`-only visibility makes a card that CSS never paints still count as
    # the one allowed surface, which suppresses every other surface for the
    # whole page view. On the feed that meant the card was invisible AND the
    # Marketplace note could never appear: zero surfaces, silently.
    assert "getClientRects().length > 0" in POLICY_JS


def test_the_card_opts_into_the_feed_rails_allowlist():
    # `.pulse-home-os .pulse-desktop-right > *` is display:none with five named
    # exceptions. A markup-count assertion passes while the card never paints.
    stylesheet = re.sub(r"\s+", "", re.sub(r"/\*.*?\*/", "", PROMO_CSS, flags=re.S))
    assert re.search(
        r"\.pulse-home-os\.pulse-desktop-right>\.pulse-app-promo\{display:grid;?\}",
        stylesheet,
    )
    # ...and a dismissed card must not be resurrected by that same opt-in.
    assert re.search(
        r"\.pulse-home-os\.pulse-desktop-right>\.pulse-app-promo\[hidden\]"
        r"\{display:none!important;?\}",
        stylesheet,
    )


def test_a_suppressed_marketplace_hover_does_not_burn_the_trigger():
    # The sidebar card is visible at first paint on every desktop shell page,
    # so the first Marketplace hover is always suppressed by the one-surface
    # rule. Retiring the listeners on that attempt leaves the note unreachable
    # for the rest of the page view -- including right after the card is
    # dismissed, which is the only moment it has room to appear.
    body = POLICY_JS[POLICY_JS.index("function watchMarketplaceIntent") :]
    body = body[: body.index("\n  function start(")]
    assert "if (show(surface)) stop();" in body
    # `{ once: true }` would retire the listener regardless of the guard above.
    assert "once: true" not in body


# ---------------------------------------------------------------------------
# Accessibility
# ---------------------------------------------------------------------------


def test_the_card_is_a_labelled_section_not_a_dialog():
    html = card()
    assert html.startswith("<section")
    assert 'role="dialog"' not in html
    assert 'aria-label="PulseSoc for iPhone"' in html


def test_the_decorative_brand_mark_is_hidden_from_the_accessibility_tree():
    assert '<img class="pulse-app-promo__mark" src="/static/brand/pulsesoc-mark-20260913.png" alt=""' in card()


def test_the_close_target_meets_the_minimum_touch_size():
    assert "min-width:44px" in PROMO_CSS.replace(" ", "")
    assert "min-height:44px" in PROMO_CSS.replace(" ", "")


def test_hidden_surfaces_stay_hidden_inside_a_grid():
    # `display:grid` on the surface would otherwise beat the UA's [hidden] rule.
    assert re.search(
        r"\.pulse-app-promo\[hidden\]\{display:none!important;?\}",
        re.sub(r"\s+", "", PROMO_CSS),
    )


def test_motion_is_reducible():
    assert "prefers-reduced-motion" in PROMO_CSS


def test_the_header_panel_sets_its_own_max_width():
    # The shell's inline <style> carries `* { max-width: 100% }`, and it is
    # emitted after every <link>. The panel's containing block is the ~137px
    # summary button, so a rule that sets only `width` is clamped to the
    # button's width: the title wraps mid-word down a 613px column. Caught in a
    # browser, not by any assertion on the markup.
    # Comments first: the one on this very rule quotes `{ max-width: 100% }`,
    # and its brace would end the captured block early.
    stylesheet = re.sub(r"\s+", "", re.sub(r"/\*.*?\*/", "", PROMO_CSS, flags=re.S))
    block = re.search(r"\.pulse-topnav-app-promo-panel\{([^}]*)\}", stylesheet)
    assert block, "the panel rule is gone"
    assert "max-width:min(320px" in block.group(1)


def test_the_qr_sits_on_a_white_plate():
    # Functional, not decorative: a QR with a dark quiet zone does not decode.
    assert "background:#ffffff" in PROMO_CSS.replace(" ", "")


# ---------------------------------------------------------------------------
# Contextual "Open in PulseSoc"
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path,expected",
    [
        ("/pulse/marketplace/7", "pulsesoc://pulse/marketplace/7"),
        ("/pulse/messages/12", "pulsesoc://pulse/messages/12"),
        ("/pulse", "pulsesoc://pulse"),
    ],
)
def test_a_native_destination_gets_a_scheme_action(path, expected):
    html = app_promotion.open_in_app_action_html(path, app_promotion.SURFACE_DESKTOP_CARD)
    assert f'href="{expected}"' in html
    assert "Open" in html


@pytest.mark.parametrize(
    "path", ["/pulse/feed", "/about", "/admin", "/", "", "/open/marketplace"]
)
def test_a_path_the_binary_cannot_open_gets_no_action(path):
    assert app_promotion.open_in_app_action_html(path, "desktop_card") == ""


def test_the_card_omits_the_native_action_unless_a_path_is_passed():
    assert "pulsesoc://" not in card()
    assert "pulsesoc://" in card(native_path="/pulse/marketplace/7")


def test_no_surface_builds_a_same_domain_app_intent_link():
    # iOS does not consult associated domains for a same-domain tap, so a
    # canonical ?pulse_app=1 link on this site reaches Flask and bounces a
    # member who HAS the app to the App Store.
    for html in (
        card(native_path="/pulse/marketplace/7"),
        app_promotion.marketplace_note_html(),
        app_promotion.header_control_html(),
    ):
        assert app_links.APP_INTENT_PARAM not in html


# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------


def test_a_surface_name_cannot_break_out_of_an_attribute():
    html = card(surface='x" onmouseover="alert(1)')
    assert 'onmouseover="alert(1)"' not in html
    assert "&quot;" in html


@pytest.mark.parametrize(
    "hostile",
    [
        "javascript:alert(1)",
        "//evil.example.com/pulse",
        "/pulse/../../etc/passwd",
        "pulsesoc://evil",
        "/pulse/marketplace/7?x=1&y=<script>",
        "https://evil.example.com/pulse/marketplace/7",
    ],
)
def test_a_hostile_path_never_becomes_an_action(hostile):
    # The safe outcome is either no button, or a button that reaches OUR app on
    # a destination the shipped binary declares. `_normalize_path` discards the
    # host by design, so an off-host URL degrades to its path rather than being
    # honoured -- what must never happen is any of the hostile input surviving
    # into the href.
    html = app_promotion.open_in_app_action_html(hostile, "desktop_card")
    if html == "":
        return
    found = re.search(r'href="([^"]*)"', html)
    assert found, html
    href = found.group(1)
    assert href.startswith("pulsesoc://pulse/")
    for fragment in ("evil.example.com", "javascript:", "..", "<script>", "?", "&"):
        assert fragment not in href


def test_telemetry_carries_a_surface_key_and_nothing_else():
    # A promotion event has no business recording which listing someone was
    # looking at.
    for value in app_promotion.TELEMETRY_EVENTS.values():
        assert value.startswith("app_promo_")
    assert "location.pathname" not in POLICY_JS
    assert "location.search" not in POLICY_JS
    assert "location.href" not in POLICY_JS


def test_the_store_link_cannot_reach_back_into_this_tab():
    assert 'rel="noopener"' in card()


# ---------------------------------------------------------------------------
# What must NOT change
# ---------------------------------------------------------------------------


def test_a_stale_store_override_yields_no_qr_rather_than_a_stale_one(monkeypatch):
    # A phone camera follows a QR before anyone reads it. No picture beats a
    # picture pointing at a listing this site no longer claims.
    monkeypatch.setenv(
        "PULSESOC_APP_STORE_URL", "https://apps.apple.com/us/app/other/id999999999"
    )
    html = card()
    assert app_links.APP_STORE_QR_ASSET not in html
    # ...and the card still works without it.
    assert "id999999999" in html


def test_a_stale_store_override_still_produces_a_consistent_banner(monkeypatch):
    monkeypatch.setenv(
        "PULSESOC_APP_STORE_URL", "https://apps.apple.com/us/app/other/id999999999"
    )
    assert "app-id=999999999" in app_promotion.smart_app_banner_meta("/")


def test_an_unusable_store_url_produces_no_banner_at_all(monkeypatch):
    monkeypatch.setattr(app_links, "app_store_url", lambda: "https://apps.apple.com/us")
    assert app_promotion.app_store_app_id() == ""
    assert app_promotion.smart_app_banner_meta("/") == ""


def test_this_module_never_becomes_a_second_link_authority():
    source = (ROOT / "services" / "app_promotion.py").read_text()
    # Every URL must come back through app_links. A literal store or scheme URL
    # here is the drift the single-authority rule exists to prevent.
    assert "apps.apple.com" not in source
    assert "pulsesoc://" not in source.split('"""')[-1]


def test_the_assets_load_config_before_the_script_that_reads_it():
    html = app_promotion.assets_html()
    assert html.index("PULSE_APP_PROMOTION") < html.index("pulse_app_promotion.js")
    assert "defer" in html


# ---------------------------------------------------------------------------
# Anti-vacuity -- each assertion above must be able to fail
# ---------------------------------------------------------------------------


def test_mutation_the_banner_id_really_is_derived_from_the_store_url(monkeypatch):
    monkeypatch.setattr(
        app_links, "app_store_url", lambda: "https://apps.apple.com/us/app/x/id12345"
    )
    assert app_promotion.app_store_app_id() == "12345"
    assert "app-id=12345" in app_promotion.smart_app_banner_meta("/")


def test_mutation_the_banner_scope_gate_is_real(monkeypatch):
    monkeypatch.setattr(app_promotion, "SMART_BANNER_PATHS", frozenset({"/about"}))
    assert app_promotion.smart_app_banner_meta("/") == ""
    assert app_promotion.smart_app_banner_meta("/about") != ""


def test_mutation_the_marker_really_follows_the_href_prefix(monkeypatch):
    monkeypatch.setattr(app_promotion, "APP_FIRST_HREF_PREFIX", "/zzz/")
    assert app_promotion.nav_marker_html("/open/marketplace") == ""
    assert app_promotion.nav_marker_html("/zzz/anything") != ""


def test_mutation_the_qr_block_really_comes_from_app_links(monkeypatch):
    monkeypatch.setattr(app_links, "app_store_qr_asset", lambda: "")
    html = card()
    assert "pulse-app-promo__qr" not in html
    assert app_promotion.QR_ALT not in html


def test_mutation_the_card_copy_really_comes_from_the_constants(monkeypatch):
    monkeypatch.setattr(app_promotion, "CARD_TITLE", "Sentinel Title")
    assert "Sentinel Title" in card()


# ---------------------------------------------------------------------------
# On iOS the install surfaces promote the App Store app, not a bookmark
# ---------------------------------------------------------------------------
#
# A home-screen shortcut on iOS is Safari wearing an app icon. It gets no push
# notifications, is not a share-sheet target, cannot hold an audio session and
# cannot answer a call -- which is most of what PulseSoc is. So an iPhone
# visitor shown "Tap Share, then Add to Home Screen" is being sent to the
# weaker of the two products we have, and the install is spent.
#
# These render identically in a screenshot to the version that promotes the
# app, which is why they are pinned here.

ACCOUNT_HTML = (ROOT / "templates" / "account.html").read_text()

HOME_SCREEN_PHRASES = (
    "Add to Home Screen",
    "add to home screen",
    "home screen",
    "Home Screen",
)


def ios_arms():
    """Every `kind === "ios" ? <this> : ...` branch in the PWA banner.

    Scoped to the iOS arms rather than to the whole file on purpose. On Android
    and desktop `beforeinstallprompt` installs a real standalone app, so "add
    to your home screen" is an accurate description of what that button does
    and has to stay. It is only on iOS that the same sentence describes a
    Safari bookmark, and only the iOS arms are pinned here.
    """

    arms = re.findall(r'kind === "ios"\s*\?\s*(.+?)\s*:\s*', PWA_JS, flags=re.S)
    assert arms, "the iOS branch is gone -- this test no longer checks anything"
    return arms


def test_the_ios_banner_never_teaches_the_add_to_home_screen_gesture():
    for arm in ios_arms():
        for phrase in HOME_SCREEN_PHRASES:
            assert phrase not in arm, f"the iOS banner still says {phrase!r}"


def test_the_account_page_never_teaches_the_add_to_home_screen_gesture():
    rendered = re.sub(r"\{#.*?#\}", "", ACCOUNT_HTML, flags=re.S)
    rendered = re.sub(r"^\s*//.*$", "", rendered, flags=re.M)
    for phrase in HOME_SCREEN_PHRASES:
        assert phrase not in rendered, f"the account page still says {phrase!r}"


def test_the_ios_banner_offers_a_real_link_to_the_app_store():
    # An `<a href>` and not a button that navigates: only an anchor lets iOS
    # hand the tap to the App Store app instead of loading apps.apple.com in a
    # web view, and only an anchor is long-pressable and reads as a link to
    # VoiceOver.
    assert "data-pulse-pwa-app-store" in PWA_JS
    assert '<a class="pulse-pwa-install__primary" href=' in PWA_JS


def test_the_account_page_offers_a_real_link_to_the_app_store():
    assert 'data-app-store-app href="{{ app_store_url() }}"' in ACCOUNT_HTML


def test_no_install_surface_spells_out_the_app_store_listing_itself():
    # `services/app_links.py` validates `PULSESOC_APP_STORE_URL` and is the one
    # place the listing is decided. A second copy in a script would keep
    # sending iPhone visitors to the old app after the server was corrected,
    # with nothing failing anywhere.
    for name, source in (("pulse_pwa_install.js", PWA_JS), ("account.html", ACCOUNT_HTML)):
        assert not re.search(r"id\d{6,}", source), f"{name} hard-codes an App Store id"


def test_the_runtime_config_publishes_the_app_store_url_from_app_links():
    assert app_promotion.runtime_config()["appStoreUrl"] == app_links.app_store_url()


def test_mutation_the_published_app_store_url_really_comes_from_app_links(monkeypatch):
    monkeypatch.setattr(app_links, "app_store_url", lambda: "https://apps.apple.com/sentinel")
    assert app_promotion.runtime_config()["appStoreUrl"] == "https://apps.apple.com/sentinel"


def test_the_ios_banner_shows_nothing_rather_than_guessing_the_listing():
    # Fails closed. With no server-supplied URL the banner is suppressed
    # entirely, because a promo whose button goes nowhere is worse than no
    # promo -- and worse than the bookmark copy it replaced.
    assert "Boolean(appStoreUrl())" in PWA_JS


def test_the_ios_banner_is_not_gated_on_safari():
    # The gesture needed Safari because only Safari can add to the home screen.
    # A link to the App Store works in Chrome, Firefox and every in-app browser
    # on iOS -- exactly the visitors who used to see nothing at all.
    assert "isSafariLike" not in PWA_JS
    assert "crios" not in PWA_JS


def test_the_injected_script_tag_carries_the_app_store_url():
    """The one source that is on every page the banner can run on.

    `PULSE_APP_PROMOTION` is published by the promotion assets, which the
    marketing shell does not render, and the Smart App Banner meta is scoped to
    a path list -- so on /about the iOS surface had nothing to read and
    suppressed itself. The URL rides on the script tag because the tag is the
    thing `bot.py` guarantees is there.
    """

    source = (ROOT / "bot.py").read_text()
    assert 'data-pulse-app-store-url="{store_url}"' in source
    assert "app_links.app_store_url()" in source
    assert "script[data-pulse-app-store-url]" in PWA_JS


def test_the_injected_app_store_url_is_escaped_for_a_quoted_attribute():
    # `html.escape` defaults to quote=False, which leaves `"` intact and would
    # let a configured URL containing one break out of the attribute.
    source = (ROOT / "bot.py").read_text()
    assert "html_escape(app_links.app_store_url(), quote=True)" in source
