"""Pre-authentication gateway for the PulseSoc Operations Command Center.

Renders the standalone secure-access login page shown to UNAUTHENTICATED
visitors. This module must never import or emit any part of the authenticated
admin shell: no sidebar, no navigation labels, no status chips, no nav-index
JSON, no admin JavaScript. The page is a closed door, not a hidden room.

Design contract (enforced by tests/admin_auth/test_pre_auth_gateway.py and
tests/admin_auth/test_gateway_contract.py):
  * None of the strings in INTERNAL_NAV_LABELS may appear in the rendered HTML.
  * No /admin/* href other than the login form action itself.
  * No external admin scripts; only inline, login-scoped CSS/JS. The only
    external reference is a public brand asset under /static/brand/.
  * The cinematic environment is pure LOCAL PRESENTATION STATE — it never
    polls operational APIs and never displays real telemetry.
  * prefers-reduced-motion collapses all atmospheric animation.
  * Error copy is generic — it never distinguishes unknown email from wrong
    password, and never reveals internal causes.
  * No military/government-grade security claims; only capabilities that
    actually exist server-side (encrypted transport in production, access
    logging via admin_audit_logs, login throttling, account lockout).
"""

from __future__ import annotations

import hashlib
from collections import deque
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

# ---------------------------------------------------------------------------
# Login throttling
#
# Two bounded dimensions, both hashed — no raw identifiers enter telemetry:
#   * source (hashed client IP)  — caps total attempts from one origin
#   * identifier (hashed normalized login email) — caps attempts against one
#     account across distributed sources
# Counts come from admin_audit_logs (shared across workers). If that table is
# unreadable, a per-process in-memory sliding window (fed by note_failure)
# takes over: brute force stays bounded during the outage, and because the
# window expires, a broken audit store can never permanently lock the owner
# out. When the database recovers, the shared view resumes automatically.
# ---------------------------------------------------------------------------

RATE_LIMIT_WINDOW_MINUTES = 10
RATE_LIMIT_MAX_FAILURES = 10             # per hashed source IP
RATE_LIMIT_MAX_IDENTIFIER_FAILURES = 6   # per hashed login identifier

# Audit actions that count as failed attempts for the source dimension. A
# CSRF-rejected login is still a login attempt and must be metered.
FAILED_LOGIN_ACTIONS = ("admin_login_failed", "admin_login_csrf_rejected")
# Audit action carrying the hashed-identifier dimension (target_id = hash).
IDENTIFIER_FAILED_ACTION = "admin_login_identifier_failed"

_MEMORY_WINDOW = {}
_MEMORY_MAX_KEYS = 4096


def hash_identifier(value, salt=""):
    """Stable salted hash of a normalized login identifier.

    Security telemetry never stores or compares the raw identifier.
    """
    normalized = (value or "").strip().lower()
    if not normalized:
        return ""
    return hashlib.sha256(f"gw-id:{salt}:{normalized}".encode("utf-8")).hexdigest()


def note_failure(ip_hash="", identifier_hash="", now=None):
    """Record a failed attempt in the in-process fallback window.

    Called on every failed/rejected login in addition to the audit log so the
    limiter still has recent data if the audit table becomes unreadable.
    """
    now = now or datetime.now()
    for dim, key in (("src", ip_hash), ("id", identifier_hash)):
        if not key:
            continue
        bucket = _MEMORY_WINDOW.setdefault((dim, key), deque(maxlen=64))
        bucket.append(now)
    if len(_MEMORY_WINDOW) > _MEMORY_MAX_KEYS:
        cutoff = now - timedelta(minutes=RATE_LIMIT_WINDOW_MINUTES)
        for stale_key in list(_MEMORY_WINDOW):
            bucket = _MEMORY_WINDOW[stale_key]
            if not bucket or bucket[-1] < cutoff:
                _MEMORY_WINDOW.pop(stale_key, None)


def _memory_count(dim, key, cutoff):
    bucket = _MEMORY_WINDOW.get((dim, key))
    if not bucket:
        return 0
    return sum(1 for stamp in bucket if stamp >= cutoff)


