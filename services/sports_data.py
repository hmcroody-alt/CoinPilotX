import time
import requests

CACHE = {"data": None, "created_at": 0}
CACHE_SECONDS = 60
SAFETY = "Informational only — not betting or financial advice. Never risk money you cannot afford to lose."

LEAGUES = {
    "nba": ("basketball", "nba", "NBA"),
    "nfl": ("football", "nfl", "NFL"),
    "mlb": ("baseball", "mlb", "MLB"),
    "nhl": ("hockey", "nhl", "NHL"),
    "epl": ("soccer", "eng.1", "EPL"),
}


def fetch_espn(league_key):
    sport, league, label = LEAGUES[league_key]
    url = f"https://site.api.espn.com/apis/site/v2/sports/{sport}/{league}/scoreboard"
    response = requests.get(url, timeout=10)
    response.raise_for_status()
    games = []
    for event in response.json().get("events", []):
        comp = (event.get("competitions") or [{}])[0]
        competitors = comp.get("competitors", [])
        home = next((c for c in competitors if c.get("homeAway") == "home"), competitors[0] if competitors else {})
        away = next((c for c in competitors if c.get("homeAway") == "away"), competitors[1] if len(competitors) > 1 else {})
        home_team = home.get("team", {})
        away_team = away.get("team", {})
        event_id = str(event.get("id") or "")
        games.append({
            "id": f"{league_key}:{event_id}",
            "callback_id": f"sportsedge_{league_key}_{event_id}",
            "event_id": event_id,
            "league": league_key,
            "league_label": label,
            "sport": sport,
            "home_team": home_team.get("displayName") or home_team.get("name") or "Home",
            "away_team": away_team.get("displayName") or away_team.get("name") or "Away",
            "home_abbr": home_team.get("abbreviation") or "HOME",
            "away_abbr": away_team.get("abbreviation") or "AWAY",
            "home_score": int(home.get("score") or 0),
            "away_score": int(away.get("score") or 0),
            "status": event.get("status", {}).get("type", {}).get("description", "Scheduled"),
            "state": event.get("status", {}).get("type", {}).get("state", "pre"),
            "is_live": event.get("status", {}).get("type", {}).get("state", "pre") == "in",
            "is_final": event.get("status", {}).get("type", {}).get("state", "pre") == "post",
            "start_time": event.get("date"),
            "odds_available": False,
            "odds_status": "unavailable",
            "odds_note": "Odds unavailable in current feed.",
            "data_quality": "espn public scoreboard",
        })
    return games


def risk_label(game):
    state = game.get("state")
    margin = abs(int(game.get("home_score") or 0) - int(game.get("away_score") or 0))
    if state == "post":
        return "Low"
    if state == "pre":
        return "Medium" if game.get("odds_available") else "Elevated"
    if game.get("sport") in {"soccer", "hockey", "tennis"} and margin <= 1:
        return "High"
    if margin <= 3:
        return "High"
    if margin >= 14 and game.get("sport") in {"basketball", "football"}:
        return "Elevated"
    return "Elevated"


def odds_text(game):
    if not game.get("odds_available"):
        return "Odds unavailable in current feed. Pulse will not invent market pricing."
    return game.get("odds_note") or "Odds are available from the connected feed."


