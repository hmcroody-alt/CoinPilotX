"""Real canonical backend, isolated staging-only entry point."""
from cj_staging_runtime import guard, health

guard()  # Refuse production before importing application startup hooks.
from bot import app
from flask import jsonify


@app.get("/health/cj-staging")
def staging_health():
    payload, status = health()
    response = jsonify(payload)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response, status
