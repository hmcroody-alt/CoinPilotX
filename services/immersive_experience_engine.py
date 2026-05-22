"""Immersive experience primitives."""

from __future__ import annotations


def panel_layout(mode: str = "pulse") -> dict:
    return {
        "mode": mode,
        "supports_overlays": True,
        "supports_spatial_rooms": True,
        "transition": "cinematic_soft",
        "mobile_first": True,
    }
