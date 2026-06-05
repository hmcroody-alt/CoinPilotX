#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")
SERVICE = (ROOT / "services/mux_live_service.py").read_text(encoding="utf-8")

def expect(ok, label):
    if not ok:
        raise AssertionError(label)
    print(f"ok - {label}")

def main():
    for token in ["Only the host can view Mux ingest details", "stream_key", "is_host", "admin_current_user"]:
        expect(token in BOT, f"live security includes {token}")
    for token in ["token_id_configured", "token_secret_configured", "webhook_secret_configured"]:
        expect(token in SERVICE, f"safe mux diagnostics include {token}")
    print("pulse mux live security audit ok")

if __name__ == "__main__":
    main()
