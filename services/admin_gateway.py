"""Pre-authentication gateway for the PulseSoc Operations Command Center.

Renders the standalone secure-access login page shown to UNAUTHENTICATED
visitors. This module must never import or emit any part of the authenticated
admin shell: no sidebar, no navigation labels, no status chips, no nav-index
JSON, no admin JavaScript. The page is a closed door, not a hidden room.

Design contract (enforced by tests/admin_auth/test_pre_auth_gateway.py):
  * None of the strings in INTERNAL_NAV_LABELS may appear in the rendered HTML.
  * No /admin/* href other than the login form action itself.
  * No external admin scripts; only inline, login-scoped CSS/JS.
  * prefers-reduced-motion disables all atmospheric animation.
  * Error copy is generic — it never distinguishes unknown email from wrong
    password, and never reveals internal causes.
"""

from __future__ import annotations

from datetime import datetime, timedelta

# Labels of the authenticated Operations Center that must never leak pre-auth.
# Kept in sync with the nav_groups in bot.admin_page_html; the test suite
# asserts absence of every entry in the unauthenticated response body.
INTERNAL_NAV_LABELS = (
    "Global Command",
    "Backend Command Center",
    "Data Recovery",
    "PulseSoc Mod",
    "Ads Review Board",
    "Music Review",
    "Chat Reports",
    "Watch Rules",
    "Scam Shield",
    "Feed Health",
    "PulseSoc Analytics",
    "Payments Command Center",
    "Unmatched Payments",
    "Payment Emails",
    "Notification Delivery",
    "AI Usage",
    "Predictions",
    "PulseSoc Infra",
    "Audit Logs",
    "Command Logs",
    "Visitors",
    "ops-sidebar",
    "ops-status-strip",
    "ops-nav-index",
    "admin_ops_center",
)

# Generic, non-enumerating state copy. "denied" is used for every credential
# failure (unknown email, wrong password, disabled account, locked account) so
# responses cannot be used to probe which accounts exist.
_STATE_COPY = {
    "idle": ("", ""),
    "denied": ("error", "Access denied."),
    "rate_limited": ("error", "Too many attempts. Try again later."),
    "expired": ("notice", "Session ended. Sign in to continue."),
    "unavailable": ("error", "Service temporarily unavailable. Try again shortly."),
}

RATE_LIMIT_WINDOW_MINUTES = 10
RATE_LIMIT_MAX_FAILURES = 10


def login_rate_limited(conn, ip_hash, window_minutes=RATE_LIMIT_WINDOW_MINUTES,
                       max_failures=RATE_LIMIT_MAX_FAILURES):
    """True when this source has too many recent failed admin logins.

    Counts admin_login_failed audit events for the hashed source IP inside the
    sliding window. Reuses the existing admin_audit_logs table so no schema
    change or parallel security store is introduced. ISO-8601 timestamps
    compare correctly as strings.
    """
    if not ip_hash:
        return False
    since = (datetime.now() - timedelta(minutes=window_minutes)).isoformat()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM admin_audit_logs "
            "WHERE action='admin_login_failed' AND ip_hash=? AND created_at>=?",
            (ip_hash, since),
        )
        row = cur.fetchone()
        count = int(row[0] if row else 0)
    except Exception:
        # Fail open on read errors: an unreadable audit table must not lock
        # every legitimate admin out. Account-level lockout still applies.
        return False
    return count >= max_failures


