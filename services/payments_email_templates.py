"""The one transactional email chrome for marketplace money events.

Every seller- and buyer-facing payment email in PulseSoc is rendered here. The
module is deliberately pure — data in, ``{subject, html, text}`` out. It opens no
connection, reads no environment, and imports nothing from ``bot``, so a test can
render all twenty templates without a database and a reviewer can read what a
seller will actually receive without booting 111k lines.

Why a module and not another f-string at a call site: ``services/email_service``
already carries sixteen hand-rolled bodies built inline where they are sent. That
is survivable for a password reset, which is one sentence and a link. It is not
survivable for money mail, where the same four-row status block has to say the
same thing in nineteen places and a seller who sees "Payouts: ENABLED" in one
email and "not yet" in the next has been told the platform does not know its own
state. One layout, one status vocabulary, one security notice.

Email-safe by construction, because these are the constraints that actually bite:

* Table layout throughout. Outlook on Windows renders through Word, which has no
  flexbox and no reliable ``div`` box model.
* Inline CSS only. Gmail strips ``<style>`` blocks on forwarded mail and the
  clipped-message view drops them entirely.
* Solid hex, never ``rgba()`` — Word drops the whole declaration rather than
  degrading, so an ``rgba`` border becomes no border.
* Explicit ``background-color`` on every filled cell, so that an iOS/Apple Mail
  dark-mode pass cannot invert a dark palette into an unreadable one.
* No JavaScript, no web fonts, no external stylesheet, no background images.

The palette matches ``email_service.branded_email_html`` so that a seller who
gets a password reset and a payout notice recognises both as PulseSoc.
"""

from __future__ import annotations

import html
from typing import Any, Callable, Dict, Iterable, Mapping, Sequence

from services import app_links

# --------------------------------------------------------------------------
# Brand tokens
# --------------------------------------------------------------------------

PAGE_BG = "#070b14"
CARD_BG = "#0d1627"
PANEL_BG = "#111e33"
BORDER = "#1d3350"
HEADING = "#ffffff"
BODY = "#c4d2e7"
MUTED = "#9fb5c0"
ACCENT = "#36e58f"
ACCENT_INK = "#041019"
LINK = "#6edff6"
WARNING = "#ffd9a0"
DANGER = "#ff9b9b"

FONT_STACK = "Inter,-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif"
CARD_WIDTH = 620

SUPPORT_EMAIL = "support@pulsesoc.com"
PRODUCT_NAME = "PulseSoc"
COMPANY_NAME = "CoinPlotXAI Inc."

#: Where the four standing status rows point. Relative paths only — they are run
#: through ``app_links.app_intent_url`` so an installed app opens natively and a
#: browser still resolves the same resource.
SELLER_PAYMENTS_PATH = "/pulse/merchant/payouts"
SELLER_DASHBOARD_PATH = "/pulse/merchant/dashboard"
SELLER_APPLY_PATH = "/pulse/merchant/apply"
SELLER_ORDERS_PATH = "/pulse/seller-store?mode=orders"
BUYER_ORDERS_PATH = "/pulse/orders"
PAYMENTS_HELP_PATH = "/pulse/help"
TERMS_PATH = "/terms"
PRIVACY_PATH = "/privacy"


# --------------------------------------------------------------------------
# Status vocabulary
# --------------------------------------------------------------------------

#: tone -> colour. Four tones is deliberate: a seller reading a status block
#: should be able to tell "done", "you must act", "waiting on someone else" and
#: "something is wrong" apart at a glance without reading the words.
_TONES = {
    "ok": ACCENT,
    "action": WARNING,
    "idle": MUTED,
    "bad": DANGER,
}

#: The canonical rendering of every status value these emails can show. Keeping
#: the label *and* the tone in one table is what stops "SETUP REQUIRED" being
#: green in one email and amber in another.
STATUS_LABELS: Dict[str, tuple] = {
    # Seller application (mirrors services/seller_lifecycle statuses)
    "draft": ("DRAFT", "idle"),
    "submitted": ("SUBMITTED", "idle"),
    "under_review": ("UNDER REVIEW", "idle"),
    "information_requested": ("MORE INFORMATION REQUIRED", "action"),
    "resubmitted": ("RESUBMITTED", "idle"),
    "approved": ("APPROVED", "ok"),
    "rejected": ("DECLINED", "bad"),
    "withdrawn": ("WITHDRAWN", "idle"),
    "expired": ("EXPIRED", "idle"),
    "suspended": ("SUSPENDED", "bad"),
    # Stripe Connect account
    "not_started": ("SETUP REQUIRED", "action"),
    "onboarding": ("SETUP IN PROGRESS", "action"),
    "requirements_due": ("VERIFICATION REQUIRED", "action"),
    "under_stripe_review": ("UNDER REVIEW BY STRIPE", "idle"),
    "payments_ready": ("CONNECTED", "ok"),
    "payouts_ready": ("CONNECTED", "ok"),
    "restricted": ("RESTRICTED", "bad"),
    "disabled": ("DISABLED", "bad"),
    # Capability flags
    "enabled": ("ENABLED", "ok"),
    "not_enabled": ("NOT YET ENABLED", "idle"),
    "paused": ("TEMPORARILY PAUSED", "action"),
    "unavailable": ("UNAVAILABLE", "bad"),
}


