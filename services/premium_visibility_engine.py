"""Premium placement and prestige logic for PulseSoc surfaces."""

from __future__ import annotations

from . import premium_capability_engine
from . import premium_identity_engine


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


#: Status words that describe a membership with no end date. A recorded expiry
#: alongside one of these is meaningless, so the clock is not consulted for them.
_UNBOUNDED_STATUS = {"founder", "lifetime"}
#: Status words that describe a membership with a TERM. These are only worth
#: anything while the term is open.
_TERM_STATUS = {"active", "trial"}
_TERM_SUBSCRIPTION_STATUS = {"active", "trialing"}
_PREMIUM_PLANS = {"pulse-premium", "premium", "creator-pro"}


def is_premium_user(user):
    """Row-level premium read for placement/prestige surfaces.

    The status-word branches used to answer from the WORD ALONE, with no clock
    check: a row whose ``premium_status`` was left at 'active' by a provider
    webhook that never arrived — or by any writer of a time-boxed grant — read
    as Premium forever, because nothing here ever looked at the period end.

    Its sibling reader ``premium_identity_engine.has_active_premium`` already
    refuses that ("a status frozen at 'active' by a missed provider webhook must
    not keep premium alive past the recorded period end"), and the two are fed
    the SAME row by the same feed and profile queries. One of them honouring the
    clock and the other not is how a member ends up with the placement of
    Premium and none of the badge, or keeps both after paying for neither.

    Both now share ``row_period_ended`` — one definition of "has the term
    ended", so the two row readers cannot disagree about what time it is.

    Unbounded grants are untouched: ``lifetime_premium`` and
    ``premium_glow_manual_grant`` are deliberate, permanent, manually-issued
    marks, and 'founder'/'lifetime' say in the word itself that there is no
    term to end.
    """
    if not user:
        return False
    if int(user.get("lifetime_premium") or 0) == 1:
        return True
    if int(user.get("premium_glow_manual_grant") or 0) == 1:
        return True
    status = str(user.get("premium_status") or "").lower()
    if status in _UNBOUNDED_STATUS:
        return True
    term_ended = premium_identity_engine.row_period_ended(user)
    if status in _TERM_STATUS:
        return not term_ended
    if (str(user.get("subscription_plan") or "").lower() in _PREMIUM_PLANS
            and str(user.get("subscription_status") or "").lower() in _TERM_SUBSCRIPTION_STATUS):
        return not term_ended
    return False


def contextual_prompt(surface, user=None, is_premium_override=None):
    surface_key = str(surface or "").strip().lower() or "dashboard"
    title, body = SURFACE_PROMPTS.get(surface_key, SURFACE_PROMPTS["dashboard"])
    # R3.3 slice: callers may pass the account-hold-aware *effective* access flag so a
    # suspended owner is not shown "Premium active / enabled across PulseSoc" copy. When
    # the override is None the legacy ownership computation is used unchanged, so every
    # existing caller and the flag-off path stay byte-for-byte identical.
    premium = is_premium_user(user) if is_premium_override is None else bool(is_premium_override)
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


def prompt_html(surface, user=None, is_premium_override=None):
    prompt = contextual_prompt(surface, user, is_premium_override)
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
