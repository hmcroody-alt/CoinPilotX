"""Privacy-safe Arena social sharing helpers."""

from __future__ import annotations

from urllib.parse import quote


def share_text(kind, profile=None, achievement=None):
    profile = profile or {}
    achievement = achievement or {}
    name = profile.get("display_name") or profile.get("public_player_id") or "an Arena pilot"
    if kind == "rank":
        return f"{name} just reached {profile.get('rank', 'a new rank')} in CoinPilotXAI Arena."
    if kind == "boss":
        return f"{name} defeated {achievement.get('boss', 'an AI training boss')} with disciplined decision-making."
    if kind == "scam":
        return f"{name} improved Scam Defense skill in CoinPilotXAI Arena."
    return f"{name} is building crypto intelligence in CoinPilotXAI Arena."


def share_urls(url, text):
    encoded_url = quote(url, safe="")
    encoded_text = quote(text, safe="")
    return {
        "x": f"https://twitter.com/intent/tweet?text={encoded_text}&url={encoded_url}",
        "facebook": f"https://www.facebook.com/sharer/sharer.php?u={encoded_url}",
        "threads": f"https://www.threads.net/intent/post?text={encoded_text}%20{encoded_url}",
        "whatsapp": f"https://wa.me/?text={encoded_text}%20{encoded_url}",
        "telegram": f"https://t.me/share/url?url={encoded_url}&text={encoded_text}",
        "discord": url,
        "copy_link": url,
    }


def share_card(profile=None, achievement=None, base_url="https://pulsesoc.com"):
    profile = profile or {}
    public_id = profile.get("public_player_id") or profile.get("username") or "arena-pilot"
    name = profile.get("display_name") or public_id
    rank = profile.get("rank") or "Rookie"
    arena_iq = int(profile.get("arena_iq") or 50)
    achievement_title = (achievement or {}).get("title") or "Arena Intelligence Progress"
    url = f"{base_url.rstrip('/')}/arena/player/{public_id}"
    text = share_text((achievement or {}).get("type", "profile"), profile, achievement)
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="630" viewBox="0 0 1200 630">
  <defs>
    <linearGradient id="bg" x1="0" x2="1" y1="0" y2="1"><stop stop-color="#050b14"/><stop offset=".55" stop-color="#071b2d"/><stop offset="1" stop-color="#0b1024"/></linearGradient>
    <linearGradient id="edge" x1="0" x2="1"><stop stop-color="#36e58f"/><stop offset=".55" stop-color="#6edff6"/><stop offset="1" stop-color="#ffd166"/></linearGradient>
    <filter id="glow"><feGaussianBlur stdDeviation="14" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
  </defs>
  <rect width="1200" height="630" fill="url(#bg)"/>
  <g opacity=".16"><path d="M0 120H1200M0 240H1200M0 360H1200M0 480H1200M180 0V630M360 0V630M540 0V630M720 0V630M900 0V630M1080 0V630" stroke="#6edff6" stroke-width="1"/></g>
  <circle cx="1010" cy="90" r="190" fill="#6edff6" opacity=".12" filter="url(#glow)"/>
  <circle cx="180" cy="540" r="210" fill="#36e58f" opacity=".11" filter="url(#glow)"/>
  <rect x="70" y="70" width="1060" height="490" rx="34" fill="rgba(8,19,35,.76)" stroke="url(#edge)" stroke-width="3"/>
  <text x="105" y="135" fill="#36e58f" font-family="Inter,Arial,sans-serif" font-size="28" font-weight="800">COINPILOTXAI ARENA</text>
  <text x="105" y="240" fill="#f2fbff" font-family="Inter,Arial,sans-serif" font-size="68" font-weight="900">{_svg_escape(name)}</text>
  <text x="105" y="312" fill="#9fb5c0" font-family="Inter,Arial,sans-serif" font-size="34">{_svg_escape(achievement_title)}</text>
  <text x="105" y="412" fill="#ffd166" font-family="Inter,Arial,sans-serif" font-size="54" font-weight="900">{_svg_escape(rank)}</text>
  <text x="105" y="475" fill="#6edff6" font-family="Inter,Arial,sans-serif" font-size="34" font-weight="800">Arena IQ {arena_iq}</text>
  <text x="105" y="522" fill="#9fb5c0" font-family="Inter,Arial,sans-serif" font-size="24">Educational simulation · no real-money trading · privacy-safe identity</text>
</svg>"""
    return {
        "ok": True,
        "title": "CoinPilotXAI Arena Achievement",
        "description": text,
        "url": url,
        "image_url": f"{base_url.rstrip('/')}/api/arena/share/profile/{public_id}?format=svg",
        "svg": svg,
        "platforms": share_urls(url, text),
        "privacy": "No email, internal user ID, payment data, or private account details are included.",
    }


def _svg_escape(value):
    return str(value or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
