"""The per-request size ceiling on proxied media uploads.

This guard is the only size check that happens *before* the body is
transferred. Every other limit in the media stack reads `file_storage.stream`,
which means the bytes have already crossed the wire and been spooled by the
time the limit is applied -- so a ceiling set too high does not merely fail, it
fails slowly, which is the complaint this exists to prevent.
"""

import os
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BOT_SOURCE = (ROOT / "bot.py").read_text()

# Gunicorn's worker timeout, from the Procfile. A proxied request that cannot
# plausibly finish inside it is work the server has agreed to do and cannot.
WORKER_TIMEOUT_SECONDS = 120


def procfile_worker_timeout():
    match = re.search(r"--timeout\s+(\d+)", (ROOT / "Procfile").read_text())
    assert match, "Procfile no longer declares a worker timeout"
    return int(match.group(1))


def pulse_media_ceiling_expression():
    """The source of the ceiling for `/api/pulse/media/upload`."""
    match = re.search(
        r'elif request\.path == "/api/pulse/media/upload":.*?max_request_mb = ([^\n]+)',
        BOT_SOURCE,
        flags=re.S,
    )
    assert match, "the /api/pulse/media/upload ceiling branch is gone"
    return match.group(1).strip()


def test_the_worker_timeout_is_still_what_this_file_assumes():
    # Everything below reasons about bytes-per-second against this number. If the
    # Procfile changes it, the arithmetic here is stale and should be revisited
    # rather than silently continuing to assert against a number nobody uses.
    assert procfile_worker_timeout() == WORKER_TIMEOUT_SECONDS


def test_the_request_ceiling_is_not_inherited_from_the_stored_video_limit():
    # These are different questions. MEDIA_UPLOAD_MAX_VIDEO_MB says how large a
    # stored video may be; production sets it to 700. Reusing it as a *request*
    # ceiling admitted a 700 MB multipart POST onto a 120s worker, which needs
    # 5.8 MB/s sustained for two minutes to survive -- so the upload was accepted,
    # transferred for as long as the link allowed, and then killed.
    expression = pulse_media_ceiling_expression()
    assert "MEDIA_UPLOAD_MAX_VIDEO_MB" not in expression, (
        "the proxied-request ceiling is reading the stored-video limit again"
    )
    assert "PULSE_MEDIA_MAX_REQUEST_MB" in expression, (
        "operators lost the override that lets them raise the proxied-request ceiling"
    )


def test_the_default_request_ceiling_can_finish_inside_the_worker_timeout():
    # Not a claim that 150 MB is fast -- a claim that the default is in a band a
    # real connection can clear before the worker is killed. At 5 MB/s (a good
    # mobile uplink) 150 MB takes 30s of the 120s budget.
    expression = pulse_media_ceiling_expression()
    default = re.search(r'"(\d+(?:\.\d+)?)"\s*\)', expression)
    assert default, f"could not read a literal default out of: {expression}"
    ceiling_mb = float(default.group(1))
    required_mb_per_second = ceiling_mb / WORKER_TIMEOUT_SECONDS
    assert required_mb_per_second <= 2.0, (
        f"the default ceiling needs {required_mb_per_second:.1f} MB/s sustained for "
        f"{WORKER_TIMEOUT_SECONDS}s, which no ordinary mobile link provides"
    )


@pytest.mark.parametrize(
    "variable",
    ["MEDIA_UPLOAD_MAX_VIDEO_MB", "MEDIA_UPLOAD_MAX_STATUS_VIDEO_MB"],
)
def test_raising_a_storage_limit_does_not_raise_the_request_ceiling(monkeypatch, variable):
    # The regression in behavioural form: turning a storage limit up must not
    # quietly widen what a single proxied request may carry.
    monkeypatch.setenv(variable, "4096")
    monkeypatch.delenv("PULSE_MEDIA_MAX_REQUEST_MB", raising=False)
    expression = pulse_media_ceiling_expression()
    resolved = eval(expression, {"os": os, "float": float})  # noqa: S307 - fixed source expression
    assert resolved <= 150.0, (
        f"{variable} moved the proxied-request ceiling to {resolved} MB"
    )
