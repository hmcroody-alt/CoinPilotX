"""Premium placement and prestige logic for PulseSoc surfaces."""

from __future__ import annotations

from . import premium_capability_engine


SURFACE_PROMPTS = {
    "dashboard": ("Unlock creator intelligence", "Premium turns your dashboard into a creator command center with identity, analytics, and studio signals."),
    "messenger": ("Premium conversation style", "Elite chat themes, creator glow, and safer creator identity cues are available with Premium."),
    "reels": ("Create with sharper hooks", "Hook AI, Caption Enhancer, Virality Radar, and premium camera polish are ready for serious creators."),
    "spaces": ("Stand out in intelligent communities", "Premium identity helps trusted builders and teachers show stronger context without overriding safety."),
    "live": ("Elevate your livestream room", "Premium adds branded presence, countdown polish, audience intelligence, and moderation assistance."),
    "teachers": ("Premium educator presence", "Show verified expertise, premium teaching identity, and clearer learner trust context."),
    "marketplace": ("Premium seller presentation", "Premium storefront polish and trust context help buyers understand who they are buying from."),
    "profile": ("Preview profile power", "Aura frames, premium themes, creator rank, and trust visibility make your profile feel alive."),
    "creator": ("Creator acceleration ready", "Premium analytics preview, AI rewrites, and studio polish are available from the creator dashboard."),
}


def feature_flags():
    return premium_capability_engine.premium_feature_flags()


def is_premium_user(user):
    if not user:
        return False
    if int(user.get("lifetime_premium") or 0) == 1:
        return True
    if int(user.get("premium_glow_manual_grant") or 0) == 1:
        return True
    if str(user.get("premium_status") or "").lower() in {"active", "founder", "lifetime", "trial"}:
        return True
    if str(user.get("subscription_plan") or "").lower() in {"pulse-premium", "premium", "creator-pro"} and str(user.get("subscription_status") or "").lower() in {"active", "trialing"}:
        return True
    return False


def contextual_prompt(surface, user=None):
    surface_key = str(surface or "").strip().lower() or "dashboard"
    title, body = SURFACE_PROMPTS.get(surface_key, SURFACE_PROMPTS["dashboard"])
    premium = is_premium_user(user)
    return {
        "show": True,
        "surface": surface_key,
        "is_premium": premium,
        "title": "Premium active" if premium else title,
        "body": "Your premium identity and creator intelligence are enabled across PulseSoc." if premium else body,
        "cta_label": "Open Premium" if premium else "Explore Premium",
        "href": "/pulse/premium",
        "tone": "prestige",
    }


def creator_card_context(user=None, surface="profile"):
    premium = is_premium_user(user)
    display_name = (user or {}).get("display_name") or (user or {}).get("username") or "PulseSoc Creator"
    return {
        "display_name": display_name,
        "premium": premium,
        "surface": surface,
        "badge": "Elite Creator" if premium else "PulseSoc Creator",
        "aura_class": "premium-aura-frame" if premium else "",
        "energy_label": "Premium Studio Active" if premium else "Creator energy building",
        "trust_label": "Trust-visible creator" if premium else "Trust-first PulseSoc identity",
        "cta": "Manage Premium" if premium else "Unlock Premium Identity",
        "href": "/pulse/premium",
    }


def prompt_html(surface, user=None):
    prompt = contextual_prompt(surface, user)
    return (
        "<article class='premium-promo-card pulse-contextual-premium'>"
        f"<span class='premium-badge'>{prompt['surface'].title()} Premium</span>"
        f"<h3>{_escape(prompt['title'])}</h3>"
        f"<p>{_escape(prompt['body'])}</p>"
        f"<div class='actions'><a class='button premium' href='{prompt['href']}'>{_escape(prompt['cta_label'])}</a></div>"
        "</article>"
    )


def _escape(value):
    return (
        str(value or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#x27;")
    )
