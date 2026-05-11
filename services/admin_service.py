"""Admin dashboard data helpers."""

from datetime import datetime, timedelta

from .user_context import connect


def core_metrics():
    conn = connect()
    cur = conn.cursor()
    since_day = (datetime.now() - timedelta(days=1)).isoformat()
    cur.execute("SELECT COUNT(*) FROM users WHERE COALESCE(email,'')!=''")
    users = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM users WHERE COALESCE(email,'')!='' AND created_at>=?", (since_day,))
    new_users_today = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM users WHERE telegram_user_id IS NOT NULL")
    telegram_linked = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM users WHERE lower(COALESCE(plan,''))='pro'")
    pro_users = cur.fetchone()[0]
    conn.close()
    return {
        "users": users,
        "new_users_today": new_users_today,
        "telegram_linked": telegram_linked,
        "pro_users": pro_users,
    }

