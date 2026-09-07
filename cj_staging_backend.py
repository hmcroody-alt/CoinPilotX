"""Real canonical backend, isolated staging-only entry point."""
from cj_staging_runtime import guard, health, configure_http

guard()  # Refuse production before importing application startup hooks.
import bot
app = bot.app
configure_http(bot)
from flask import jsonify


@app.get("/health/cj-staging")
def staging_health():
    payload, status = health()
    response = jsonify(payload)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response, status
