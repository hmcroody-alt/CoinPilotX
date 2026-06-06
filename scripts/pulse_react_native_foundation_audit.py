#!/usr/bin/env python3
"""Compatibility wrapper for the React Native foundation audit."""

import runpy
from pathlib import Path


runpy.run_path(str(Path(__file__).with_name("pulse_native_app_foundation_audit.py")), run_name="__main__")
