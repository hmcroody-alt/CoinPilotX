"""The sign-in artwork activates by existing, and costs nothing when it doesn't.

The artwork is owner-supplied and is not in the repository yet. That makes the
*absent* case the one that actually ships today, and it is the one with a
failure mode: a CSS `background-image` naming a file that is not there is a 404
on every sign-in render, shows nothing, and reports nothing. So absence has to
mean "no rule emitted", which is a thing worth asserting rather than assuming.

The second half is the cache. `static/` is served with a long immutable cache
keyed on the path, so artwork under a fixed name can never be replaced -- every
returning member keeps the first one their browser ever saw. The date token in
the filename is what makes a replacement deliverable, and the newest-wins glob
is what makes the token work without a code change.
"""

import os
import pathlib
import tempfile

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/login_hero.db")
os.environ.setdefault("SECRET_KEY", "login-hero-test")

import bot  # noqa: E402

BRAND = pathlib.Path(bot.webhook_app.static_folder or "static") / "brand"


def _art():
    with bot.webhook_app.test_request_context("/login"):
        return bot.login_hero_art()


def _write(name):
    BRAND.mkdir(parents=True, exist_ok=True)
    path = BRAND / name
    path.write_bytes(b"\xff\xd8\xff\xe0not-a-real-image")
    return path


def test_absent_artwork_yields_nothing_to_render():
    """No asset, no rule. The gradient fallback is the design, not a failure."""
    existing = list(BRAND.glob("login-hero*"))
    assert existing == [], f"test assumes no artwork is installed, found {existing}"

    assert _art() == []


def test_the_newest_token_wins_so_a_replacement_is_deliverable():
    """Two dates installed, the later one is served.

    Picking the older one would be invisible locally and permanent in
    production, because the immutable cache means nobody ever re-requests the
    path that would show the mistake.
    """
    older = _write("login-hero-20260101.jpg")
    newer = _write("login-hero-20261231.jpg")
    try:
        urls = [variant["url"] for variant in _art()]
        assert any(url.endswith("login-hero-20261231.jpg") for url in urls), urls
        assert not any(url.endswith("login-hero-20260101.jpg") for url in urls), urls
    finally:
        older.unlink(missing_ok=True)
        newer.unlink(missing_ok=True)


def test_variants_are_offered_best_first_and_absent_ones_are_skipped():
    """AVIF before WebP before JPEG, and no entry for a format not installed.

    Order is the whole value here: this image is the LCP element on the sign-in
    page, and `image-set` takes the first type the browser accepts. Listing
    JPEG first would hand every modern browser the largest file.
    """
    jpg = _write("login-hero-20261231.jpg")
    webp = _write("login-hero-20261231.webp")
    try:
        mimes = [variant["mime"] for variant in _art()]
        assert mimes == ["image/webp", "image/jpeg"], mimes
    finally:
        jpg.unlink(missing_ok=True)
        webp.unlink(missing_ok=True)


def test_the_login_page_emits_no_preload_while_the_artwork_is_absent():
    """End to end, because the context key and the template are two chances to
    get this wrong and only the rendered page shows both."""
    assert list(BRAND.glob("login-hero*")) == []

    bot.webhook_app.config["WTF_CSRF_ENABLED"] = False
    with bot.webhook_app.test_client() as client:
        html = client.get("/login").get_data(as_text=True)

    assert "login-hero" not in html
    assert 'rel="preload" as="image"' not in html
