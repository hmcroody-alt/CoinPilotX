"""Feature flags for Pulse Communications 2.0."""

from __future__ import annotations

import os


FLAG_NAME = "PULSE_COMMUNICATIONS_V2_ENABLED"
VOICE_NOTES_FLAG = "PULSE_VOICE_NOTES_ENABLED"
AUDIO_CALLS_FLAG = "PULSE_AUDIO_CALLS_ENABLED"
VIDEO_CALLS_FLAG = "PULSE_VIDEO_CALLS_ENABLED"
GROUP_CALLS_FLAG = "PULSE_GROUP_CALLS_ENABLED"


def is_enabled() -> bool:
    return os.getenv(FLAG_NAME, "true").strip().lower() in {"1", "true", "yes", "on"}


def subflag_enabled(name: str, default: bool = False) -> bool:
    fallback = "true" if default else "false"
    return os.getenv(name, fallback).strip().lower() in {"1", "true", "yes", "on"}


PULSE_COMMUNICATIONS_V2_ENABLED = is_enabled()
PULSE_VOICE_NOTES_ENABLED = subflag_enabled(VOICE_NOTES_FLAG)
PULSE_AUDIO_CALLS_ENABLED = subflag_enabled(AUDIO_CALLS_FLAG)
PULSE_VIDEO_CALLS_ENABLED = subflag_enabled(VIDEO_CALLS_FLAG)
PULSE_GROUP_CALLS_ENABLED = subflag_enabled(GROUP_CALLS_FLAG)