def status_label(value: Any) -> tuple:
    """(display text, tone) for a status value, never raising on an unknown one.

    An unrecognised status renders as its own uppercased name in the neutral
    tone rather than falling back to a *specific* status. Guessing here is how a
    restricted account would end up displayed as "ENABLED" — the failure mode is
    that the email confidently tells a seller the opposite of the truth, so the
    default is deliberately the one tone that asserts nothing.
    """
    key = str(value or "").strip().lower()
    if key in STATUS_LABELS:
        return STATUS_LABELS[key]
    return (key.replace("_", " ").upper() or "UNKNOWN", "idle")


# --------------------------------------------------------------------------
# Primitives
# --------------------------------------------------------------------------


def _esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _url(path_or_url: Any, source: str = "email") -> str:
    """Absolute, app-intent-marked URL for an email CTA.

    Routed through ``app_links`` rather than string-concatenated so that an
    installed app opens the destination natively and an uninstalled one gets the
    approved web fallback — and so that this module cannot become the nineteenth
    place that hardcodes ``https://pulsesoc.com``. Anything off-host (a Stripe
    onboarding link) comes back untouched, which is what we want: those must stay
    Stripe's own URLs.
    """
    raw = str(path_or_url or "").strip()
    if not raw:
        return ""
    if raw.startswith("http://"):
        # Never emit a cleartext link from a money email.
        return ""
    try:
        return app_links.app_intent_url(raw, source)
    except Exception:
        # A link that cannot be built must not take the whole email down with
        # it: the seller still needs to read that their payout failed.
        return raw if raw.startswith("https://") else ""


def _paragraph(text: str, *, color: str = BODY, size: int = 15, top: int = 0) -> str:
    return (
        f"<p style=\"margin:{top}px 0 14px;padding:0;color:{color};"
        f"font-family:{FONT_STACK};font-size:{size}px;line-height:1.65\">{text}</p>"
    )


def _heading(text: str) -> str:
    return (
        f"<h2 style=\"margin:26px 0 10px;padding:0;color:{HEADING};"
        f"font-family:{FONT_STACK};font-size:17px;line-height:1.35;font-weight:700\">{_esc(text)}</h2>"
    )


def _button(label: str, url: str, *, primary: bool = True) -> str:
    """A table-wrapped button, because Outlook ignores padding on a bare anchor.

    ``display:block`` on the anchor inside a ``bgcolor`` cell is the shape that
    survives Word rendering; ``border-radius`` is simply dropped there, which
    degrades to a square button rather than to an invisible one.
    """
    if not url:
        return ""
    bg = ACCENT if primary else PANEL_BG
    ink = ACCENT_INK if primary else HEADING
    edge = ACCENT if primary else BORDER
    return (
        "<table role=\"presentation\" cellpadding=\"0\" cellspacing=\"0\" border=\"0\" "
        "style=\"margin:0 0 12px\"><tr>"
        f"<td bgcolor=\"{bg}\" style=\"background-color:{bg};border:1px solid {edge};"
        "border-radius:10px\">"
        f"<a href=\"{_esc(url)}\" style=\"display:block;padding:13px 22px;color:{ink};"
        f"font-family:{FONT_STACK};font-size:15px;font-weight:700;text-decoration:none\">"
        f"{_esc(label)}</a></td></tr></table>"
    )


def _status_table(rows: Sequence[tuple]) -> str:
    """The four-row state block. ``rows`` is a sequence of (label, status value)."""
    if not rows:
        return ""
    cells = []
    for index, (label, value) in enumerate(rows):
        text, tone = status_label(value)
        color = _TONES.get(tone, MUTED)
        edge = "" if index == 0 else f"border-top:1px solid {BORDER};"
        cells.append(
            f"<tr><td style=\"{edge}padding:11px 14px;color:{MUTED};"
            f"font-family:{FONT_STACK};font-size:13px;line-height:1.4\">{_esc(label)}</td>"
            f"<td align=\"right\" style=\"{edge}padding:11px 14px;color:{color};"
            f"font-family:{FONT_STACK};font-size:13px;font-weight:700;line-height:1.4;"
            f"letter-spacing:.3px\">{_esc(text)}</td></tr>"
        )
    return (
        "<table role=\"presentation\" width=\"100%\" cellpadding=\"0\" cellspacing=\"0\" "
        f"border=\"0\" bgcolor=\"{PANEL_BG}\" style=\"background-color:{PANEL_BG};"
        f"border:1px solid {BORDER};border-radius:12px;margin:0 0 18px\">"
        + "".join(cells)
        + "</table>"
    )


def _facts(rows: Sequence[tuple]) -> str:
    """A label/value block for order and payout detail. Values are plain text."""
    if not rows:
        return ""
    cells = []
    for index, (label, value) in enumerate(rows):
        if value in (None, ""):
            continue
        edge = "" if index == 0 else f"border-top:1px solid {BORDER};"
        cells.append(
            f"<tr><td style=\"{edge}padding:10px 14px;color:{MUTED};"
            f"font-family:{FONT_STACK};font-size:13px\">{_esc(label)}</td>"
            f"<td align=\"right\" style=\"{edge}padding:10px 14px;color:{HEADING};"
            f"font-family:{FONT_STACK};font-size:13px;font-weight:600\">{_esc(value)}</td></tr>"
        )
    if not cells:
        return ""
    return (
        "<table role=\"presentation\" width=\"100%\" cellpadding=\"0\" cellspacing=\"0\" "
        f"border=\"0\" style=\"border:1px solid {BORDER};border-radius:12px;margin:0 0 18px\">"
        + "".join(cells)
        + "</table>"
    )


