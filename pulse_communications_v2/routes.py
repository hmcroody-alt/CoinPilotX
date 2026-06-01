"""Disabled Pulse Communications 2.0 route blueprint."""

from __future__ import annotations

from flask import Blueprint, jsonify

from .flags import PULSE_COMMUNICATIONS_V2_ENABLED


comm_v2_blueprint = Blueprint("pulse_communications_v2", __name__)


@comm_v2_blueprint.get("/api/pulse/comm/v2/health")
def health():
    if not PULSE_COMMUNICATIONS_V2_ENABLED:
        return jsonify({"enabled": False, "status": "disabled"})
    return jsonify({"enabled": True, "status": "ready"})


def register(app) -> None:
    app.register_blueprint(comm_v2_blueprint)