_GATEWAY_CSS = """
:root{--gw-bg0:#04070d;--gw-bg1:#071019;--gw-teal:#2ce8c4;--gw-teal-dim:rgba(44,232,196,.32);
--gw-line:rgba(44,232,196,.08);--gw-text:#e8f4f1;--gw-muted:#7d938f;--gw-red:#ff5f56;
--gw-card:rgba(10,20,28,.72);--gw-border:rgba(120,220,200,.18)}
*{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%}
body.gw{font-family:'SF Pro Display','Segoe UI',system-ui,-apple-system,sans-serif;
background:radial-gradient(1200px 700px at 50% 18%,#0a1826 0%,var(--gw-bg1) 45%,var(--gw-bg0) 100%);
color:var(--gw-text);min-height:100vh;display:flex;flex-direction:column;align-items:center;
justify-content:center;overflow-x:hidden;position:relative}
.gw-grid{position:fixed;inset:0;pointer-events:none;opacity:.5;
background-image:linear-gradient(var(--gw-line) 1px,transparent 1px),
linear-gradient(90deg,var(--gw-line) 1px,transparent 1px);
background-size:56px 56px;
mask-image:radial-gradient(ellipse 90% 75% at 50% 40%,#000 30%,transparent 75%);
-webkit-mask-image:radial-gradient(ellipse 90% 75% at 50% 40%,#000 30%,transparent 75%);
animation:gw-grid-drift 24s linear infinite}
@keyframes gw-grid-drift{from{background-position:0 0,0 0}to{background-position:0 56px,56px 0}}
.gw-ring{position:fixed;left:50%;top:44%;width:640px;height:640px;margin:-320px 0 0 -320px;
border-radius:50%;pointer-events:none;border:1px solid rgba(44,232,196,.07);
box-shadow:0 0 120px rgba(44,232,196,.05) inset;animation:gw-ring-pulse 7s ease-in-out infinite}
.gw-ring.r2{width:880px;height:880px;margin:-440px 0 0 -440px;animation-delay:2.4s}
@keyframes gw-ring-pulse{0%,100%{opacity:.5;transform:scale(1)}50%{opacity:1;transform:scale(1.015)}}
.gw-particle{position:fixed;width:3px;height:3px;border-radius:50%;background:var(--gw-teal);
opacity:.18;pointer-events:none;animation:gw-float 14s ease-in-out infinite}
@keyframes gw-float{0%,100%{transform:translateY(0)}50%{transform:translateY(-46px)}}
.gw-mark{width:64px;height:64px;border-radius:16px;display:flex;align-items:center;justify-content:center;
background:linear-gradient(160deg,rgba(44,232,196,.16),rgba(44,232,196,.04));
border:1px solid var(--gw-border);box-shadow:0 0 34px rgba(44,232,196,.18);margin:0 auto 18px;
position:relative;z-index:2}
.gw-mark svg{display:block}
.gw-title{font-size:2.1rem;font-weight:700;letter-spacing:.02em;text-align:center;z-index:2;position:relative}
.gw-title b{color:var(--gw-teal)}
.gw-sub{color:var(--gw-teal);font-size:.72rem;letter-spacing:.42em;text-transform:uppercase;
text-align:center;margin:.7rem 0 2rem;z-index:2;position:relative}
.gw-card{position:relative;z-index:2;width:min(430px,calc(100vw - 2.5rem));
background:var(--gw-card);border:1px solid var(--gw-border);border-radius:18px;
padding:2.1rem 2rem 1.7rem;backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px);
box-shadow:0 24px 70px rgba(0,0,0,.55),0 0 0 1px rgba(255,255,255,.02) inset;overflow:hidden}
.gw-scan{position:absolute;left:0;right:0;top:0;height:1px;pointer-events:none;
background:linear-gradient(90deg,transparent,var(--gw-teal-dim),transparent);
animation:gw-scan 6s ease-in-out infinite}
@keyframes gw-scan{0%,100%{top:6%;opacity:0}12%{opacity:1}50%{top:94%;opacity:1}62%{opacity:0}}
.gw-card h1{font-size:.8rem;font-weight:600;letter-spacing:.34em;text-transform:uppercase;
color:#b8cdc9;text-align:center;margin-bottom:1.5rem}
.gw-field{position:relative;margin-bottom:1rem}
.gw-field input{width:100%;padding:.95rem 2.9rem .95rem 2.7rem;border-radius:12px;
background:rgba(6,12,18,.85);border:1px solid rgba(120,220,200,.14);color:var(--gw-text);
font-size:.95rem;transition:border-color .18s,box-shadow .18s}
.gw-field input::placeholder{color:#5d6f6c}
.gw-field input:focus{outline:none;border-color:var(--gw-teal-dim);
box-shadow:0 0 0 3px rgba(44,232,196,.10)}
.gw-field .ic{position:absolute;left:1rem;top:50%;transform:translateY(-50%);color:#5d6f6c;
display:flex;pointer-events:none}
.gw-eye{position:absolute;right:.55rem;top:50%;transform:translateY(-50%);background:none;
border:none;color:#5d6f6c;cursor:pointer;padding:.45rem;border-radius:8px;display:flex}
.gw-eye:hover,.gw-eye:focus-visible{color:var(--gw-teal)}
.gw-eye:focus-visible{outline:2px solid var(--gw-teal-dim);outline-offset:1px}
.gw-submit{width:100%;margin-top:.4rem;padding:1rem;border-radius:12px;cursor:pointer;
font-size:.82rem;font-weight:700;letter-spacing:.28em;text-transform:uppercase;color:#052019;
background:linear-gradient(160deg,#37f0cd,#18b898);border:1px solid rgba(44,232,196,.5);
box-shadow:0 6px 26px rgba(44,232,196,.22);transition:filter .18s,transform .12s}
.gw-submit:hover{filter:brightness(1.08)}
.gw-submit:active{transform:translateY(1px)}
.gw-submit:focus-visible{outline:2px solid #fff;outline-offset:2px}
.gw-submit[disabled]{filter:grayscale(.4) brightness(.8);cursor:wait}
.gw-alert{display:flex;align-items:center;gap:.55rem;border-radius:10px;padding:.7rem .9rem;
font-size:.85rem;margin-bottom:1.1rem}
.gw-alert.error{color:#ffb3ad;background:rgba(255,95,86,.08);border:1px solid rgba(255,95,86,.28)}
.gw-alert.notice{color:#bfe9df;background:rgba(44,232,196,.07);border:1px solid rgba(44,232,196,.2)}
.gw-monitor{display:flex;align-items:center;justify-content:center;gap:.45rem;
color:var(--gw-muted);font-size:.78rem;margin-top:1.25rem}
.gw-monitor .dot{width:7px;height:7px;border-radius:50%;background:var(--gw-teal);
box-shadow:0 0 8px var(--gw-teal);animation:gw-beat 2.2s ease-in-out infinite}
@keyframes gw-beat{0%,100%{opacity:.5}50%{opacity:1}}
.gw-footer{position:relative;z-index:2;margin-top:2.2rem;color:#54756e;font-size:.7rem;
letter-spacing:.4em;text-transform:uppercase;display:flex;align-items:center;gap:1rem;text-align:center}
.gw-footer::before,.gw-footer::after{content:"";height:1px;width:44px;
background:linear-gradient(90deg,transparent,rgba(44,232,196,.4));display:block}
.gw-footer::after{background:linear-gradient(90deg,rgba(44,232,196,.4),transparent)}
.gw-skip{position:absolute;left:-9999px;top:0;background:#0a141c;color:var(--gw-text);
padding:.6rem 1rem;border-radius:8px;z-index:9}
.gw-skip:focus{left:1rem;top:1rem}
.visually-hidden{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);
white-space:nowrap}
@media (max-width:560px){.gw-title{font-size:1.7rem}.gw-card{padding:1.6rem 1.25rem 1.35rem}
.gw-ring,.gw-ring.r2{display:none}}
@media (prefers-reduced-motion:reduce){
.gw-grid,.gw-ring,.gw-particle,.gw-scan,.gw-monitor .dot{animation:none!important}
.gw-scan{display:none}}
"""