def _notice(text: str, *, tone: str = "idle") -> str:
    color = _TONES.get(tone, MUTED)
    return (
        f"<table role=\"presentation\" width=\"100%\" cellpadding=\"0\" cellspacing=\"0\" border=\"0\" "
        f"style=\"border-left:3px solid {color};margin:0 0 18px\"><tr>"
        f"<td style=\"padding:2px 0 2px 13px;color:{MUTED};font-family:{FONT_STACK};"
        f"font-size:13px;line-height:1.6\">{text}</td></tr></table>"
    )


#: Stage 50. The exact list a seller is told PulseSoc will never ask for.
SECURITY_NOTICE = (
    f"<strong style=\"color:{HEADING}\">Security.</strong> {PRODUCT_NAME} will never ask you "
    "by chat, email, or phone for your full card number, CVV, bank password, Stripe password, "
    "or a verification code. Complete every payment step inside the app or on Stripe's own "
    "pages — never from a link someone sends you privately."
)

#: The transparency wording. It says what is true under separate charges and
#: transfers: the buyer pays PulseSoc, and PulseSoc pays the seller through
#: Stripe. The stronger claim — that PulseSoc never holds funds — is false under
#: this charge model and is deliberately absent.
STRIPE_TRANSPARENCY = (
    "Payments are powered by Stripe. Stripe securely processes card payments and manages "
    f"seller payout infrastructure, including identity and bank verification. {PRODUCT_NAME} "
    "does not store your full card or bank account credentials."
)


# --------------------------------------------------------------------------
# Layout
# --------------------------------------------------------------------------


def _layout(*, title: str, preheader: str, blocks: Iterable[str]) -> str:
    body = "".join(block for block in blocks if block)
    help_url = _url(PAYMENTS_HELP_PATH)
    terms_url = _url(TERMS_PATH)
    privacy_url = _url(PRIVACY_PATH)
    footer_links = " &nbsp;•&nbsp; ".join(
        part
        for part in (
            f"<a href=\"{_esc(help_url)}\" style=\"color:{LINK};text-decoration:none\">Payments help</a>" if help_url else "",
            f"<a href=\"mailto:{SUPPORT_EMAIL}\" style=\"color:{LINK};text-decoration:none\">{SUPPORT_EMAIL}</a>",
            f"<a href=\"{_esc(terms_url)}\" style=\"color:{LINK};text-decoration:none\">Terms</a>" if terms_url else "",
            f"<a href=\"{_esc(privacy_url)}\" style=\"color:{LINK};text-decoration:none\">Privacy</a>" if privacy_url else "",
        )
        if part
    )
    return f"""<!DOCTYPE html>
<html lang="en" style="margin:0;padding:0">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="x-apple-disable-message-reformatting">
<meta name="color-scheme" content="dark">
<meta name="supported-color-schemes" content="dark">
<title>{_esc(title)}</title>
</head>
<body style="margin:0;padding:0;background-color:{PAGE_BG};">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;color:transparent;height:0;width:0">{_esc(preheader)}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="{PAGE_BG}" style="background-color:{PAGE_BG};margin:0;padding:0">
<tr><td align="center" style="padding:24px 12px">
<table role="presentation" width="{CARD_WIDTH}" cellpadding="0" cellspacing="0" border="0" style="width:100%;max-width:{CARD_WIDTH}px">
<tr><td style="padding:0 0 16px">
<span style="color:{HEADING};font-family:{FONT_STACK};font-size:20px;font-weight:800;letter-spacing:.4px">Pulse<span style="color:{ACCENT}">Soc</span></span>
</td></tr>
<tr><td bgcolor="{CARD_BG}" style="background-color:{CARD_BG};border:1px solid {BORDER};border-radius:14px;padding:26px">
<h1 style="margin:0 0 16px;padding:0;color:{HEADING};font-family:{FONT_STACK};font-size:22px;line-height:1.3;font-weight:800">{_esc(title)}</h1>
{body}
</td></tr>
<tr><td style="padding:18px 6px 0">
<p style="margin:0 0 8px;color:{MUTED};font-family:{FONT_STACK};font-size:12px;line-height:1.6">{footer_links}</p>
<p style="margin:0 0 6px;color:{MUTED};font-family:{FONT_STACK};font-size:12px;line-height:1.6">{PRODUCT_NAME}&trade; &bull; Built by {COMPANY_NAME}</p>
<p style="margin:0;color:{MUTED};font-family:{FONT_STACK};font-size:12px;line-height:1.6">You received this because it concerns money or account access on your {PRODUCT_NAME} seller account. Notifications like this cannot be turned off.</p>
</td></tr>
</table>
</td></tr>
</table>
</body>
</html>"""


def _plain_text(*, title: str, lines: Sequence[str], ctas: Sequence[tuple]) -> str:
    """The text/plain alternative.

    Not a nicety: a text part is what a spam filter expects on transactional
    mail, and it is what a seller reading on a watch or a screen reader gets.
    """
    out = [title, "=" * min(len(title), 60), ""]
    out.extend(line for line in lines if line)
    if ctas:
        out.append("")
        for label, url in ctas:
            if url:
                out.append(f"{label}: {url}")
    out.extend(
        [
            "",
            STRIPE_TRANSPARENCY,
            "",
            f"Security: {PRODUCT_NAME} will never ask you for your full card number, CVV, bank "
            "password, Stripe password, or a verification code.",
            "",
            f"{PRODUCT_NAME} - {COMPANY_NAME} - {SUPPORT_EMAIL}",
        ]
    )
    return "\n".join(out)


# --------------------------------------------------------------------------
# Shared blocks
# --------------------------------------------------------------------------


