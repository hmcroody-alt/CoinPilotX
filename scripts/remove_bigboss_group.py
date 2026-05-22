#!/usr/bin/env python3
"""One-time hard cleanup for the broken Pulse group named Bigboss."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bot  # noqa: E402
from services import db as db_service  # noqa: E402


TARGET = "bigboss"


def table_exists(cur: sqlite3.Cursor, table: str) -> bool:
    cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1", (table,))
    return cur.fetchone() is not None


def columns(cur: sqlite3.Cursor, table: str) -> set[str]:
    if not table_exists(cur, table):
        return set()
    cur.execute(f"PRAGMA table_info({table})")
    return {str(row["name"]) for row in cur.fetchall()}


def placeholders(values: list[int]) -> str:
    return ",".join(["?"] * len(values))


def delete_where(cur: sqlite3.Cursor, table: str, where: str, params: tuple = ()) -> int:
    if not table_exists(cur, table):
        return 0
    cur.execute(f"DELETE FROM {table} WHERE {where}", params)
    return int(cur.rowcount or 0)


def safe_media_file_for_url(media_url: str) -> Path | None:
    url = str(media_url or "").strip()
    if not url.startswith("/static/uploads/"):
        return None
    path = (ROOT / url.lstrip("/")).resolve()
    uploads_root = (ROOT / "static" / "uploads").resolve()
    try:
        path.relative_to(uploads_root)
    except ValueError:
        return None
    return path


def remove_orphan_media_files(media_urls: set[str]) -> int:
    removed = 0
    for media_url in media_urls:
        path = safe_media_file_for_url(media_url)
        if path and path.exists() and path.is_file():
            path.unlink()
            removed += 1
    return removed


def main() -> int:
    bot.init_db()
    conn = db_service.connect()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute(
        """
        SELECT *
        FROM pulse_groups
        WHERE LOWER(TRIM(COALESCE(name,'')))=?
           OR LOWER(TRIM(COALESCE(slug,'')))=?
        ORDER BY id
        """,
        (TARGET, TARGET),
    )
    groups = [dict(row) for row in cur.fetchall()]
    if not groups:
        print("Bigboss group not found")
        conn.close()
        return 0

    group_ids = [int(group["id"]) for group in groups]
    group_slugs = [str(group.get("slug") or "") for group in groups if group.get("slug")]
    print("Found Bigboss group(s):")
    for group in groups:
        print(f"- id={group.get('id')} slug={group.get('slug')} name={group.get('name')}")

    post_ids: list[int] = []
    if table_exists(cur, "pulse_group_posts"):
        cur.execute(f"SELECT id FROM pulse_group_posts WHERE group_id IN ({placeholders(group_ids)})", group_ids)
        post_ids = [int(row["id"]) for row in cur.fetchall()]

    comment_ids: list[int] = []
    if post_ids and table_exists(cur, "pulse_group_post_comments"):
        cur.execute(f"SELECT id FROM pulse_group_post_comments WHERE group_post_id IN ({placeholders(post_ids)})", post_ids)
        comment_ids = [int(row["id"]) for row in cur.fetchall()]

    conversation_ids: list[int] = []
    if table_exists(cur, "pulse_conversations"):
        conv_cols = columns(cur, "pulse_conversations")
        clauses = []
        params: list[int] = []
        if "group_id" in conv_cols:
            clauses.append(f"group_id IN ({placeholders(group_ids)})")
            params.extend(group_ids)
        if "linked_group_id" in conv_cols:
            clauses.append(f"linked_group_id IN ({placeholders(group_ids)})")
            params.extend(group_ids)
        if clauses:
            cur.execute(f"SELECT id FROM pulse_conversations WHERE {' OR '.join(clauses)}", params)
            conversation_ids = [int(row["id"]) for row in cur.fetchall()]

    media_urls: set[str] = set()
    if post_ids and table_exists(cur, "pulse_group_post_media"):
        media_cols = columns(cur, "pulse_group_post_media")
        select_cols = [col for col in ("media_url", "thumbnail_url") if col in media_cols]
        if select_cols:
            cur.execute(f"SELECT {', '.join(select_cols)} FROM pulse_group_post_media WHERE group_post_id IN ({placeholders(post_ids)})", post_ids)
            for row in cur.fetchall():
                for col in select_cols:
                    if row[col]:
                        media_urls.add(str(row[col]))
    if post_ids and table_exists(cur, "pulse_group_posts"):
        post_cols = columns(cur, "pulse_group_posts")
        select_cols = [col for col in ("media_url", "thumbnail_url") if col in post_cols]
        if select_cols:
            cur.execute(f"SELECT {', '.join(select_cols)} FROM pulse_group_posts WHERE id IN ({placeholders(post_ids)})", post_ids)
            for row in cur.fetchall():
                for col in select_cols:
                    if row[col]:
                        media_urls.add(str(row[col]))

    deleted: dict[str, int] = {}
    try:
        if conversation_ids:
            if table_exists(cur, "pulse_messages"):
                msg_cols = columns(cur, "pulse_messages")
                if "media_url" in msg_cols:
                    cur.execute(f"SELECT media_url, thumbnail_url FROM pulse_messages WHERE conversation_id IN ({placeholders(conversation_ids)})", conversation_ids)
                    for row in cur.fetchall():
                        if row["media_url"]:
                            media_urls.add(str(row["media_url"]))
                        if "thumbnail_url" in row.keys() and row["thumbnail_url"]:
                            media_urls.add(str(row["thumbnail_url"]))
                deleted["pulse_messages"] = delete_where(cur, "pulse_messages", f"conversation_id IN ({placeholders(conversation_ids)})", tuple(conversation_ids))
            deleted["pulse_conversation_participants"] = delete_where(cur, "pulse_conversation_participants", f"conversation_id IN ({placeholders(conversation_ids)})", tuple(conversation_ids))
            deleted["pulse_conversations"] = delete_where(cur, "pulse_conversations", f"id IN ({placeholders(conversation_ids)})", tuple(conversation_ids))

        if post_ids:
            deleted["pulse_group_comment_reports"] = delete_where(cur, "pulse_group_comment_reports", f"group_post_id IN ({placeholders(post_ids)})", tuple(post_ids))
            if comment_ids:
                deleted["pulse_group_comment_reports_by_comment"] = delete_where(cur, "pulse_group_comment_reports", f"comment_id IN ({placeholders(comment_ids)})", tuple(comment_ids))
            deleted["pulse_group_post_reports"] = delete_where(cur, "pulse_group_post_reports", f"group_post_id IN ({placeholders(post_ids)})", tuple(post_ids))
            deleted["pulse_group_post_reactions"] = delete_where(cur, "pulse_group_post_reactions", f"group_post_id IN ({placeholders(post_ids)})", tuple(post_ids))
            deleted["pulse_group_post_comments"] = delete_where(cur, "pulse_group_post_comments", f"group_post_id IN ({placeholders(post_ids)})", tuple(post_ids))
            deleted["pulse_group_post_media"] = delete_where(cur, "pulse_group_post_media", f"group_post_id IN ({placeholders(post_ids)})", tuple(post_ids))
            deleted["pulse_group_posts"] = delete_where(cur, "pulse_group_posts", f"id IN ({placeholders(post_ids)})", tuple(post_ids))

        deleted["pulse_group_reports"] = delete_where(cur, "pulse_group_reports", f"group_id IN ({placeholders(group_ids)})", tuple(group_ids))
        deleted["pulse_group_invites"] = delete_where(cur, "pulse_group_invites", f"group_id IN ({placeholders(group_ids)})", tuple(group_ids))
        deleted["pulse_group_roles"] = delete_where(cur, "pulse_group_roles", f"group_id IN ({placeholders(group_ids)})", tuple(group_ids))
        deleted["pulse_group_members"] = delete_where(cur, "pulse_group_members", f"group_id IN ({placeholders(group_ids)})", tuple(group_ids))
        deleted["pulse_group_action_logs"] = delete_where(cur, "pulse_group_action_logs", f"group_id IN ({placeholders(group_ids)})", tuple(group_ids))

        if table_exists(cur, "pulse_notifications") and group_slugs:
            like_params = [f"/pulse/groups/{slug}%" for slug in group_slugs]
            where = " OR ".join(["target_url LIKE ?"] * len(like_params))
            deleted["pulse_notifications"] = delete_where(cur, "pulse_notifications", where, tuple(like_params))
        if table_exists(cur, "notifications"):
            notification_cols = columns(cur, "notifications")
            filters = []
            params: list[str] = []
            for slug in group_slugs:
                if "message" in notification_cols:
                    filters.append("message LIKE ?")
                    params.append(f"%{slug}%")
                if "metadata" in notification_cols:
                    filters.append("metadata LIKE ?")
                    params.append(f"%{slug}%")
            if filters:
                deleted["notifications"] = delete_where(cur, "notifications", " OR ".join(filters), tuple(params))

        if table_exists(cur, "chat_media_uploads"):
            media_cols = columns(cur, "chat_media_uploads")
            filters = []
            params: list[str] = []
            if "context_type" in media_cols and "context_id" in media_cols:
                for group in groups:
                    filters.append("(context_type='pulse_group' AND context_id=?)")
                    params.append(str(group.get("slug") or ""))
                    filters.append("(context_type='pulse_group' AND context_id=?)")
                    params.append(str(group.get("id") or ""))
            if conversation_ids and "context_type" in media_cols and "context_id" in media_cols:
                for conv_id in conversation_ids:
                    filters.append("(context_type='pulse_message' AND context_id=?)")
                    params.append(str(conv_id))
            if filters:
                cur.execute(f"SELECT media_url, thumbnail_url FROM chat_media_uploads WHERE {' OR '.join(filters)}", params)
                for row in cur.fetchall():
                    if row["media_url"]:
                        media_urls.add(str(row["media_url"]))
                    if row["thumbnail_url"]:
                        media_urls.add(str(row["thumbnail_url"]))
                deleted["chat_media_uploads"] = delete_where(cur, "chat_media_uploads", " OR ".join(filters), tuple(params))

        deleted["pulse_groups"] = delete_where(cur, "pulse_groups", f"id IN ({placeholders(group_ids)})", tuple(group_ids))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    removed_files = remove_orphan_media_files(media_urls)
    for table, count in sorted(deleted.items()):
        if count:
            print(f"deleted {count} row(s) from {table}")
    if removed_files:
        print(f"deleted {removed_files} local media file(s)")
    print("Bigboss group removed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
