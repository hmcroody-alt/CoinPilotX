import os
import requests


def send_brevo_email(to_email, subject, text_body, html_body="", from_email=None, from_name=None):
    api_key = os.getenv("BREVO_API_KEY")
    if not api_key:
        return {"ok": False, "status_code": None, "response": {"message": "BREVO_API_KEY is not loaded."}}
    response = requests.post(
        "https://api.brevo.com/v3/smtp/email",
        headers={"api-key": api_key, "Content-Type": "application/json", "Accept": "application/json"},
        json={
            "sender": {"email": from_email or os.getenv("MAIL_FROM_ADDRESS", "support@coinpilotx.app"), "name": from_name or os.getenv("MAIL_FROM_NAME", "CoinPilotXAI Inc.")},
            "to": [{"email": to_email}],
            "subject": subject,
            "textContent": text_body,
            "htmlContent": html_body or text_body.replace("\n", "<br>"),
        },
        timeout=15,
    )
    try:
        body = response.json() if response.text else {}
    except Exception:
        body = {"raw": response.text}
    return {"ok": 200 <= response.status_code < 300, "status_code": response.status_code, "response": body}