def _seller_status_rows(ctx: Mapping[str, Any]) -> Sequence[tuple]:
    return (
        ("Seller application", ctx.get("seller_application_status") or "approved"),
        ("Stripe account", ctx.get("stripe_connect_status") or "not_started"),
        ("Card payments", ctx.get("card_payment_status") or "not_enabled"),
        ("Payouts", ctx.get("payout_status") or "not_enabled"),
    )


def money(amount_cents: Any, currency: str = "USD") -> str:
    """Format minor units for display. Never used for arithmetic."""
    try:
        cents = int(amount_cents or 0)
    except (TypeError, ValueError):
        return ""
    code = str(currency or "USD").upper()
    sign = "-" if cents < 0 else ""
    whole, part = divmod(abs(cents), 100)
    return f"{sign}{'$' if code == 'USD' else ''}{whole:,}.{part:02d}{'' if code == 'USD' else ' ' + code}"


# --------------------------------------------------------------------------
# Templates
# --------------------------------------------------------------------------

#: A spec builder takes the render context and returns the pieces the layout
#: needs. Keeping them as functions rather than a static dict is what lets a
#: template say "your payout failed because X" using the real reason.
SpecBuilder = Callable[[Mapping[str, Any]], Dict[str, Any]]


def _seller_approved(ctx: Mapping[str, Any]) -> Dict[str, Any]:
    first = _esc(ctx.get("seller_first_name") or "there")
    store = _esc(ctx.get("store_name") or "your store")
    onboarding = _url(ctx.get("stripe_onboarding_url") or SELLER_PAYMENTS_PATH)
    dashboard = _url(ctx.get("seller_dashboard_url") or SELLER_DASHBOARD_PATH)
    return {
        "subject": f"Your {PRODUCT_NAME} seller application has been approved",
        "title": f"{store} is approved",
        "preheader": "One more step before you can accept card payments: set up payments with Stripe.",
        "blocks": [
            _paragraph(f"Hi {first},"),
            _paragraph(
                f"Your {PRODUCT_NAME} seller application for <strong style=\"color:{HEADING}\">{store}</strong> "
                f"was approved on {_esc(ctx.get('approval_date') or 'today')}. Your seller dashboard is open now."
            ),
            _paragraph(
                "<strong style=\"color:%s\">Approval is not the same as being paid.</strong> %s has approved you "
                "to sell. Stripe — our payment provider — still has to verify your identity and payout details "
                "before you can accept card payments or receive money."
                % (HEADING, PRODUCT_NAME)
            ),
            _status_table(_seller_status_rows(ctx)),
            _button("Set up payments with Stripe", onboarding),
            _button("Open seller dashboard", dashboard, primary=False),
            _heading("What Stripe will ask you for"),
            _paragraph(
                "Your legal name and date of birth, your business details if you sell as a business, a "
                "government ID, and the bank account you want to be paid into. Stripe may ask for more "
                "depending on your country. You enter all of it on Stripe's own pages — it never passes "
                f"through {PRODUCT_NAME}."
            ),
            _notice(STRIPE_TRANSPARENCY),
            _notice(SECURITY_NOTICE, tone="action"),
        ],
        "text": [
            f"Hi {ctx.get('seller_first_name') or 'there'},",
            "",
            f"Your {PRODUCT_NAME} seller application for {ctx.get('store_name') or 'your store'} was approved "
            f"on {ctx.get('approval_date') or 'today'}.",
            "",
            "Approval is not the same as being paid. Stripe still has to verify your identity and payout "
            "details before you can accept card payments or receive money.",
            "",
            f"Seller application: {status_label(ctx.get('seller_application_status') or 'approved')[0]}",
            f"Stripe account: {status_label(ctx.get('stripe_connect_status') or 'not_started')[0]}",
            f"Card payments: {status_label(ctx.get('card_payment_status') or 'not_enabled')[0]}",
            f"Payouts: {status_label(ctx.get('payout_status') or 'not_enabled')[0]}",
        ],
        "ctas": [("Set up payments with Stripe", onboarding), ("Open seller dashboard", dashboard)],
    }


def _seller_application_received(ctx: Mapping[str, Any]) -> Dict[str, Any]:
    first = _esc(ctx.get("seller_first_name") or "there")
    dashboard = _url(ctx.get("application_url") or SELLER_APPLY_PATH)
    return {
        "subject": f"We received your {PRODUCT_NAME} seller application",
        "title": "Application received",
        "preheader": "We have your application and it is queued for review.",
        "blocks": [
            _paragraph(f"Hi {first},"),
            _paragraph(
                f"Your seller application for <strong style=\"color:{HEADING}\">"
                f"{_esc(ctx.get('store_name') or 'your store')}</strong> is in the review queue. "
                "We will email you when a reviewer reaches a decision."
            ),
            _status_table(_seller_status_rows(ctx)),
            _button("View application", dashboard, primary=False),
            _notice(SECURITY_NOTICE, tone="action"),
        ],
        "text": [
            f"Hi {ctx.get('seller_first_name') or 'there'},",
            "",
            "Your seller application is in the review queue. We will email you when a reviewer reaches a decision.",
        ],
        "ctas": [("View application", dashboard)],
    }