def login_rate_limited(conn, ip_hash, identifier_hash="",
                       window_minutes=RATE_LIMIT_WINDOW_MINUTES,
                       max_failures=RATE_LIMIT_MAX_FAILURES,
                       max_identifier_failures=RATE_LIMIT_MAX_IDENTIFIER_FAILURES):
    """True when this source OR this login identifier has too many recent
    failed admin logins.

    Reuses the existing admin_audit_logs table so no schema change or parallel
    security store is introduced. ISO-8601 timestamps compare correctly as
    strings. On audit-store read errors, falls back to the per-process memory
    window (see module comment) instead of failing fully open.
    """
    now = datetime.now()
    since = (now - timedelta(minutes=window_minutes)).isoformat()
    cutoff = now - timedelta(minutes=window_minutes)

    src_limited = False
    if ip_hash:
        try:
            cur = conn.cursor()
            placeholders = ",".join("?" for _ in FAILED_LOGIN_ACTIONS)
            cur.execute(
                "SELECT COUNT(*) FROM admin_audit_logs "
                f"WHERE action IN ({placeholders}) AND ip_hash=? AND created_at>=?",
                (*FAILED_LOGIN_ACTIONS, ip_hash, since),
            )
            row = cur.fetchone()
            src_count = int(row[0] if row else 0)
        except Exception:
            src_count = _memory_count("src", ip_hash, cutoff)
        src_limited = src_count >= max_failures

    id_limited = False
    if identifier_hash:
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(*) FROM admin_audit_logs "
                "WHERE action=? AND target_id=? AND created_at>=?",
                (IDENTIFIER_FAILED_ACTION, identifier_hash, since),
            )
            row = cur.fetchone()
            id_count = int(row[0] if row else 0)
        except Exception:
            id_count = _memory_count("id", identifier_hash, cutoff)
        id_limited = id_count >= max_identifier_failures

    return src_limited or id_limited


# ---------------------------------------------------------------------------
# Visual system
#
# Layered, GPU-friendly (transform/opacity only) cinematic environment:
#   A  deep atmosphere + grid drift   E  security scan sweeps
#   B  network arcs behind the mark   F  rare red threat glints / threat wash
#   C  guardian silhouettes           G  stable glass login panel
#   D  central pulse core
# All decorative. No operational data, real or fake, is displayed.
# ---------------------------------------------------------------------------

GATEWAY_MARK_SRC = "/static/brand/pulsesoc-gateway-mark-20260810.png"

