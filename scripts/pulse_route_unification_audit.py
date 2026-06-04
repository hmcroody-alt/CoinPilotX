#!/usr/bin/env python3
"""Audit that logged-in product routes converge on Pulse."""
from pathlib import Path
S = (Path(__file__).resolve().parents[1] / "bot.py").read_text(encoding="utf-8")
for token in ['return redirect(safe_redirect_target("pulse_page"))', 'return redirect("/pulse/premium/intelligence", code=302)', '@webhook_app.route("/pulse/roast-battle"', '@webhook_app.route("/pulse/videos"', '@webhook_app.route("/pulse/saved"']:
    assert token in S, token
    print("PASS:", token)
print("pulse route unification audit ok")
