"""Scene, overlay, and transition presets for Pulse Live Studio."""

from __future__ import annotations

SCENES = [
    {"key": "camera_only", "label": "Camera only", "aspect": "16:9", "layers": ["camera", "lower_third"]},
    {"key": "split_screen", "label": "Split screen", "aspect": "16:9", "layers": ["camera", "guest", "lower_third"]},
    {"key": "interview", "label": "Interview mode", "aspect": "16:9", "layers": ["host", "guest", "chat_highlight"]},
    {"key": "podcast", "label": "Podcast mode", "aspect": "16:9", "layers": ["camera", "audio_meter", "topic_card"]},
    {"key": "screen_share", "label": "Screen share", "aspect": "16:9", "layers": ["screen", "camera_pip"]},
    {"key": "vertical_live", "label": "Vertical live", "aspect": "9:16", "layers": ["camera", "floating_chat", "reaction_rail"]},
    {"key": "gaming", "label": "Gaming", "aspect": "16:9", "layers": ["screen", "camera_pip", "alerts"]},
    {"key": "brb", "label": "BRB", "aspect": "16:9", "layers": ["ambient", "countdown"]},
    {"key": "starting_soon", "label": "Starting soon", "aspect": "16:9", "layers": ["ambient", "countdown", "music"]},
    {"key": "ending_soon", "label": "Ending soon", "aspect": "16:9", "layers": ["ambient", "replay_prompt"]},
]

TRANSITIONS = [
    {"key": "soft_cut", "label": "Soft cut", "duration_ms": 180},
    {"key": "cinematic_fade", "label": "Cinematic fade", "duration_ms": 420},
    {"key": "pulse_sweep", "label": "Pulse sweep", "duration_ms": 520},
]


def scene_catalog() -> list[dict]:
    return [dict(scene) for scene in SCENES]


def transition_catalog() -> list[dict]:
    return [dict(transition) for transition in TRANSITIONS]


def default_scene_state(active: str = "camera_only") -> dict:
    scene = next((item for item in SCENES if item["key"] == active), SCENES[0])
    return {
        "active_scene": scene,
        "available_scenes": scene_catalog(),
        "transitions": transition_catalog(),
        "overlays": ["animated_lower_third", "live_badge", "viewer_count", "reaction_bursts", "countdown"],
        "scene_memory": True,
    }
