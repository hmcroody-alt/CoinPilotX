from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
service = (ROOT / "services" / "notification_service.py").read_text()
email_service = (ROOT / "services" / "email_service.py").read_text()

checks = {
    "brevo service": "BREVO" in email_service or "api.brevo.com" in email_service,
    "email channel": '"email"' in service and "email_service.send_email" in service,
    "security category email": '"security": {"in_app": True, "push": True, "email": True' in service,
    "premium category email": '"premium": {"in_app": True, "push": True, "email": True' in service,
    "pulsesoc senders": "support@pulsesoc.com" in email_service or "PulseSoc" in email_service,
}

failed = [name for name, ok in checks.items() if not ok]
for name, ok in checks.items():
    print(("ok" if ok else "FAIL") + f" - {name}")
if failed:
    raise SystemExit("Pulse notification email audit failed: " + ", ".join(failed))
print("pulse notification email audit ok")
