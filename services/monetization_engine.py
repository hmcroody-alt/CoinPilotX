"""Trust-first monetization architecture for CoinPilotXAI."""

from __future__ import annotations


def revenue_layers():
    return [
        {"layer": "Free Platform", "items": ["PulseSoc Feed", "basic Scam Shield", "basic Arena", "basic Roast Battle", "basic alerts", "public replays"]},
        {"layer": "Pro Subscription", "items": ["advanced alerts", "Auto Signals", "AI market analysis", "advanced Scam Shield", "whale intelligence", "priority Telegram/SMS/push"]},
        {"layer": "Creator Economy", "items": ["creator profiles", "premium rooms foundation", "digital badges", "revenue-share-ready architecture"]},
        {"layer": "Ethical Ads", "items": ["contextual sponsorships", "sponsored educational posts", "trusted partner placements", "no scam token promotions"]},
        {"layer": "Enterprise Intelligence", "items": ["Scam Shield Enterprise", "aggregate scam reports", "market sentiment summaries", "fraud trend intelligence"]},
    ]


def pro_features():
    return [
        "Advanced alerts",
        "Auto Signals",
        "AI assistant",
        "Telegram/SMS priority",
        "Advanced Scam Shield",
        "Whale intelligence",
        "Portfolio AI",
        "Creator tools",
    ]


def admin_summary(conn):
    cur = conn.cursor()
    summary = {}
    for key, query in {
        "pro_users": "SELECT COUNT(*) AS total FROM users WHERE COALESCE(is_pro,0)=1 OR subscription_status IN ('active','trialing')",
        "trial_users": "SELECT COUNT(*) AS total FROM users WHERE subscription_status='trialing'",
        "creator_profiles": "SELECT COUNT(*) AS total FROM creator_profiles",
        "ad_reports": "SELECT COUNT(*) AS total FROM ad_reports WHERE status='open'",
        "enterprise_leads": "SELECT COUNT(*) AS total FROM enterprise_leads",
    }.items():
        try:
            cur.execute(query)
            row = cur.fetchone()
            summary[key] = int((dict(row) if hasattr(row, "keys") else {"total": row[0] if row else 0}).get("total") or 0)
        except Exception:
            summary[key] = 0
    summary["revenue_layers"] = revenue_layers()
    return summary
