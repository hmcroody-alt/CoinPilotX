"""The general-purpose assistant answer, and the deterministic read it falls back to.

U2 of `UNDX_PROVIDER_CALLSITE_CENSUS.md`. This module used to post to
`api.openai.com` directly; it now goes through `undx_router`. The census calls it
"one change point worth five" because five call sites reach it, so the interesting
part of this migration is not the transport — that is the same move as U1 — but that
a single function cannot honestly declare one call domain on behalf of five
different callers.

## Why `call_domain` is a parameter here and was a constant in U1

`bot.sports_edge_ai_analysis` has two callers and both are Telegram handlers, so it
can state TELEGRAM as a fact. This function is reached from a website API route, a
Telegram command handler, a menu-action dispatcher carrying its own `channel`, a
message router, and a wrapper with no callers at all. Hardcoding any one of those
would be a claim about the *majority* of callers, which is the kind of label that is
wrong for at least one site from the moment it is written.

So the domain arrives from the caller and defaults to `GENERAL` — the value
`undx_call_domain` chose as its default precisely so that "nobody said" expresses no
preference rather than sounding safe. §5 is what makes this cheap: a domain can
reorder providers and can never widen them, so a caller that forgets to declare loses
an ordering hint and nothing else.

## What the migration changed, beyond the transport

The `OPENAI_API_KEY` gate is gone. It used to short-circuit to an apology naming a
vendor — "The OpenAI key is not configured" — which had two problems. It leaked which
vendor the deployment was expected to hold, and it returned *less* than the failure
path right below it: the apology carried no market data, while the `except` branch
returned `fallback_response`, a complete deterministic read. A deployment holding a
Claude key and no OpenAI key got the worse of the two. Every failure now takes the
`fallback_response` path, so the answer degrades in quality rather than in kind.

`OPENAI_MODEL` is gone with it, as one of the four competing model defaults §25-27
removes. `undx_router.PROVIDERS` is the authority on which model a provider uses.

Deliberately unchanged: the system instruction verbatim, the six required sections,
temperature 0.35, the 850/320 Pro/free token split, the 20s timeout, the
two-condition disclosure check, and the guarantee that this function returns a
non-empty string and never raises.

## Why `assistant_response_envelope` exists

`assistant_response` still returns a bare string, because five callers treat it as
one and changing that return type is the sort of blast radius §14 warns about. But
two of those callers derived a user-visible provider attribution from
`OPENAI_API_KEY`, and routing is what makes that reading false — it would print
"OpenAI" when Gemini answered, or "fallback" when Claude answered perfectly well.
Leaving a claim that my own change falsified is worse than widening the interface, so
the envelope form is available for callers that publish a source, and
`assistant_response` is a thin wrapper over it.
"""

import logging

import undx_router

from . import market_data
from . import undx_call_domain, undx_privacy

DISCLAIMER = "Informational only — not financial advice."

#: Appended when the model's answer does not already disclaim.
#:
#: Hoisted so a test can assert it survived, for the same reason as
#: `bot.SPORTS_EDGE_SYSTEM_PROMPT`: an inline literal duplicated across a branch and a
#: failure path is one reformat away from drifting between them.
REQUIRED_DISCLOSURE = (
    "Educational information only. Not financial, betting, investment, or legal advice."
)

#: The safety posture, not a style guide.
#:
#: "Never guarantee profits" and the refusal to ask for seed phrases are the two lines
#: that make this safe to point at a crypto audience, and they are preserved verbatim
#: from the pre-migration payload.
ASSISTANT_SYSTEM_PROMPT = (
    "You are PulseSoc, operated by CoinPlotXAI Inc. Give honest crypto, wallet, scam, market, sports, and portfolio education. "
    "Never guarantee profits, betting wins, certainty, or insider information. Never ask for seed phrases, private keys, recovery phrases, wallet passwords, or signing credentials. "
    "Use sections: Market Snapshot, Momentum Read, Risk Level, What to Watch, Safer Next Step, Disclaimer. "
    "If live data is unavailable, say so clearly."
)

#: What a caller may publish as the source when no provider answered.
#:
#: Names this platform rather than a vendor, because the whole point of the fallback is
#: that no vendor was involved.
FALLBACK_SOURCE_LABEL = "CoinPlotXAI deterministic market read"


def fallback_response(question, pro=False):
    snapshot = market_data.live_market_board(limit=8)
    summary = snapshot.get("summary", {})
    live_note = snapshot.get("warning") or "Live market data is available from public sources."
    depth = "Pro view includes deeper risk context and what could change the signal." if pro else "Free view gives a shorter safety-first summary."
    return (
        "💬 PulseSoc AI Assistant\n\n"
        f"Market Snapshot:\nBTC: {summary.get('btc_price') or 'unavailable'} · ETH: {summary.get('eth_price') or 'unavailable'}\n"
        f"Momentum Read:\nMarket trend appears {summary.get('market_trend', 'mixed')} based on available live data.\n\n"
        f"Risk Level:\n{summary.get('risk_level', 'Medium')}\n\n"
        f"What to Watch:\n{live_note}\n\n"
        f"Safer Next Step:\nAsk one specific question, verify live data, and avoid decisions based on urgency. {depth}\n\n"
        f"Question received: {question[:500]}\n\n{DISCLAIMER}"
    )