def game_intelligence(game):
    if not game:
        return {
            "action": "UNAVAILABLE",
            "risk_label": "Unknown",
            "response": "That game is no longer available in the live feed.",
            "disclaimer": SAFETY,
        }
    home_score = int(game.get("home_score") or 0)
    away_score = int(game.get("away_score") or 0)
    margin = home_score - away_score
    abs_margin = abs(margin)
    leading = game.get("home_team") if margin > 0 else game.get("away_team") if margin < 0 else "Neither side"
    trailing = game.get("away_team") if margin > 0 else game.get("home_team") if margin < 0 else "Neither side"
    state = game.get("state")
    sport = game.get("sport") or game.get("league")
    risk = risk_label(game)
    score_line = (
        f"{game.get('away_team')} {away_score} - {home_score} {game.get('home_team')}"
        if state != "pre"
        else f"{game.get('away_team')} at {game.get('home_team')}"
    )
    sport_notes = {
        "basketball": {
            "momentum": "Basketball momentum can swing through pace, scoring runs, foul trouble, and three-point variance.",
            "risk": ["Pace can inflate live totals quickly.", "A short scoring run can erase a mid-size lead.", "Public feeds may miss player availability."],
            "change": "confirmed injuries, foul trouble, pace shift, or a major odds move",
        },
        "football": {
            "momentum": "Football position quality depends on down-distance pressure, turnovers, clock, field position, and red-zone efficiency.",
            "risk": ["One turnover can swing probability sharply.", "Clock state matters more late in halves.", "Weather and injury context may be missing."],
            "change": "turnover margin, quarterback injury news, weather, or a late clock-management shift",
        },
        "baseball": {
            "momentum": "Baseball is lower scoring, so inning, bullpen quality, pitcher fatigue, and base-runner pressure matter more than a simple score gap.",
            "risk": ["Bullpen changes can flip the script.", "Low-scoring volatility makes small edges fragile.", "Pitcher and lineup context may be missing."],
            "change": "starter exit, bullpen mismatch, late-inning base traffic, or lineup news",
        },
        "hockey": {
            "momentum": "Hockey can turn on shot pressure, goalie performance, penalties, and empty-net game state.",
            "risk": ["Penalty swings can change pressure fast.", "Goalie variance is high.", "Shot pressure may be unavailable from the feed."],
            "change": "power-play pressure, goalie change, shot imbalance, or late empty-net situation",
        },
        "soccer": {
            "momentum": "Soccer is low scoring, so time remaining, draw risk, red-card risk, and set-piece pressure matter.",
            "risk": ["Draw risk can dominate close matches.", "One red card can change the position.", "Live xG and card context may be missing."],
            "change": "red card, tactical substitution, late pressure, or confirmed injury",
        },
    }
    note = sport_notes.get(sport, {
        "momentum": "Momentum should be judged through score, time, market price, and sport-specific context.",
        "risk": ["Live markets can overreact.", "Public feeds may miss lineup or injury context.", "Price matters as much as the prediction."],
        "change": "new lineup, injury, odds, or momentum information",
    })
    if state == "post":
        action = "REVIEW ONLY"
        considerations = ["Use the result to compare your pre-game read against what actually happened."]
        avoid = "Avoid forcing a follow-up position just because the final score creates emotion."
    elif state == "pre":
        action = "WAIT FOR CONFIRMATION"
        considerations = ["Consider a position only if price, research, and risk limit all agree.", "Pre-game reads improve when odds, lineups, and availability are confirmed."]
        avoid = "Avoid forcing a position before market price and missing context are clear."
    elif risk == "High":
        action = "HIGH VOLATILITY"
        considerations = [f"{leading} has the current edge, but the game state is fragile.", "If price does not compensate for uncertainty, waiting is cleaner."]
        avoid = "Avoid chasing a live move when one play, possession, inning, goal, or break can flip the view."
    elif abs_margin >= 10:
        action = "WATCH CLOSELY"
        considerations = [f"{leading} controls the scoreboard, which can support a cautious momentum lean.", f"Also check whether {trailing} has comeback paths before trusting the score."]
        avoid = "Avoid paying an inflated live price after the obvious move already happened."
    else:
        action = "REVIEW RISK"
        considerations = ["The current edge is measurable but not decisive.", "Position quality depends on whether market price still leaves room for error."]
        avoid = "Avoid treating a small scoreboard edge as a full prediction."
    matchup = f"{game.get('league_label')} matchup: {score_line}. Status: {game.get('status', 'Scheduled')}."
    current_state = (
        "The game is final, so the best use is post-game review."
        if state == "post"
        else "The game has not started yet; lineup, injury, weather, and price confirmation matter."
        if state == "pre"
        else f"Live scoreboard edge: {leading} leads by {abs_margin}. Momentum can change quickly from here."
    )
    market_context = odds_text(game)
    response = (
        "🎲 Sports Edge Intelligence\n\n"
        f"Matchup Summary:\n{matchup}\n\n"
        f"Current Game State:\n{current_state}\n\n"
        f"Market / Odds Context:\n{market_context}\n\n"
        f"Momentum Read:\n{note['momentum']}\n\n"
        f"Risk Level: {risk}\n\n"
        "Position Considerations:\n" + "\n".join(f"• {item}" for item in considerations) + "\n\n"
        f"Why to Avoid Forcing a Position:\n{avoid}\n\n"
        f"What Could Change the View:\nThe view could change with {note['change']}.\n\n"
        f"Final Caution:\n{SAFETY}"
    )
    return {
        "action": action,
        "risk_label": risk,
        "risk_level": f"{risk}: {', '.join(note['risk'][:2])}",
        "matchup_summary": matchup,
        "current_game_state": current_state,
        "market_odds_context": market_context,
        "momentum_read": note["momentum"],
        "risk_factors": note["risk"],
        "position_considerations": considerations,
        "why_avoid": avoid,
        "what_could_change": f"The view could change with {note['change']}.",
        "final_caution": SAFETY,
        "market_context": f"{market_context} {game.get('data_quality', 'Public data only')}.",
        "disclaimer": SAFETY,
        "response": response,
    }


def live_sports_edge(league="all", limit=30):
    now = time.time()
    if CACHE["data"] and now - CACHE["created_at"] < CACHE_SECONDS:
        data = dict(CACHE["data"])
        games = data["games"]
        if league not in {"", "all"}:
            games = [g for g in games if g.get("league") == league]
        data["games"] = games[:limit]
        return data
    games = []
    source = []
    for key in LEAGUES:
        try:
            games.extend(fetch_espn(key))
            source.append("espn_public_scoreboard")
        except Exception:
            continue
    for game in games:
        game["risk_label"] = risk_label(game)
    warning = None if games else "Live data source is not connected yet."
    data = {
        "source": " + ".join(sorted(set(source))) if source else "unavailable",
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "warning": warning,
        "odds_status": "unavailable",
        "games": games[:limit],
        "disclaimer": SAFETY,
    }
    CACHE["data"] = dict(data, games=games)
    CACHE["created_at"] = now
    if league not in {"", "all"}:
        data["games"] = [g for g in games if g.get("league") == league][:limit]
    return data


def game_analysis(game):
    return game_intelligence(game)
