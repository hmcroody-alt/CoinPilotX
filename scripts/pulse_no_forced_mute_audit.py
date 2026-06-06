#!/usr/bin/env python3
"""Compatibility entrypoint for the Pulse no-forced-mute audit."""

import runpy
from pathlib import Path


runpy.run_path(str(Path(__file__).resolve().with_name("pulse_video_no_forced_mute_audit.py")), run_name="__main__")