def _seller_more_info_required(ctx: Mapping[str, Any]) -> Dict[str, Any]:
    first = _esc(ctx.get("seller_first_name") or "there")
    dashboard = _url(ctx.get("application_url") or SELLER_APPLY_PATH)
    reason = _esc(ctx.get("reviewer_message") or "")
    return {
        "subject": f"Your {PRODUCT_NAME} seller application needs more information",
        "title": "We need a bit more information",
        "preheader": "A reviewer needs something else before they can decide.",
        "blocks": [
            _paragraph(f"Hi {first},"),
            _paragraph(
                "A reviewer looked at your seller application and needs more information before deciding."
            ),
            _notice(f"<strong style=\"color:{HEADING}\">What we need:</strong> {reason}", tone="action")
            if reason
            else "",
            _status_table(_seller_status_rows(ctx)),
            _button("Complete application", dashboard),
            _notice(SECURITY_NOTICE, tone="action"),
        ],
        "text": [
            f"Hi {ctx.get('seller_first_name') or 'there'},",
            "",
            "A reviewer needs more information before deciding on your seller application.",
            "",
            f"What we need: {ctx.get('reviewer_message') or ''}",
        ],
        "ctas": [("Complete application", dashboard)],
    }


def _seller_declined(ctx: Mapping[str, Any]) -> Dict[str, Any]:
    first = _esc(ctx.get("seller_first_name") or "there")
    dashboard = _url(ctx.get("application_url") or SELLER_APPLY_PATH)
    reason = _esc(ctx.get("reviewer_message") or "")
    return {
        "subject": f"Your {PRODUCT_NAME} seller application was declined",
        "title": "Application declined",
        "preheader": "A reviewer could not approve this application.",
        "blocks": [
            _paragraph(f"Hi {first},"),
            _paragraph("A reviewer was not able to approve your seller application."),
            _notice(f"<strong style=\"color:{HEADING}\">Reason given:</strong> {reason}", tone="bad")
            if reason
            else "",
            _paragraph(
                "You can fix what was raised and submit the same application again — your history stays "
                "with it, so a reviewer sees the full picture rather than a fresh row with no past."
            ),
            _button("Review and resubmit", dashboard, primary=False),
        ],
        "text": [
            f"Hi {ctx.get('seller_first_name') or 'there'},",
            "",
            "A reviewer was not able to approve your seller application.",
            "",
            f"Reason given: {ctx.get('reviewer_message') or ''}",
            "",
            "You can fix what was raised and submit the same application again.",
        ],
        "ctas": [("Review and resubmit", dashboard)],
    }


def _stripe_verification_required(ctx: Mapping[str, Any]) -> Dict[str, Any]:
    first = _esc(ctx.get("seller_first_name") or "there")
    onboarding = _url(ctx.get("stripe_onboarding_url") or SELLER_PAYMENTS_PATH)
    deadline = _esc(ctx.get("requirements_deadline") or "")
    items = ctx.get("requirements") or []
    listed = "".join(
        f"<li style=\"margin:0 0 6px\">{_esc(item)}</li>" for item in list(items)[:12]
    )
    return {
        "subject": "Action required: Stripe needs to verify your payment account",
        "title": "Stripe needs more information",
        "preheader": "Card payments or payouts will stop until this is completed.",
        "blocks": [
            _paragraph(f"Hi {first},"),
            _paragraph(
                "Stripe needs more information to keep your payment account in good standing. Until it is "
                "provided, card payments or payouts on your store may be paused."
            ),
            (
                f"<ul style=\"margin:0 0 16px;padding:0 0 0 20px;color:{BODY};font-family:{FONT_STACK};"
                f"font-size:14px;line-height:1.6\">{listed}</ul>"
            )
            if listed
            else "",
            _notice(
                f"<strong style=\"color:{HEADING}\">Due by {deadline}.</strong> After this date Stripe may "
                "restrict the account automatically.",
                tone="action",
            )
            if deadline
            else "",
            _status_table(_seller_status_rows(ctx)),
            _button("Complete verification", onboarding),
            _notice(SECURITY_NOTICE, tone="action"),
        ],
        "text": [
            f"Hi {ctx.get('seller_first_name') or 'there'},",
            "",
            "Stripe needs more information to keep your payment account in good standing. Until it is "
            "provided, card payments or payouts on your store may be paused.",
            "",
            *[f"- {item}" for item in list(items)[:12]],
            "",
            f"Due by: {ctx.get('requirements_deadline') or 'not specified'}",
        ],
        "ctas": [("Complete verification", onboarding)],
    }


def _stripe_ready(ctx: Mapping[str, Any]) -> Dict[str, Any]:
    first = _esc(ctx.get("seller_first_name") or "there")
    payments = _url(ctx.get("seller_payments_url") or SELLER_PAYMENTS_PATH)
    return {
        "subject": "Your store can now accept card payments",
        "title": "You are ready to get paid",
        "preheader": "Stripe finished verifying your account. Card payments and payouts are on.",
        "blocks": [
            _paragraph(f"Hi {first},"),
            _paragraph(
                "Stripe finished verifying your account. Buyers can now pay by card on your listings, and "
                "your earnings will be paid out to the bank account you gave Stripe."
            ),
            _status_table(_seller_status_rows(ctx)),
            _button("Open payments & payouts", payments),
            _heading("How you get paid"),
            _paragraph(
                "A buyer pays %s at checkout. Once the order is fulfilled and its protection window closes, "
                "your earnings become eligible and are transferred to your Stripe account, which then pays "
                "them out to your bank on your payout schedule. You can watch every stage in Payments &amp; "
                "Payouts." % PRODUCT_NAME
            ),
            _notice(STRIPE_TRANSPARENCY),
        ],
        "text": [
            f"Hi {ctx.get('seller_first_name') or 'there'},",
            "",
            "Stripe finished verifying your account. Buyers can now pay by card on your listings, and your "
            "earnings will be paid out to the bank account you gave Stripe.",
        ],
        "ctas": [("Open payments & payouts", payments)],
    }


