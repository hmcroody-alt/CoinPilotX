"""Briefing summarization: governed UNDX pass + deterministic fallback.

UNDX converts a bounded BriefingFacts payload into notification copy. It never
sources data itself. Output is grounding-validated: any number in the copy that
does not appear in the fact payload rejects the LLM output and the deterministic
template is used instead. If UNDX is down, templates always work (Stage 12).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

TITLE_MAX = 50
BODY_MAX = 178

FORBIDDEN_ADVICE = re.compile(
    r"\b(buy|sell|hold|entry|exit|guaranteed|target profit|should invest)\b", re.I
)

SYSTEM_PROMPT = (
    "You write one short push notification for PulseSoc called a Pulse Briefing. "
    "You are given a JSON fact payload. Rules: use ONLY numbers and facts present in the payload; "
    "never invent prices, counts, percentages, or causes; no financial advice (never say buy/sell/hold); "
    "no exaggerated urgency; write in the locale given by the payload's 'locale' field; "
    "keep it concise and useful. "
    'Respond with ONLY a JSON object: {"title": "...", "body": "..."}. '
    f"Title <= {TITLE_MAX} chars. Body <= {BODY_MAX} chars."
)

#: What the *routing decision* is made from, which is not what gets sent.
#:
#: `undx_router.classify_request` reads the text it is given, and what this module
#: sends is `json.dumps(payload)` - so before this constant existed the provider was
#: chosen from serialized **field names**. `marketplace_orders` supplies the token
#: "market" and the crypto block supplies "crypto", which together classify every
#: briefing in this codebase as `research`. The `research` lane leads with Perplexity:
#: a paid web-search provider, selected for a job that must not source data (see the
#: module docstring) and whose output is rejected outright by `grounded()` if it
#: contains a number absent from the payload. The correct lane is `fast_directive`,
#: which leads with Groq - right for 178 characters at `max_tokens=220` under a 10s
#: timeout, sent once per user per cycle.
#:
#: That misroute is currently *masked*, and the masking is the reason to fix it rather
#: than a reason to leave it. CONFIDENTIAL refuses Perplexity and Gemini, so `research`
#: collapses to [openai, claude, meta] and `fast_directive` to [openai, claude] - both
#: lead with OpenAI, so no request is misrouted today. The privacy ceiling is doing the
#: work here, not the routing policy, and the day any caller declares something below
#: CONFIDENTIAL this call starts paying Perplexity to write push notifications.
#:
#: Phrased as a description of the task because this call site has no user question to
#: route on: the work is the same shape on every invocation, so the honest subject is
#: the job rather than the data. Pinned by a test that asserts the *category*, since a
#: sentence that classifies correctly today is not self-evidently doing so after a
#: change to the classifier's rules.
ROUTING_SUBJECT = "summarize a bounded fact payload into notification copy"


def _fmt_pct(value) -> str:
    return f"{value:+.1f}%".replace("+-", "-")


def _numbers_in(text: str) -> set[str]:
    return {m.replace(",", "") for m in re.findall(r"\d[\d,]*\.?\d*", text)}


def _fact_number_pool(facts: dict[str, Any]) -> set[str]:
    pool: set[str] = set()

    def walk(value):
        if isinstance(value, dict):
            for v in value.values():
                walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            f = float(value)
            for rendered in (
                f"{f:.0f}", f"{f:.1f}", f"{f:.2f}", f"{abs(f):.0f}", f"{abs(f):.1f}",
                f"{abs(f):.2f}", f"{abs(f)/1000:.0f}", f"{abs(f)/1_000_000_000:.1f}",
                f"{abs(f)/1_000_000_000_000:.1f}",
            ):
                pool.add(rendered.rstrip("0").rstrip(".") or "0")
                pool.add(rendered)
    walk(facts)
    return pool


#: Structural period references ("1h", "24h", "7d") are legitimate copy even
#: though the raw fact payload never contains them as values.
_SAFE_NUMBERS = {"1", "7", "24"}


def grounded(copy_text: str, facts: dict[str, Any]) -> bool:
    """Every number in the copy must exist in the fact payload."""
    pool = _fact_number_pool(facts) | _SAFE_NUMBERS
    for num in _numbers_in(copy_text):
        candidates = {num, num.rstrip("0").rstrip(".") or "0"}
        if not candidates & pool:
            return False
    return True


# --- Deterministic templates (Stage 12 / Stage 25) -------------------------

_T = {
    "en": {
        "title": "Pulse Briefing",
        "msgs": "{n} unread conversation(s)",
        "followers": "{n} new follower(s)",
        "requests": "{n} friend request(s)",
        "orders": "{n} marketplace update(s)",
        "security": "{n} security alert(s)",
        "btc": "BTC {c} over 24h",
        "eth": "ETH {c}",
        "quiet_net": "Your network is quiet",
        "open": "Open PulseSoc for details.",
    },
    "es": {
        "title": "Resumen Pulse",
        "msgs": "{n} conversación(es) sin leer",
        "followers": "{n} seguidor(es) nuevo(s)",
        "requests": "{n} solicitud(es) de amistad",
        "orders": "{n} novedad(es) del mercado",
        "security": "{n} alerta(s) de seguridad",
        "btc": "BTC {c} en 24h",
        "eth": "ETH {c}",
        "quiet_net": "Tu red está tranquila",
        "open": "Abre PulseSoc para más detalles.",
    },
    "fr": {
        "title": "Brief Pulse",
        "msgs": "{n} conversation(s) non lue(s)",
        "followers": "{n} nouvel(aux) abonné(s)",
        "requests": "{n} demande(s) d'ami",
        "orders": "{n} mise(s) à jour du marché",
        "security": "{n} alerte(s) de sécurité",
        "btc": "BTC {c} sur 24h",
        "eth": "ETH {c}",
        "quiet_net": "Votre réseau est calme",
        "open": "Ouvrez PulseSoc pour les détails.",
    },
    "ht": {
        "title": "Rezime Pulse",
        "msgs": "{n} konvèsasyon poko li",
        "followers": "{n} nouvo moun k ap suiv ou",
        "requests": "{n} demann zanmi",
        "orders": "{n} nouvèl nan makèt la",
        "security": "{n} alèt sekirite",
        "btc": "BTC {c} nan 24è",
        "eth": "ETH {c}",
        "quiet_net": "Rezo ou trankil",
        "open": "Louvri PulseSoc pou plis detay.",
    },
}


def template_copy(facts: dict[str, Any]) -> dict[str, str]:
    """No-LLM fallback: deterministic, localized, grounded by construction."""
    locale = str(facts.get("locale") or "en").split("-")[0]
    t = _T.get(locale, _T["en"])
    network = facts.get("network") or {}
    crypto = facts.get("crypto") or {}
    parts: list[str] = []
    if network.get("security_alerts"):
        parts.append(t["security"].format(n=network["security_alerts"]))
    if network.get("unread_messages"):
        parts.append(t["msgs"].format(n=network["unread_messages"]))
    if network.get("friend_requests"):
        parts.append(t["requests"].format(n=network["friend_requests"]))
    if network.get("new_followers"):
        parts.append(t["followers"].format(n=network["new_followers"]))
    if network.get("marketplace_orders"):
        parts.append(t["orders"].format(n=network["marketplace_orders"]))
    if not parts and network:
        parts.append(t["quiet_net"])
    if crypto.get("available"):
        if crypto.get("btc_change_24h") is not None:
            parts.append(t["btc"].format(c=_fmt_pct(crypto["btc_change_24h"])))
        if crypto.get("eth_change_24h") is not None:
            parts.append(t["eth"].format(c=_fmt_pct(crypto["eth_change_24h"])))
    body = ". ".join(parts[:4])
    body = (body + ". " if body else "") + t["open"]
    return {"title": t["title"][:TITLE_MAX], "body": body[:BODY_MAX], "source": "template"}


def undx_copy(facts: dict[str, Any]) -> dict[str, str] | None:
    """Governed UNDX summarization; None on any failure (caller falls back)."""
    try:
        from undx_router import route_structured_request

        from services import undx_call_domain, undx_privacy
    except Exception:  # noqa: BLE001
        return None
    payload = {k: facts.get(k) for k in ("locale", "network", "crypto", "urgency")}
    try:
        result = route_structured_request(
            facts.get("user_id"), SYSTEM_PROMPT,
            json.dumps(payload, sort_keys=True), timeout=10, max_tokens=220,
            # CONFIDENTIAL (§4). The payload is per-user activity - unread message
            # counts, friend requests, marketplace orders, security alerts - which is
            # personal even though every value is an integer. Not SECRET: no
            # credentials, no message contents, no addresses, and SECRET would refuse
            # every external provider and permanently disable this path in favour of
            # the template. Declared rather than omitted: omission already normalises
            # to CONFIDENTIAL, so this changes no behaviour and makes the
            # classification reviewable, which §4 asks for precisely because a class
            # that defaults correctly is indistinguishable from one nobody chose.
            privacy_class=undx_privacy.SENSITIVITY_CONFIDENTIAL,
            # GENERAL (§5), and load-bearing rather than a placeholder. A briefing
            # aggregates messaging, commerce and security counts into one
            # notification, so no narrower domain is true of it, and naming one would
            # be a claim about content this caller cannot make. The domain may reorder
            # providers and may never widen them, so the honest value costs nothing.
            call_domain=undx_call_domain.CALL_DOMAIN_GENERAL,
            # Route on the job, not on the serialized payload's field names. See
            # ROUTING_SUBJECT above for the measurement.
            classify_text=ROUTING_SUBJECT,
            # Deliberately NOT require_json=True, which is the opposite of the call in
            # `undx_capability_planner`. Two things differ. The parse here is tolerant
            # by construction - `raw.index("{")` lifts the object out of surrounding
            # prose - and Claude, which has no JSON mode in `PROVIDERS`, is half of the
            # reachable chain at CONFIDENTIAL; requiring the capability would refuse it
            # and leave OpenAI alone. And a parse failure here degrades to
            # `template_copy`, which is deterministic, localized and grounded by
            # construction, so the fallback is a correct answer rather than a missing
            # one. In the planner the same failure became `_miss("unparseable")` - a
            # silently dropped capability - which is why it pays a provider for the
            # guarantee and this does not.
        )
        if not result.get("ok"):
            return None
        raw = result["response"].strip()
        raw = raw[raw.index("{"): raw.rindex("}") + 1]
        parsed = json.loads(raw)
        title = str(parsed.get("title") or "").strip()[:TITLE_MAX]
        body = str(parsed.get("body") or "").strip()[:BODY_MAX]
        if not title or not body:
            return None
        combined = f"{title} {body}"
        if FORBIDDEN_ADVICE.search(combined):
            logging.warning("BRIEFING_UNDX_ADVICE_REJECTED user_id=%s", facts.get("user_id"))
            return None
        if not grounded(combined, facts):
            logging.warning("BRIEFING_UNDX_UNGROUNDED_REJECTED user_id=%s", facts.get("user_id"))
            return None
        return {"title": title, "body": body, "source": f"undx:{result.get('provider')}"}
    except Exception:  # noqa: BLE001 - LLM failure must not fail the briefing
        logging.exception("BRIEFING_UNDX_SUMMARY_FAILED user_id=%s", facts.get("user_id"))
        return None


def summarize(facts: dict[str, Any]) -> dict[str, str]:
    """UNDX first, deterministic template always as safety net."""
    return undx_copy(facts) or template_copy(facts)