_LOGO_SVG = (
    "<svg width='34' height='34' viewBox='0 0 34 34' fill='none' aria-hidden='true'>"
    "<path d='M3 17h6l3-8 5 16 4-11 2 3h8' stroke='#2ce8c4' stroke-width='2.2' "
    "stroke-linecap='round' stroke-linejoin='round'/></svg>"
)

_SHIELD_SVG = (
    "<svg width='13' height='13' viewBox='0 0 24 24' fill='none' aria-hidden='true'>"
    "<path d='M12 2l8 4v6c0 5-3.4 8.6-8 10-4.6-1.4-8-5-8-10V6l8-4z' "
    "stroke='currentColor' stroke-width='2' stroke-linejoin='round'/></svg>"
)

_PERSON_SVG = (
    "<svg width='16' height='16' viewBox='0 0 24 24' fill='none' aria-hidden='true'>"
    "<circle cx='12' cy='8' r='4' stroke='currentColor' stroke-width='2'/>"
    "<path d='M4 21c1.5-3.6 4.4-5 8-5s6.5 1.4 8 5' stroke='currentColor' "
    "stroke-width='2' stroke-linecap='round'/></svg>"
)

_LOCK_SVG = (
    "<svg width='16' height='16' viewBox='0 0 24 24' fill='none' aria-hidden='true'>"
    "<rect x='4' y='10' width='16' height='11' rx='2' stroke='currentColor' stroke-width='2'/>"
    "<path d='M8 10V7a4 4 0 0 1 8 0v3' stroke='currentColor' stroke-width='2'/></svg>"
)

_EYE_SVG = (
    "<svg width='18' height='18' viewBox='0 0 24 24' fill='none' aria-hidden='true'>"
    "<path d='M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7z' stroke='currentColor' "
    "stroke-width='2'/><circle cx='12' cy='12' r='3' stroke='currentColor' stroke-width='2'/></svg>"
)

_PARTICLES = "".join(
    f"<span class='gw-particle' style='left:{left}%;top:{top}%;"
    f"animation-delay:{delay}s;animation-duration:{dur}s'></span>"
    for left, top, delay, dur in (
        (8, 24, 0, 13), (16, 68, 2.5, 16), (27, 40, 5, 12), (38, 82, 1.2, 15),
        (61, 76, 3.8, 14), (72, 30, 0.6, 17), (84, 58, 4.4, 13), (92, 22, 2.0, 15),
    )
)


