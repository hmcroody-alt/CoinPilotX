"""Render /login to a static file so it can be looked at.

Not a test and not wired into anything -- a local preview harness for the
sign-in surface, which is server-rendered behind no dev server.

The sqlite binding happens before `import bot` on purpose: importing bot
connects and runs init_db() at module scope, so a late binding writes ~170
tables into the real dev database.

`federated_sign_in_options()` returns [] until the Apple/Google secrets are
configured, which is correct behaviour and also means the provider buttons are
invisible on any machine that does not have them. The override forces the
configured shape so the layout can be seen; it changes nothing about when the
real page shows them.
"""

import os
import pathlib
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
_db = pathlib.Path(tempfile.mkdtemp()) / "preview.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_db}"
os.environ.setdefault("SECRET_KEY", "login-preview-only")

import bot  # noqa: E402
from services import external_identity  # noqa: E402

bot.init_db()

# Built from the real source of truth rather than hand-written. A hand-written
# `label` of "Continue with Apple" renders as "Continue with Continue with
# Apple", because the template supplies the "Continue with " itself -- a defect
# that exists only in the preview, which is exactly the kind of thing a preview
# must not invent.
PROVIDERS = [
    {"provider": provider,
     "label": external_identity.PROVIDER_LABELS.get(provider, provider.title())}
    for provider in ("apple", "google")
]


def main() -> None:
    bot.federated_sign_in_options = lambda *a, **k: list(PROVIDERS)
    app = bot.webhook_app
    app.config["WTF_CSRF_ENABLED"] = False
    with app.test_client() as client:
        response = client.get("/login")
        html = response.get_data(as_text=True)
    out = ROOT / "login_preview.html"
    out.write_text(html, encoding="utf-8")
    print(f"status={response.status_code} bytes={len(html)} -> {out}")
    for needle in ("Continue with Apple", "Continue with Google", "auth-hero"):
        print(f"  {needle!r}: {'present' if needle in html else 'ABSENT'}")


if __name__ == "__main__":
    main()
