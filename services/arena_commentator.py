"""AI-style Arena commentary with deterministic safe fallbacks."""

from __future__ import annotations

from . import arena_psychology


def trade_commentary(player_name: str, symbol: str, side: str, quantity: float, price: float, psychology: dict):
    player = player_name or "This pilot"
    symbol = (symbol or "BTC").upper()
    side = (side or "trade").lower()
    discipline = int((psychology or {}).get("discipline_score") or 50)
    fomo = int((psychology or {}).get("fomo_score") or 0)
    notional = float(quantity or 0) * float(price or 0)
    if discipline >= 80:
        return f"{player} placed a controlled simulated {side} on {symbol}. Discipline score improved while keeping risk visible."
    if fomo >= 55:
        return f"{player} entered {symbol} with elevated FOMO pressure. AI recommends smaller sizing and confirmation before the next simulated trade."
    return f"{player} executed a simulated {side} on {symbol} worth about ${notional:,.2f}. {arena_psychology.match_psychology_summary(psychology)}"


def match_recap(match: dict, participants: list, events: list):
    leader = None
    if participants:
        leader = sorted(participants, key=lambda item: item.get("score", 0), reverse=True)[0]
    if leader:
        return (
            f"{leader.get('display_name') or 'The leading pilot'} currently leads this educational battle "
            f"with {int(leader.get('score') or 0)} points. Risk-adjusted discipline matters more than simulated profit."
        )
    if events:
        return "The match is active. AI is watching simulated trades, discipline, and scam-defense decisions."
    return "Match room ready. Waiting for pilots to make the first educational move."


def room_commentary(room_title: str, active_matches: int, top_player: str = ""):
    if active_matches:
        return f"{room_title} is live with {active_matches} active battle updates. Watch discipline and risk control, not just simulated P/L."
    if top_player:
        return f"{top_player} is setting the pace in {room_title}. Challenge smartly and keep trades educational."
    return f"{room_title} is open. Start a mission, ask for a battle, or practice scam defense."