_GATEWAY_CSS = """
:root{--gw-bg0:#02050b;--gw-bg1:#061018;--gw-teal:#2ce8c4;--gw-green:#5ff05f;
--gw-teal-dim:rgba(44,232,196,.32);--gw-line:rgba(44,232,196,.07);
--gw-text:#e8f4f1;--gw-muted:#7d938f;--gw-red:#ff4d42;--gw-red-dim:rgba(255,77,66,.5);
--gw-card:rgba(8,17,24,.74);--gw-border:rgba(120,220,200,.18)}
*{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%}
body.gw{font-family:'SF Pro Display','Segoe UI',system-ui,-apple-system,sans-serif;
background:radial-gradient(1300px 760px at 50% 16%,#0a1a28 0%,var(--gw-bg1) 46%,var(--gw-bg0) 100%);
color:var(--gw-text);min-height:100vh;display:flex;flex-direction:column;align-items:center;
justify-content:center;overflow-x:hidden;position:relative;padding:2.2rem 1rem}
/* A — deep atmosphere */
.gw-atmo{position:fixed;inset:-20%;pointer-events:none;opacity:.55;will-change:transform;
background:radial-gradient(46% 34% at 24% 26%,rgba(30,90,110,.10),transparent 70%),
radial-gradient(40% 30% at 78% 20%,rgba(24,70,120,.08),transparent 70%),
radial-gradient(52% 40% at 50% 88%,rgba(20,60,70,.09),transparent 72%);
animation:gw-atmo 52s ease-in-out infinite alternate}
@keyframes gw-atmo{from{transform:translate3d(-1.5%,-1%,0) scale(1)}to{transform:translate3d(1.5%,1.2%,0) scale(1.04)}}
.gw-gridwrap{position:fixed;inset:0;pointer-events:none;overflow:hidden;
mask-image:radial-gradient(ellipse 92% 78% at 50% 42%,#000 28%,transparent 76%);
-webkit-mask-image:radial-gradient(ellipse 92% 78% at 50% 42%,#000 28%,transparent 76%)}
.gw-grid{position:absolute;inset:-56px;opacity:.5;will-change:transform;
background-image:linear-gradient(var(--gw-line) 1px,transparent 1px),
linear-gradient(90deg,var(--gw-line) 1px,transparent 1px);background-size:56px 56px;
animation:gw-grid-drift 30s linear infinite}
@keyframes gw-grid-drift{from{transform:translate3d(0,0,0)}to{transform:translate3d(56px,56px,0)}}
.gw-floor{position:fixed;left:0;right:0;bottom:0;height:30vh;pointer-events:none;opacity:.8;
background:radial-gradient(48% 90% at 50% 100%,rgba(34,190,150,.10),transparent 70%),
radial-gradient(26% 70% at 12% 100%,rgba(200,40,30,.05),transparent 75%),
radial-gradient(26% 70% at 88% 100%,rgba(200,40,30,.05),transparent 75%)}
/* B — network arcs behind the mark */
.gw-net{position:absolute;top:-96px;left:50%;width:380px;height:380px;margin-left:-190px;
pointer-events:none;opacity:.6}
.gw-net .orbit{transform-origin:190px 190px;animation:gw-orbit 70s linear infinite;will-change:transform}
.gw-net .orbit.o2{animation-direction:reverse;animation-duration:110s}
@keyframes gw-orbit{from{transform:rotate(0)}to{transform:rotate(360deg)}}
.gw-net .node{animation:gw-node 6s ease-in-out infinite}
.gw-net .node.n2{animation-delay:2s}.gw-net .node.n3{animation-delay:3.4s}.gw-net .node.n4{animation-delay:4.8s}
@keyframes gw-node{0%,72%,100%{opacity:.15}80%,88%{opacity:.9}}
/* C — guardians */
.gw-guardian{position:fixed;bottom:-4vh;width:30vw;max-width:430px;min-width:260px;
pointer-events:none;opacity:.5;filter:blur(1.5px);will-change:transform,opacity;
animation:gw-breathe 17s ease-in-out infinite}
.gw-guardian svg{display:block;width:100%;height:auto}
.gw-guardian.gl{left:-4vw}.gw-guardian.gr{right:-4vw}
.gw-guardian.gr{animation-name:gw-breathe-r;animation-delay:5s}
@keyframes gw-breathe{0%,100%{opacity:.42;transform:translateY(0)}50%{opacity:.58;transform:translateY(-7px)}}
@keyframes gw-breathe-r{0%,100%{opacity:.42;transform:scaleX(-1) translateY(0)}50%{opacity:.58;transform:scaleX(-1) translateY(-7px)}}
.gw-guardian.gr{transform:scaleX(-1)}
body.gw-threat .gw-guardian{opacity:.68}
/* rings */
.gw-ring{position:fixed;left:50%;top:44%;width:640px;height:640px;margin:-320px 0 0 -320px;
border-radius:50%;pointer-events:none;border:1px solid rgba(44,232,196,.07);
box-shadow:0 0 120px rgba(44,232,196,.05) inset;animation:gw-ring-pulse 7s ease-in-out infinite;will-change:transform,opacity}
.gw-ring.r2{width:900px;height:900px;margin:-450px 0 0 -450px;animation-delay:2.4s}
@keyframes gw-ring-pulse{0%,100%{opacity:.5;transform:scale(1)}50%{opacity:1;transform:scale(1.015)}}
/* particles */
.gw-particle{position:fixed;width:3px;height:3px;border-radius:50%;background:var(--gw-teal);
opacity:.16;pointer-events:none;animation:gw-float 14s ease-in-out infinite;will-change:transform}
@keyframes gw-float{0%,100%{transform:translateY(0)}50%{transform:translateY(-46px)}}
/* D — pulse core */
.gw-core{position:fixed;left:50%;bottom:2.2vh;transform:translateX(-50%);width:150px;height:110px;
pointer-events:none;opacity:.85}
.gw-core .base{position:absolute;left:50%;bottom:0;width:150px;height:34px;transform:translateX(-50%);
background:radial-gradient(50% 50% at 50% 50%,rgba(44,232,196,.22),transparent 72%);border-radius:50%}
.gw-core .dot{position:absolute;left:50%;bottom:26px;width:14px;height:14px;margin-left:-7px;
border-radius:50%;background:var(--gw-teal);box-shadow:0 0 18px var(--gw-teal),0 0 46px rgba(44,232,196,.5);
animation:gw-heart 4.2s ease-in-out infinite}
@keyframes gw-heart{0%,18%,100%{opacity:.55;transform:scale(1)}7%{opacity:1;transform:scale(1.25)}11%{opacity:.8;transform:scale(1.05)}14%{opacity:1;transform:scale(1.2)}}
.gw-core .wave,.gw-core .wave2{position:absolute;left:50%;bottom:8px;width:104px;height:52px;
margin-left:-52px;border:1px solid rgba(44,232,196,.5);border-radius:50%;opacity:0;
animation:gw-corewave 4.2s ease-out infinite;will-change:transform,opacity}
.gw-core .wave2{animation-delay:1.4s}
@keyframes gw-corewave{0%{opacity:.55;transform:scale(.35)}70%{opacity:0;transform:scale(1.5)}100%{opacity:0;transform:scale(1.5)}}
/* E — security scans */
.gw-sweep{position:fixed;top:0;bottom:0;left:-2px;width:2px;pointer-events:none;opacity:0;
background:linear-gradient(180deg,transparent,var(--gw-teal-dim),transparent);
animation:gw-sweep 12s ease-in-out infinite;will-change:transform,opacity}
@keyframes gw-sweep{0%,58%,100%{opacity:0;transform:translateX(0)}60%{opacity:.7;transform:translateX(4vw)}82%{opacity:.7;transform:translateX(96vw)}84%{opacity:0;transform:translateX(100vw)}}
/* F — red glints + threat wash */
.gw-glint{position:fixed;width:4px;height:4px;border-radius:50%;background:var(--gw-red);
box-shadow:0 0 10px var(--gw-red-dim);opacity:0;pointer-events:none;animation:gw-glint 13s ease-in-out infinite}
.gw-glint.g2{animation-duration:17s;animation-delay:6s}.gw-glint.g3{animation-duration:23s;animation-delay:11s}
@keyframes gw-glint{0%,91%,100%{opacity:0}93%,96%{opacity:.8}}
.gw-redwash{position:fixed;inset:0;pointer-events:none;opacity:0;
background:radial-gradient(120% 90% at 50% 50%,transparent 52%,rgba(210,30,22,.30) 100%)}
body.gw-threat .gw-redwash{animation:gw-threatpulse 3s ease-out 1 forwards}
@keyframes gw-threatpulse{0%{opacity:0}18%{opacity:.6}52%{opacity:.22}100%{opacity:.14}}
/* side intel panels — decorative capability copy only, never live data */
.gw-panel{position:fixed;bottom:9vh;width:248px;padding:1rem 1.1rem;z-index:2;
background:rgba(7,14,20,.66);border:1px solid rgba(120,220,200,.16);border-radius:4px;
clip-path:polygon(14px 0,100% 0,100% calc(100% - 14px),calc(100% - 14px) 100%,0 100%,0 14px);
backdrop-filter:blur(8px);-webkit-backdrop-filter:blur(8px);font-size:.72rem;letter-spacing:.04em}
.gw-panel.pl{left:3vw}.gw-panel.pr{right:3vw}
.gw-panel h2{font-size:.72rem;letter-spacing:.22em;text-transform:uppercase;color:var(--gw-teal);
display:flex;align-items:center;gap:.5rem;margin-bottom:.7rem}
.gw-panel ul{list-style:none}
.gw-panel li{color:#93aca7;padding:.22rem 0;display:flex;align-items:center;gap:.5rem}
.gw-panel li::before{content:"";width:5px;height:5px;border-radius:50%;background:var(--gw-teal);
box-shadow:0 0 6px var(--gw-teal-dim);flex:none}
.gw-panel.pr h2.idle-title{color:#6f8783}
.gw-panel.pr li::before{background:#4d615d;box-shadow:none}
.gw-panel.pr .threat-only{display:none}
.gw-panel.pr h2.threat-title{display:none}
body.gw-threat .gw-panel.pr{border-color:rgba(255,77,66,.45)}
body.gw-threat .gw-panel.pr h2.threat-title{display:flex;color:var(--gw-red)}
body.gw-threat .gw-panel.pr h2.idle-title{display:none}
body.gw-threat .gw-panel.pr .idle-only{display:none}
body.gw-threat .gw-panel.pr .threat-only{display:flex}
body.gw-threat .gw-panel.pr li::before{background:var(--gw-red);box-shadow:0 0 6px var(--gw-red-dim)}
/* mark + titles */
.gw-head{position:relative;z-index:2;display:flex;flex-direction:column;align-items:center}
.gw-halo{position:absolute;top:-34px;width:250px;height:250px;border-radius:50%;pointer-events:none;
background:radial-gradient(50% 50% at 50% 50%,rgba(44,232,196,.16),transparent 70%);
animation:gw-halo 6.5s ease-in-out infinite;will-change:transform,opacity}
@keyframes gw-halo{0%,100%{opacity:.6;transform:scale(1)}50%{opacity:1;transform:scale(1.07)}}
.gw-markimg{width:132px;height:auto;display:block;position:relative;z-index:2;
animation:gw-markbreathe 6.5s ease-in-out infinite;will-change:transform}
@keyframes gw-markbreathe{0%,100%{transform:scale(1)}50%{transform:scale(1.025)}}
.gw-title{font-size:2.15rem;font-weight:700;letter-spacing:.02em;text-align:center;margin-top:.35rem}
.gw-title b{color:var(--gw-teal)}
.gw-sub{color:var(--gw-teal);font-size:.72rem;letter-spacing:.42em;text-transform:uppercase;
text-align:center;margin:.7rem 0 1.7rem}
/* G — the stable glass card */
.gw-card{position:relative;z-index:2;width:min(432px,calc(100vw - 2.2rem));
background:var(--gw-card);border:1px solid var(--gw-border);border-radius:16px;
padding:2rem 2rem 1.6rem;backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px);
box-shadow:0 24px 70px rgba(0,0,0,.55),0 0 40px rgba(44,232,196,.05),0 0 0 1px rgba(255,255,255,.02) inset;
overflow:hidden}
.gw-scan{position:absolute;left:0;right:0;top:0;height:1px;pointer-events:none;
background:linear-gradient(90deg,transparent,var(--gw-teal-dim),transparent);
animation:gw-scan 6s ease-in-out infinite}
@keyframes gw-scan{0%,100%{top:6%;opacity:0}12%{opacity:1}50%{top:94%;opacity:1}62%{opacity:0}}
.gw-card h1{font-size:.82rem;font-weight:600;letter-spacing:.34em;text-transform:uppercase;
color:#d7e8e4;text-align:center;margin-bottom:.45rem}
.gw-card .tagline{text-align:center;color:var(--gw-muted);font-size:.78rem;margin-bottom:1.35rem}
.gw-field{position:relative;margin-bottom:1rem}
.gw-field input{width:100%;padding:.95rem 2.9rem .95rem 2.7rem;border-radius:12px;
background:rgba(5,11,17,.85);border:1px solid rgba(120,220,200,.14);color:var(--gw-text);
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
font-size:.84rem;font-weight:700;letter-spacing:.3em;text-transform:uppercase;color:#052019;
background:linear-gradient(160deg,#37f0cd,#20c99f);border:1px solid rgba(44,232,196,.5);
box-shadow:0 6px 26px rgba(44,232,196,.22);transition:filter .18s,transform .12s,box-shadow .3s}
.gw-submit:hover{filter:brightness(1.08);box-shadow:0 6px 34px rgba(44,232,196,.35)}
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
.gw-footer{position:relative;z-index:2;margin-top:2rem;color:#54756e;font-size:.7rem;
letter-spacing:.4em;text-transform:uppercase;display:flex;align-items:center;gap:1rem;text-align:center}
.gw-footer::before,.gw-footer::after{content:"";height:1px;width:44px;
background:linear-gradient(90deg,transparent,rgba(44,232,196,.4));display:block}
.gw-footer::after{background:linear-gradient(90deg,rgba(44,232,196,.4),transparent)}
body.gw-threat .gw-footer{color:var(--gw-red)}
body.gw-threat .gw-footer::before{background:linear-gradient(90deg,transparent,var(--gw-red-dim))}
body.gw-threat .gw-footer::after{background:linear-gradient(90deg,var(--gw-red-dim),transparent)}
/* authorized transition overlay */
.gw-grant{position:fixed;inset:0;z-index:9;display:none;align-items:center;justify-content:center;
flex-direction:column;background:radial-gradient(60% 60% at 50% 50%,rgba(5,18,16,.92),rgba(2,5,11,.97))}
.gw-grant.on{display:flex}
.gw-grant .gring{width:120px;height:120px;border-radius:50%;border:2px solid var(--gw-teal);
box-shadow:0 0 40px rgba(44,232,196,.5);animation:gw-grant-ring .7s ease-out forwards}
@keyframes gw-grant-ring{from{opacity:0;transform:scale(.5)}60%{opacity:1}to{opacity:.9;transform:scale(1)}}
.gw-grant p{margin-top:1.3rem;color:var(--gw-teal);font-size:.85rem;letter-spacing:.4em;
text-transform:uppercase;font-weight:700}
.gw-skip{position:absolute;left:-9999px;top:0;background:#0a141c;color:var(--gw-text);
padding:.6rem 1rem;border-radius:8px;z-index:10}
.gw-skip:focus{left:1rem;top:1rem}
.visually-hidden{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);
white-space:nowrap}
/* responsive */
@media (max-width:1180px){.gw-panel,.gw-guardian{display:none}}
@media (max-width:560px){.gw-title{font-size:1.7rem}.gw-card{padding:1.6rem 1.25rem 1.35rem}
.gw-ring,.gw-ring.r2,.gw-core,.gw-atmo{display:none}.gw-markimg{width:104px}
.gw-halo{width:190px;height:190px;top:-24px}
.gw-net{width:290px;height:290px;margin-left:-145px;top:-70px}}
@media (max-height:640px){.gw-core{display:none}}
/* reduced motion: near-static, fully usable */
@media (prefers-reduced-motion:reduce){
.gw-atmo,.gw-grid,.gw-ring,.gw-particle,.gw-scan,.gw-monitor .dot,.gw-halo,.gw-markimg,
.gw-net .orbit,.gw-net .node,.gw-guardian,.gw-core .dot,.gw-core .wave,.gw-core .wave2,
.gw-sweep,.gw-glint,.gw-redwash,.gw-grant .gring{animation:none!important}
.gw-scan,.gw-sweep,.gw-glint,.gw-particle{display:none}
body.gw-threat .gw-redwash{opacity:.14}}
"""