def assistant_response_envelope(user_id, question, pro=False, call_domain=None):
    """Answer, plus who answered — for callers that publish a source.

    Always returns a dict with a non-empty ``text``. ``routed`` is the only honest
    way for a caller to distinguish a model answer from the deterministic read, and
    ``provider``/``source`` are `None`/`FALLBACK_SOURCE_LABEL` exactly when it is
    `False`.

    CONFIDENTIAL (§4). ``question`` is free text the user wrote, which is the floor
    for this whole family of call sites and admits three providers rather than seven.
    `user_id` goes to the router for budgeting and attribution and is never
    interpolated into the prompt — that is asserted in
    `tests/test_assistant_response_routing.py`, not merely intended, because the
    classification above is only honest while it stays true.
    """
    snapshot = market_data.live_market_board(limit=10)
    try:
        envelope = undx_router.route_structured_request(
            user_id,
            ASSISTANT_SYSTEM_PROMPT,
            f"Live context: {snapshot}\n\nQuestion: {question}",
            timeout=20,
            temperature=0.35,
            max_tokens=850 if pro else 320,
            privacy_class=undx_privacy.SENSITIVITY_CONFIDENTIAL,
            call_domain=call_domain,
            # Route on the question, not on the market board sitting in front of it. The
            # snapshot measures ~8,850 characters against the classifier's 2,600-character
            # window, so before this argument existed the user's sentence was never
            # classified at all and every request here came out `current_web`. The cost was
            # one category rather than all of them: CONFIDENTIAL refuses four providers,
            # which collapses `current_web`, `repository` and `research` onto the same
            # reachable chain — but `security` puts Claude first, so "is this wallet address
            # a scam" was answered by OpenAI. Measured against live CoinGecko data, not
            # inferred from reading the classifier.
            classify_text=question,
        )
    except Exception as exc:
        # The router returns a typed miss rather than raising. If it raises anyway that
        # is a router bug, and it is still not a reason to hand a user an exception
        # where a deterministic market read would do.
        logging.warning("Assistant routing transport failed: %s", exc)
        envelope = {"ok": False, "error": str(exc)[:300], "attempts": []}

    text = str(envelope.get("response") or "").strip() if envelope.get("ok") else ""
    if not text:
        logging.info("Assistant response unavailable, falling back: %s attempts=%s",
                     envelope.get("error"), envelope.get("attempts"))
        return {
            "text": fallback_response(question, pro),
            "routed": False,
            "provider": None,
            "source": FALLBACK_SOURCE_LABEL,
            "call_domain": undx_call_domain.normalise(call_domain),
        }

    # Two conditions, preserved as written. A model that says "this is not financial
    # advice" and a model that says "not investment or financial advice" both satisfy
    # the requirement, and collapsing these into one check would append a duplicate
    # disclosure to one of them.
    if "not financial" not in text.lower() and "financial advice" not in text.lower():
        text += f"\n\n{REQUIRED_DISCLOSURE}"
    return {
        "text": text,
        "routed": True,
        "provider": envelope.get("provider"),
        "source": envelope.get("source") or envelope.get("provider"),
        "call_domain": envelope.get("call_domain"),
    }


def assistant_response(user_id, question, pro=False, call_domain=None):
    """The answer as a string, which is what four of the five call sites want.

    Signature-compatible with the pre-migration function apart from the new optional
    domain, so a caller that does not care about attribution needs no change.
    """
    return assistant_response_envelope(user_id, question, pro=pro,
                                       call_domain=call_domain)["text"]


def intelligence_feed():
    board = market_data.live_market_board(limit=12)
    summary = board.get("summary", {})
    avg = summary.get("average_change_24h")
    signal = 50
    if avg is not None:
        signal = max(1, min(99, int(55 + float(avg) * 8)))
    risk = 55 if summary.get("risk_level") == "Elevated" else 38
    if avg is not None and abs(float(avg)) > 3:
        risk = 72
    action = "WATCH CLOSELY" if risk >= 70 else "WAIT" if signal < 58 else "HOLD"
    return {
        "signal": signal,
        "risk": risk,
        "action": action,
        "btc_price": summary.get("btc_price"),
        "eth_price": summary.get("eth_price"),
        "market_state": summary.get("market_trend", "mixed"),
        "confidence": 70 if board.get("markets") else 35,
        "updated_at": board.get("updated_at"),
        "source": board.get("source"),
        "warning": board.get("warning"),
        "educational": DISCLAIMER,
    }
