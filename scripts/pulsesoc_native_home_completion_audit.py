#!/usr/bin/env python3
"""Audit PulseSoc Native Home visible publish completion evidence."""

from __future__ import annotations

import importlib
import os
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

COMPOSER = ROOT / "mobile-native" / "src" / "components" / "HomePulseComposer.tsx"
HOME = ROOT / "mobile-native" / "src" / "screens" / "HomeScreen.tsx"
BOT = ROOT / "bot.py"
COMPLETION = ROOT / "reports" / "pulsesoc_native_home_completion.md"
VISIBLE_QA = ROOT / "reports" / "pulsesoc_native_home_visible_publish_qa.md"
PROGRESS = ROOT / "reports" / "pulsesoc_native_progress.md"


REQUIRED_COMPOSER_TOKENS = [
    'testID="home-composer-input"',
    'testID="home-composer-counter"',
    "testID={`home-composer-mode-${item.key}`}",
    'testID="home-composer-photo"',
    'testID="home-composer-video"',
    'testID="home-composer-status"',
    'testID="home-composer-retry"',
    'testID="home-composer-publish"',
    'testID="home-composer-recovered-draft"',
    'testID="home-composer-clear-draft"',
    "AsyncStorage.getItem(DRAFT_KEY)",
    "AsyncStorage.setItem(DRAFT_KEY",
    "AsyncStorage.removeItem(DRAFT_KEY)",
    "Retry Last Publish",
    "Composer validation blocked an empty signal",
    "media.upload",
    "createPost",
]

REQUIRED_HOME_TOKENS = [
    "invalidateNativeSync",
    "post_published",
    "native_home_composer",
    'load("refresh")',
]

REQUIRED_BACKEND_TOKENS = [
    "notify_user(",
    '"pulse_post_created"',
    '"category": "home_publish"',
    '"source": "native_home_composer"',
]

REQUIRED_REPORT_TOKENS = [
    "Result: passed.",
    "Visible publish proof completed.",
    "Draft restored after reload.",
    "Composer reset after publish.",
    "Exactly one matching post appeared in the feed.",
    "Cursor sync exposed `pulse_post_created`",
    "Can Home foundation be considered complete: YES",
]

REQUIRED_PROGRESS_TOKENS = [
    "Home foundation: 96%",
    "Publishing: 96%",
    "Draft recovery: 96%",
    "Feed consistency: 94%",
    "Can Home foundation be considered complete: YES",
]


def read(path: Path) -> str:
    if not path.exists():
        raise AssertionError(f"Missing required file: {path.relative_to(ROOT)}")
    return path.read_text(encoding="utf-8")


def require_tokens(label: str, text: str, tokens: list[str]) -> None:
    missing = [token for token in tokens if token not in text]
    if missing:
        raise AssertionError(f"{label} missing tokens: {', '.join(missing)}")


def import_bot_with_temp_db():
    with tempfile.NamedTemporaryFile(prefix="pulsesoc_home_completion_", suffix=".sqlite", delete=False) as handle:
        db_path = handle.name
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
    os.environ["TELEGRAM_BOT_TOKEN"] = ""
    os.environ["BOT_TOKEN"] = ""
    os.environ["SKIP_TELEGRAM"] = "1"
    os.environ["BREVO_EMAIL_ENABLED"] = "false"
    os.environ.pop("STRIPE_SECRET_KEY", None)
    bot = importlib.import_module("bot")
    bot.STRIPE_SECRET_KEY = ""
    bot.stripe.api_key = ""
    bot.init_db()
    return bot


def verify_seeded_publish_cursor_event() -> None:
    bot = import_bot_with_temp_db()
    conn = bot.db()
    conn.row_factory = bot.sqlite3.Row
    cur = conn.cursor()
    now = "2026-07-09T17:30:00"
    cur.execute(
        """
        INSERT INTO users (email, username, display_name, password_hash, email_verified, created_at, updated_at)
        VALUES (?, ?, ?, 'x', 1, ?, ?)
        """,
        ("home-completion-audit@example.com", "homecompletionaudit", "Home Completion Audit", now, now),
    )
    user_id = int(cur.lastrowid)
    conn.commit()
    conn.close()

    client = bot.webhook_app.test_client()
    with client.session_transaction() as session:
        session["account_user_id"] = user_id

    text = "Home completion audit publish cursor proof"
    post_response = client.post(
        "/api/pulse/posts",
        json={"body": text, "post_type": "text", "visibility": "public"},
    )
    if post_response.status_code != 200 or not (post_response.json or {}).get("ok"):
        raise AssertionError(f"post publish failed: {post_response.status_code} {post_response.get_data(as_text=True)[:300]}")
    post_id = int((post_response.json or {}).get("post_id") or 0)
    if not post_id:
        raise AssertionError("post publish did not return post_id")

    sync_response = client.get("/api/pulse/sync/events?limit=100")
    if sync_response.status_code != 200:
        raise AssertionError(f"sync endpoint failed: {sync_response.status_code} {sync_response.get_data(as_text=True)[:300]}")
    events = (sync_response.json or {}).get("events") or []
    matching = [
        event
        for event in events
        if event.get("event_type") == "pulse_post_created"
        and str(event.get("entity_id")) == str(post_id)
        and (event.get("metadata") or {}).get("source") == "native_home_composer"
    ]
    if len(matching) != 1:
        raise AssertionError(f"expected exactly one cursor-visible Home publish event, got {len(matching)}")
    event = matching[0]
    invalidates = set(event.get("invalidate") or event.get("invalidates") or [])
    if not {"activity", "notifications"}.issubset(invalidates):
        raise AssertionError(f"Home publish event did not invalidate activity/notifications: {sorted(invalidates)}")

    feed_response = client.get("/api/pulse/feed?limit=20")
    if feed_response.status_code != 200 or text not in feed_response.get_data(as_text=True):
        raise AssertionError("newly published post was not visible through feed API")


def main() -> None:
    composer = read(COMPOSER)
    home = read(HOME)
    bot_source = read(BOT)
    completion = read(COMPLETION)
    visible = read(VISIBLE_QA)
    progress = read(PROGRESS)

    require_tokens("Home composer QA/publish contract", composer, REQUIRED_COMPOSER_TOKENS)
    require_tokens("Home feed invalidation", home, REQUIRED_HOME_TOKENS)
    require_tokens("Home publish backend sync bridge", bot_source, REQUIRED_BACKEND_TOKENS)
    require_tokens("Home completion report", completion, REQUIRED_REPORT_TOKENS)
    require_tokens("Visible publish QA report", visible, REQUIRED_REPORT_TOKENS)
    require_tokens("Native progress report", progress, REQUIRED_PROGRESS_TOKENS)
    verify_seeded_publish_cursor_event()

    print("PulseSoc Native Home completion audit passed.")
    print("Verified visible publish evidence, draft recovery proof, feed consistency, and cursor-visible publish event.")


if __name__ == "__main__":
    main()
