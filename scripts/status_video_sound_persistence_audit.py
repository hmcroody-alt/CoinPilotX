#!/usr/bin/env python3
"""Compatibility entrypoint for the Status video sound persistence audit."""

import runpy
from pathlib import Path


runpy.run_path(str(Path(__file__).resolve().with_name("status_video_sound_audit.py")), run_name="__main__")