def _shell(title, inner):
    return (
        "<!doctype html><html lang='en'><head>"
        "<meta charset='utf-8'/>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'/>"
        "<meta name='robots' content='noindex,nofollow'/>"
        "<meta name='referrer' content='no-referrer'/>"
        f"<title>{title}</title>"
        "<link rel='stylesheet' href='/static/css/pulsesoc-tokens.css?v=parity-20260806a'/>"
        f"<style>{_GATEWAY_CSS}</style>"
        "</head><body class='gw'>"
        "<a class='gw-skip' href='#gw-form'>Skip to login form</a>"
        "<div class='gw-grid' aria-hidden='true'></div>"
        "<div class='gw-ring' aria-hidden='true'></div>"
        "<div class='gw-ring r2' aria-hidden='true'></div>"
        f"{_PARTICLES}"
        f"{inner}"
        "<p class='gw-footer'>Authorized personnel only</p>"
        "</body></html>"
    )


def render_gateway(csrf_token, state="idle"):
    """Full standalone HTML for the unauthenticated login gateway."""
    kind, copy = _STATE_COPY.get(state, _STATE_COPY["idle"])
    alert = (
        f"<div class='gw-alert {kind}' role='alert'>{_SHIELD_SVG}<span>{copy}</span></div>"
        if copy else ""
    )
    disabled = " disabled" if state == "rate_limited" else ""
    inner = (
        f"<div class='gw-mark'>{_LOGO_SVG}</div>"
        "<p class='gw-title'>Pulse<b>Soc</b></p>"
        "<p class='gw-sub'>Operations Command Center</p>"
        "<section class='gw-card'>"
        "<span class='gw-scan' aria-hidden='true'></span>"
        "<h1>Secure Access Only</h1>"
        f"{alert}"
        "<form id='gw-form' method='post' action='/admin/login' novalidate>"
        f"<input type='hidden' name='csrf_token' value='{csrf_token}'/>"
        "<div class='gw-field'>"
        f"<span class='ic'>{_PERSON_SVG}</span>"
        "<label class='visually-hidden' for='gw-email'>Admin email</label>"
        "<input id='gw-email' name='email' type='email' autocomplete='username' "
        "placeholder='Admin email' required autofocus/>"
        "</div>"
        "<div class='gw-field'>"
        f"<span class='ic'>{_LOCK_SVG}</span>"
        "<label class='visually-hidden' for='gw-password'>Password</label>"
        "<input id='gw-password' name='password' type='password' "
        "autocomplete='current-password' placeholder='Password' required/>"
        "<button class='gw-eye' type='button' id='gw-eye' "
        f"aria-label='Show password' aria-pressed='false'>{_EYE_SVG}</button>"
        "</div>"
        f"<button class='gw-submit' id='gw-submit' type='submit'{disabled}>Access System</button>"
        "</form>"
        "<p class='gw-monitor'><span class='dot' aria-hidden='true'></span>"
        "All access attempts are monitored and logged</p>"
        "</section>"
        "<script>(function(){"
        "var e=document.getElementById('gw-eye'),p=document.getElementById('gw-password');"
        "if(e&&p){e.addEventListener('click',function(){"
        "var show=p.type==='password';p.type=show?'text':'password';"
        "e.setAttribute('aria-pressed',show?'true':'false');"
        "e.setAttribute('aria-label',show?'Hide password':'Show password');});}"
        "var f=document.getElementById('gw-form'),s=document.getElementById('gw-submit');"
        "if(f&&s){f.addEventListener('submit',function(){"
        "s.disabled=true;s.textContent='Authenticating\\u2026';});}"
        "})();</script>"
    )
    return _shell("Secure Access | PulseSoc Operations", inner)


def render_notice(title, body_html):
    """Standalone pre-auth notice page (e.g. owner bootstrap) — same closed-door
    chrome as the login gateway, no admin shell, no navigation."""
    inner = (
        f"<div class='gw-mark'>{_LOGO_SVG}</div>"
        "<p class='gw-title'>Pulse<b>Soc</b></p>"
        "<p class='gw-sub'>Operations Command Center</p>"
        "<section class='gw-card'>"
        f"<h1>{title}</h1>"
        f"<div style='font-size:.9rem;line-height:1.65;color:#c9dcd7'>{body_html}</div>"
        "</section>"
    )
    return _shell(f"{title} | PulseSoc Operations", inner)