def _payout_failed(ctx: Mapping[str, Any]) -> Dict[str, Any]:
    first = _esc(ctx.get("seller_first_name") or "there")
    payments = _url(ctx.get("seller_payments_url") or SELLER_PAYMENTS_PATH)
    return {
        "subject": "Action required: your payout could not be completed",
        "title": "Your payout could not be completed",
        "preheader": "Stripe needs updated payout details before it can try again.",
        "blocks": [
            _paragraph(f"Hi {first},"),
            _paragraph(
                "A payout to your bank account did not go through. Your money is not lost — it stays in your "
                "Stripe balance until a payout succeeds."
            ),
            _facts(
                [
                    ("Amount", money(ctx.get("amount_cents"), ctx.get("currency") or "USD")),
                    ("Destination", ctx.get("destination_masked") or ""),
                    ("Attempted", ctx.get("failed_at") or ""),
                    ("Reason", ctx.get("failure_reason") or "Stripe did not give a specific reason."),
                ]
            ),
            _notice(
                "This usually means the bank details need updating, or Stripe needs more information about "
                "the account.",
                tone="action",
            ),
            _button("Fix payout details", payments),
            _notice(SECURITY_NOTICE, tone="action"),
        ],
        "text": [
            f"Hi {ctx.get('seller_first_name') or 'there'},",
            "",
            "A payout to your bank account did not go through. Your money is not lost - it stays in your "
            "Stripe balance until a payout succeeds.",
            "",
            f"Amount: {money(ctx.get('amount_cents'), ctx.get('currency') or 'USD')}",
            f"Reason: {ctx.get('failure_reason') or 'Stripe did not give a specific reason.'}",
        ],
        "ctas": [("Fix payout details", payments)],
    }


def _payout_paid(ctx: Mapping[str, Any]) -> Dict[str, Any]:
    payments = _url(ctx.get("seller_payments_url") or SELLER_PAYMENTS_PATH)
    return {
        "subject": f"{money(ctx.get('amount_cents'), ctx.get('currency') or 'USD')} is on its way to your bank",
        "title": "Payout sent",
        "preheader": "Stripe has sent this payout to your bank account.",
        "blocks": [
            _paragraph(
                f"Hi {_esc(ctx.get('seller_first_name') or 'there')}, your earnings have been paid out."
            ),
            _facts(
                [
                    ("Amount", money(ctx.get("amount_cents"), ctx.get("currency") or "USD")),
                    ("Destination", ctx.get("destination_masked") or ""),
                    ("Expected arrival", ctx.get("arrival_date") or ""),
                    ("Payout reference", ctx.get("payout_reference") or ""),
                ]
            ),
            _paragraph(
                "Banks usually post the money within a couple of business days of Stripe sending it."
            ),
            _button("View payouts", payments, primary=False),
        ],
        "text": [
            "Your earnings have been paid out.",
            "",
            f"Amount: {money(ctx.get('amount_cents'), ctx.get('currency') or 'USD')}",
            f"Expected arrival: {ctx.get('arrival_date') or 'not specified'}",
        ],
        "ctas": [("View payouts", payments)],
    }


def _seller_account_restricted(ctx: Mapping[str, Any]) -> Dict[str, Any]:
    payments = _url(ctx.get("seller_payments_url") or SELLER_PAYMENTS_PATH)
    reason = _esc(ctx.get("failure_reason") or ctx.get("disabled_reason") or "")
    return {
        "subject": "Your payment account has been restricted",
        "title": "Your payment account is restricted",
        "preheader": "Card payments and payouts are paused until this is resolved.",
        "blocks": [
            _paragraph(
                f"Hi {_esc(ctx.get('seller_first_name') or 'there')}, Stripe has restricted your payment "
                "account. Card payments and payouts are paused until it is resolved."
            ),
            _notice(f"<strong style=\"color:{HEADING}\">Reason:</strong> {reason}", tone="bad") if reason else "",
            _status_table(_seller_status_rows(ctx)),
            _paragraph(
                "Orders already paid for are not cancelled by this. Money already in your Stripe balance "
                "stays there until the restriction is lifted."
            ),
            _button("Resolve with Stripe", payments),
            _notice(SECURITY_NOTICE, tone="action"),
        ],
        "text": [
            "Stripe has restricted your payment account. Card payments and payouts are paused until it is "
            "resolved.",
            "",
            f"Reason: {ctx.get('failure_reason') or ctx.get('disabled_reason') or 'not specified'}",
        ],
        "ctas": [("Resolve with Stripe", payments)],
    }


def _new_paid_order(ctx: Mapping[str, Any]) -> Dict[str, Any]:
    order_url = _url(ctx.get("order_url") or SELLER_ORDERS_PATH)
    return {
        "subject": f"New paid order — {_esc(ctx.get('item_summary') or 'your listing')}",
        "title": "You have a new paid order",
        "preheader": "Payment is confirmed. Fulfil the order to move it forward.",
        "blocks": [
            _paragraph(
                f"Hi {_esc(ctx.get('seller_first_name') or 'there')}, a buyer has paid for an order on your store."
            ),
            _facts(
                [
                    ("Order", ctx.get("order_reference") or ""),
                    ("Item", ctx.get("item_summary") or ""),
                    ("Order total", money(ctx.get("amount_cents"), ctx.get("currency") or "USD")),
                    ("Your net earnings", money(ctx.get("seller_net_cents"), ctx.get("currency") or "USD")),
                    ("Placed", ctx.get("placed_at") or ""),
                ]
            ),
            _button("View order", order_url),
            _paragraph(
                "Earnings become eligible for payout after the order is fulfilled and its protection window "
                "closes.",
                color=MUTED,
                size=13,
            ),
        ],
        "text": [
            "A buyer has paid for an order on your store.",
            "",
            f"Order: {ctx.get('order_reference') or ''}",
            f"Item: {ctx.get('item_summary') or ''}",
            f"Order total: {money(ctx.get('amount_cents'), ctx.get('currency') or 'USD')}",
        ],
        "ctas": [("View order", order_url)],
    }


