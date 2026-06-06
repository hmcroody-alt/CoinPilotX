#!/usr/bin/env python3
"""Compatibility entrypoint for the feed video sound persistence audit."""

import runpy
from pathlib import Path


runpy.run_path(str(Path(__file__).resolve().with_name("pulse_feed_sound_persistence_audit.py")), run_name="__main__")