_SHIELD_SVG = (
    "<svg width='13' height='13' viewBox='0 0 24 24' fill='none' aria-hidden='true'>"
    "<path d='M12 2l8 4v6c0 5-3.4 8.6-8 10-4.6-1.4-8-5-8-10V6l8-4z' "
    "stroke='currentColor' stroke-width='2' stroke-linejoin='round'/></svg>"
)

_LOCK_BADGE_SVG = (
    "<svg width='14' height='14' viewBox='0 0 24 24' fill='none' aria-hidden='true'>"
    "<rect x='4' y='10' width='16' height='11' rx='2' stroke='currentColor' stroke-width='2'/>"
    "<path d='M8 10V7a4 4 0 0 1 8 0v3' stroke='currentColor' stroke-width='2'/></svg>"
)

_WARN_SVG = (
    "<svg width='14' height='14' viewBox='0 0 24 24' fill='none' aria-hidden='true'>"
    "<path d='M12 3l10 18H2L12 3z' stroke='currentColor' stroke-width='2' stroke-linejoin='round'/>"
    "<path d='M12 10v5' stroke='currentColor' stroke-width='2' stroke-linecap='round'/>"
    "<circle cx='12' cy='18' r='.6' fill='currentColor'/></svg>"
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

# Abstract hooded guardian silhouette — deliberately faceless and near-static.
_GUARDIAN_SVG = (
    "<svg viewBox='0 0 220 320' fill='none' aria-hidden='true' "
    "preserveAspectRatio='xMidYMax meet'>"
    "<defs><linearGradient id='gwrim' x1='0' y1='0' x2='0' y2='1'>"
    "<stop offset='0' stop-color='#2ce8c4' stop-opacity='.34'/>"
    "<stop offset='.55' stop-color='#173c3a' stop-opacity='.22'/>"
    "<stop offset='1' stop-color='#3a0f0c' stop-opacity='.4'/></linearGradient></defs>"
    "<path d='M110 18 C66 30 48 74 50 116 L38 236 C30 292 60 314 110 316 "
    "C160 314 190 292 182 236 L170 116 C172 74 154 30 110 18 Z' "
    "fill='#04070c' stroke='url(#gwrim)' stroke-width='2'/>"
    "<ellipse cx='110' cy='104' rx='34' ry='42' fill='#010204'/>"
    "<path d='M62 210 C84 236 136 236 158 210' stroke='#0c1a1f' stroke-width='3' fill='none'/>"
    "</svg>"
)

_NET_SVG = (
    "<svg viewBox='0 0 380 380' width='100%' height='100%' fill='none' aria-hidden='true'>"
    "<g class='orbit'><circle cx='190' cy='190' r='150' stroke='rgba(44,232,196,.14)' "
    "stroke-width='1' stroke-dasharray='3 14'/>"
    "<circle class='node' cx='190' cy='40' r='2.6' fill='#2ce8c4'/>"
    "<circle class='node n3' cx='340' cy='190' r='2.2' fill='#2ce8c4'/></g>"
    "<g class='orbit o2'><circle cx='190' cy='190' r='118' stroke='rgba(44,232,196,.1)' "
    "stroke-width='1' stroke-dasharray='2 10'/>"
    "<circle class='node n2' cx='190' cy='72' r='2.2' fill='#5ff05f'/>"
    "<circle class='node n4' cx='72' cy='190' r='2' fill='#2ce8c4'/></g>"
    "</svg>"
)

_PARTICLES = "".join(
    f"<span class='gw-particle' style='left:{left}%;top:{top}%;"
    f"animation-delay:{delay}s;animation-duration:{dur}s'></span>"
    for left, top, delay, dur in (
        (8, 24, 0, 13), (16, 68, 2.5, 16), (27, 40, 5, 12), (38, 82, 1.2, 15),
        (61, 76, 3.8, 14), (72, 30, 0.6, 17), (84, 58, 4.4, 13), (92, 22, 2.0, 15),
        (47, 18, 3.1, 18), (55, 60, 1.8, 12),
    )
)

_GLINTS = (
    "<span class='gw-glint' style='left:11%;top:31%'></span>"
    "<span class='gw-glint g2' style='left:87%;top:24%'></span>"
    "<span class='gw-glint g3' style='left:78%;top:66%'></span>"
)

# Every capability named here exists server-side: TLS transport in production,
# admin_audit_logs access logging, sliding-window login throttling, and
# failed-login account lockout. Nothing else may be claimed.
_LEFT_PANEL = (
    "<aside class='gw-panel pl' aria-hidden='true'>"
    f"<h2>{_LOCK_BADGE_SVG} Access Protected</h2>"
    "<ul><li>Encrypted connection</li><li>Access logging enabled</li>"
    "<li>Login throttling armed</li><li>Lockout controls active</li></ul>"
    "</aside>"
)

_RIGHT_PANEL = (
    "<aside class='gw-panel pr' aria-hidden='true'>"
    f"<h2 class='idle-title'>{_SHIELD_SVG} System Guard Active</h2>"
    f"<h2 class='threat-title'>{_WARN_SVG} Unauthorized Access Detected</h2>"
    "<ul><li class='idle-only'>Monitoring active</li>"
    "<li class='idle-only'>No active alerts</li>"
    "<li class='threat-only'>Attempt logged</li>"
    "<li class='threat-only'>Source recorded</li>"
    "<li class='threat-only'>Rate limits engaged</li></ul>"
    "</aside>"
)


def _shell(title, inner, body_class="gw"):
    return (
        "<!doctype html><html lang='en'><head>"
        "<meta charset='utf-8'/>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'/>"
        "<meta name='robots' content='noindex,nofollow'/>"
        "<meta name='referrer' content='no-referrer'/>"
        f"<title>{title}</title>"
        f"<style>{_GATEWAY_CSS}</style>"
        f"</head><body class='{body_class}'>"
        "<a class='gw-skip' href='#gw-form'>Skip to login form</a>"
        "<div class='gw-atmo' aria-hidden='true'></div>"
        "<div class='gw-gridwrap' aria-hidden='true'><div class='gw-grid'></div></div>"
        "<div class='gw-floor' aria-hidden='true'></div>"
        "<div class='gw-ring' aria-hidden='true'></div>"
        "<div class='gw-ring r2' aria-hidden='true'></div>"
        f"<div class='gw-guardian gl' aria-hidden='true'>{_GUARDIAN_SVG}</div>"
        f"<div class='gw-guardian gr' aria-hidden='true'>{_GUARDIAN_SVG}</div>"
        f"{_PARTICLES}{_GLINTS}"
        "<span class='gw-sweep' aria-hidden='true'></span>"
        "<div class='gw-redwash' aria-hidden='true'></div>"
        f"{_LEFT_PANEL}{_RIGHT_PANEL}"
        f"{inner}"
        "<div class='gw-core' aria-hidden='true'><span class='base'></span>"
        "<span class='wave'></span><span class='wave2'></span><span class='dot'></span></div>"
        "<p class='gw-footer'>Authorized personnel only</p>"
        "</body></html>"
    )


def _head_block():
    return (
        "<div class='gw-head'>"
        f"<div class='gw-net' aria-hidden='true'>{_NET_SVG}</div>"
        "<span class='gw-halo' aria-hidden='true'></span>"
        f"<img class='gw-markimg' src='{GATEWAY_MARK_SRC}' width='132' height='95' "
        "alt='' aria-hidden='true'/>"
        "<p class='gw-title'>Pulse<b>Soc</b></p>"
        "<p class='gw-sub'>Operations Command Center</p>"
        "</div>"
    )


def render_gateway(csrf_token, state="idle"):
    """Full standalone HTML for the unauthenticated login gateway."""
    kind, copy = _STATE_COPY.get(state, _STATE_COPY["idle"])
    alert = (
        f"<div class='gw-alert {kind}' role='alert'>{_SHIELD_SVG}<span>{copy}</span></div>"
        if copy else ""
    )
    disabled = " disabled" if state == "rate_limited" else ""
    threat = " gw-threat" if state in ("denied", "rate_limited") else ""
    inner = (
        f"{_head_block()}"
        "<section class='gw-card'>"
        "<span class='gw-scan' aria-hidden='true'></span>"
        "<h1>Secure Access Only</h1>"
        "<p class='tagline'>This system is for authorized personnel only.</p>"
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
        "<div class='gw-grant' id='gw-grant' role='status' aria-live='polite'>"
        "<span class='gring' aria-hidden='true'></span><p>Access Authorized</p></div>"
        # Progressive enhancement only: without JS (or if fetch fails) the form
        # posts natively. The fetch path exists solely to play the short
        # ACCESS AUTHORIZED transition (<=700ms) before entering the
        # authenticated app, or to re-render the generic denied state.
        "<script>(function(){"
        "var e=document.getElementById('gw-eye'),p=document.getElementById('gw-password');"
        "if(e&&p){e.addEventListener('click',function(){"
        "var show=p.type==='password';p.type=show?'text':'password';"
        "e.setAttribute('aria-pressed',show?'true':'false');"
        "e.setAttribute('aria-label',show?'Hide password':'Show password');});}"
        "var f=document.getElementById('gw-form'),s=document.getElementById('gw-submit'),"
        "g=document.getElementById('gw-grant'),busy=false,"
        "rm=window.matchMedia&&window.matchMedia('(prefers-reduced-motion: reduce)').matches;"
        "if(f&&s&&window.fetch){f.addEventListener('submit',function(ev){"
        "if(busy)return;busy=true;ev.preventDefault();"
        "s.disabled=true;s.textContent='Authenticating\\u2026';"
        "fetch(f.getAttribute('action'),{method:'POST',body:new FormData(f),"
        "credentials:'same-origin',redirect:'follow',headers:{'Accept':'text/html'}})"
        ".then(function(r){"
        "var landed=(r.url||'').split('?')[0];"
        "if(r.ok&&landed.indexOf(f.getAttribute('action'))===-1){"
        "if(g&&!rm){g.classList.add('on');"
        "setTimeout(function(){window.location.assign(r.url);},650);}"
        "else{window.location.assign(r.url);}return null;}"
        "return r.text();})"
        ".then(function(html){if(html){document.open();document.write(html);document.close();}})"
        ".catch(function(){window.location.reload();});"
        "});}else if(f&&s){f.addEventListener('submit',function(){"
        "s.disabled=true;s.textContent='Authenticating\\u2026';});}"
        "})();</script>"
    )
    return _shell("Secure Access | PulseSoc Operations", inner, body_class="gw" + threat)


def render_notice(title, body_html):
    """Standalone pre-auth notice page (e.g. owner bootstrap) — same closed-door
    chrome as the login gateway, no admin shell, no navigation."""
    inner = (
        f"{_head_block()}"
        "<section class='gw-card'>"
        f"<h1>{title}</h1>"
        f"<div style='font-size:.9rem;line-height:1.65;color:#c9dcd7'>{body_html}</div>"
        "</section>"
    )
    return _shell(f"{title} | PulseSoc Operations", inner)