def _dispute_opened(ctx: Mapping[str, Any]) -> Dict[str, Any]:
    order_url = _url(ctx.get("order_url") or SELLER_ORDERS_PATH)
    return {
        "subject": "A buyer has disputed a payment on your store",
        "title": "A payment has been disputed",
        "preheader": "The payout for this order is on hold while the dispute is open.",
        "blocks": [
            _paragraph(
                f"Hi {_esc(ctx.get('seller_first_name') or 'there')}, a buyer's bank has disputed a payment "
                "on one of your orders. This is called a chargeback."
            ),
            _facts(
                [
                    ("Order", ctx.get("order_reference") or ""),
                    ("Disputed amount", money(ctx.get("amount_cents"), ctx.get("currency") or "USD")),
                    ("Reason given", ctx.get("dispute_reason") or ""),
                    ("Respond by", ctx.get("evidence_due_by") or ""),
                ]
            ),
            _notice(
                f"<strong style=\"color:{HEADING}\">The payout for this order is on hold</strong> while the "
                "dispute is open. If the dispute is resolved in your favour, it is released.",
                tone="action",
            ),
            _button("Review dispute", order_url),
        ],
        "text": [
            "A buyer's bank has disputed a payment on one of your orders.",
            "",
            f"Order: {ctx.get('order_reference') or ''}",
            f"Disputed amount: {money(ctx.get('amount_cents'), ctx.get('currency') or 'USD')}",
            f"Respond by: {ctx.get('evidence_due_by') or 'not specified'}",
            "",
            "The payout for this order is on hold while the dispute is open.",
        ],
        "ctas": [("Review dispute", order_url)],
    }


def _dispute_won(ctx: Mapping[str, Any]) -> Dict[str, Any]:
    order_url = _url(ctx.get("order_url") or SELLER_ORDERS_PATH)
    return {
        "subject": "A disputed payment was resolved in your favour",
        "title": "The dispute was resolved in your favour",
        "preheader": "The payout hold on this order has been lifted.",
        "blocks": [
            _paragraph(
                f"Hi {_esc(ctx.get('seller_first_name') or 'there')}, the bank has decided a disputed "
                "payment on one of your orders in your favour. Nothing is being taken back."
            ),
            _facts(
                [
                    ("Order", ctx.get("order_reference") or ""),
                    ("Amount", money(ctx.get("amount_cents"), ctx.get("currency") or "USD")),
                    ("Reason given", ctx.get("dispute_reason") or ""),
                ]
            ),
            _notice(
                f"<strong style=\"color:{HEADING}\">The payout hold has been lifted</strong> and this "
                "order returns to its normal payout schedule.",
                tone="ok",
            ),
            _button("View order", order_url),
        ],
        "text": [
            "A disputed payment on one of your orders was resolved in your favour.",
            "",
            f"Order: {ctx.get('order_reference') or ''}",
            f"Amount: {money(ctx.get('amount_cents'), ctx.get('currency') or 'USD')}",
            "",
            "The payout hold has been lifted and this order returns to its normal payout schedule.",
        ],
        "ctas": [("View order", order_url)],
    }


def _dispute_lost(ctx: Mapping[str, Any]) -> Dict[str, Any]:
    order_url = _url(ctx.get("order_url") or SELLER_ORDERS_PATH)
    return {
        "subject": "A disputed payment was decided against you",
        "title": "The dispute was decided against you",
        "preheader": "The disputed amount has been taken back from your earnings.",
        "blocks": [
            _paragraph(
                f"Hi {_esc(ctx.get('seller_first_name') or 'there')}, the bank has decided a disputed "
                "payment on one of your orders in the buyer's favour. The money has been returned to "
                "them and is no longer payable to you."
            ),
            _facts(
                [
                    ("Order", ctx.get("order_reference") or ""),
                    ("Taken back", money(ctx.get("amount_cents"), ctx.get("currency") or "USD")),
                    ("Reason given", ctx.get("dispute_reason") or ""),
                ]
            ),
            _notice(
                f"<strong style=\"color:{HEADING}\">Your earnings have been adjusted</strong> by this "
                "amount. If the order had already been paid out, it is recovered from later earnings.",
                tone="bad",
            ),
            _button("View order", order_url),
        ],
        "text": [
            "A disputed payment on one of your orders was decided in the buyer's favour.",
            "",
            f"Order: {ctx.get('order_reference') or ''}",
            f"Taken back: {money(ctx.get('amount_cents'), ctx.get('currency') or 'USD')}",
            "",
            "Your earnings have been adjusted by this amount. If the order had already been paid "
            "out, it is recovered from later earnings.",
        ],
        "ctas": [("View order", order_url)],
    }


def _refund_completed(ctx: Mapping[str, Any]) -> Dict[str, Any]:
    order_url = _url(ctx.get("order_url") or "/pulse/orders")
    return {
        "subject": f"Your refund of {money(ctx.get('amount_cents'), ctx.get('currency') or 'USD')} is on its way",
        "title": "Your refund has been issued",
        "preheader": "The refund is on its way back to your original payment method.",
        "blocks": [
            _paragraph(f"Hi {_esc(ctx.get('buyer_first_name') or 'there')}, your refund has been issued."),
            _facts(
                [
                    ("Order", ctx.get("order_reference") or ""),
                    ("Refunded", money(ctx.get("amount_cents"), ctx.get("currency") or "USD")),
                    ("Back to", ctx.get("payment_method_masked") or "your original payment method"),
                    ("Issued", ctx.get("refunded_at") or ""),
                ]
            ),
            _paragraph(
                "Refunds are returned to the card or payment method used at checkout. Most banks post the "
                "money within five to ten business days."
            ),
            _button("View order", order_url, primary=False),
        ],
        "text": [
            "Your refund has been issued.",
            "",
            f"Refunded: {money(ctx.get('amount_cents'), ctx.get('currency') or 'USD')}",
            "",
            "Refunds are returned to the card or payment method used at checkout. Most banks post the money "
            "within five to ten business days.",
        ],
        "ctas": [("View order", order_url)],
    }


def _payment_succeeded(ctx: Mapping[str, Any]) -> Dict[str, Any]:
    receipt_url = _url(ctx.get("receipt_url") or "/pulse/orders")
    return {
        "subject": f"Your {PRODUCT_NAME} order is confirmed",
        "title": "Your order is confirmed",
        "preheader": "Payment received. Here is your receipt.",
        "blocks": [
            _paragraph(f"Hi {_esc(ctx.get('buyer_first_name') or 'there')}, your payment went through."),
            _facts(
                [
                    ("Order", ctx.get("order_reference") or ""),
                    ("Seller", ctx.get("store_name") or ""),
                    ("Item", ctx.get("item_summary") or ""),
                    ("Subtotal", money(ctx.get("subtotal_cents"), ctx.get("currency") or "USD")),
                    ("Shipping", money(ctx.get("shipping_cents"), ctx.get("currency") or "USD")),
                    ("Tax", money(ctx.get("tax_cents"), ctx.get("currency") or "USD")),
                    ("Total paid", money(ctx.get("amount_cents"), ctx.get("currency") or "USD")),
                    ("Paid with", ctx.get("payment_method_masked") or ""),
                    ("Paid", ctx.get("paid_at") or ""),
                ]
            ),
            _button("View receipt", receipt_url),
            _notice(STRIPE_TRANSPARENCY),
        ],
        "text": [
            "Your payment went through.",
            "",
            f"Order: {ctx.get('order_reference') or ''}",
            f"Total paid: {money(ctx.get('amount_cents'), ctx.get('currency') or 'USD')}",
        ],
        "ctas": [("View receipt", receipt_url)],
    }


def _order_shipped(ctx: Mapping[str, Any]) -> Dict[str, Any]:
    order_url = _url(ctx.get("order_url") or "/pulse/orders")
    return {
        "subject": "Your order has shipped",
        "title": "Your order is on the way",
        "preheader": "The seller has marked your order as shipped.",
        "blocks": [
            _paragraph(
                f"Hi {_esc(ctx.get('buyer_first_name') or 'there')}, "
                f"{_esc(ctx.get('store_name') or 'the seller')} has shipped your order."
            ),
            _facts(
                [
                    ("Order", ctx.get("order_reference") or ""),
                    ("Item", ctx.get("item_summary") or ""),
                    ("Carrier", ctx.get("carrier") or ""),
                    ("Tracking", ctx.get("tracking_reference") or ""),
                ]
            ),
            _button("Track order", order_url),
        ],
        "text": [
            "The seller has shipped your order.",
            "",
            f"Order: {ctx.get('order_reference') or ''}",
            f"Tracking: {ctx.get('tracking_reference') or 'not provided'}",
        ],
        "ctas": [("Track order", order_url)],
    }


#: template key -> builder. The keys are the domain event names used by
#: ``services/payments_notifications``, so a reader can follow one string from
#: the webhook that fired to the pixels the seller sees.
TEMPLATES: Dict[str, SpecBuilder] = {
    "seller_application_received": _seller_application_received,
    "seller_more_info_required": _seller_more_info_required,
    "seller_approved": _seller_approved,
    "seller_declined": _seller_declined,
    "stripe_verification_required": _stripe_verification_required,
    "stripe_ready": _stripe_ready,
    "card_payments_enabled": _stripe_ready,
    "payout_paid": _payout_paid,
    "payout_failed": _payout_failed,
    "seller_account_restricted": _seller_account_restricted,
    "new_paid_order": _new_paid_order,
    "dispute_opened": _dispute_opened,
    "dispute_action_required": _dispute_opened,
    "dispute_won": _dispute_won,
    "dispute_lost": _dispute_lost,
    "refund_completed": _refund_completed,
    "payment_succeeded": _payment_succeeded,
    "order_shipped": _order_shipped,
}


def render(template_key: str, context: Mapping[str, Any] | None = None) -> Dict[str, str]:
    """Render one payment email.

    Returns ``{"subject", "html", "text"}``. Raises :class:`KeyError` for an
    unknown key — deliberately, because the alternative is mailing a seller a
    blank page about their money. The caller that queues these owns the decision
    about what an unknown event does, and it can see the event name; this module
    cannot.
    """
    ctx = dict(context or {})
    spec = TEMPLATES[str(template_key)](ctx)
    return {
        "subject": str(spec["subject"])[:200],
        "html": _layout(title=spec["title"], preheader=spec.get("preheader") or "", blocks=spec["blocks"]),
        "text": _plain_text(
            title=spec["title"],
            lines=spec.get("text") or [],
            ctas=spec.get("ctas") or [],
        ),
    }


def template_keys() -> tuple:
    """Every renderable key, for the audit script and the notification matrix."""
    return tuple(sorted(TEMPLATES))
