"""The one authority on how long a stored video may be, per surface.

Before this module every surface answered the question separately and none of
them answered it on the server: the Messenger picker capped clips at 120s, the
feed/Reels picker at 180s, Status at 60s, and the backend at nothing at all.
Client-side `videoMaxDuration` is a courtesy to the person choosing a file, not
an enforcement point -- anything that talks to the API directly bypassed it
entirely.

Scope: stored/uploaded video only. Live streaming and RTC call length are a
different subsystem with different owners and are deliberately not represented
here.

## Why unknown surfaces get the strictest cap

`max_duration_seconds` is a *ceiling*. Defaulting an unrecognised name to a
permissive value inverts the protection -- a typo in a surface name would quietly
buy the caller the longest limit in the table instead of the shortest. So the
fallback is the strictest cap, and callers that need to distinguish "this surface
is deliberately short" from "nobody registered this surface" use
`is_known_surface`.
"""

from __future__ import annotations

import os

# The platform maximum. Raising this is a product decision about storage,
# processing time and bandwidth -- not just a number.
MAX_STORED_VIDEO_SECONDS = 90 * 60  # 5400

# Surfaces whose short limit is a product decision rather than a technical one.
# Status is ephemeral short-form; lifting it to 90 minutes would change what the
# surface *is*, so it keeps its own cap until someone asks for that explicitly.
SHORT_FORM_SECONDS = 60
MARKETPLACE_SECONDS = 10 * 60

_SURFACE_SECONDS: dict[str, int] = {
    "messenger": MAX_STORED_VIDEO_SECONDS,
    "comm_v2": MAX_STORED_VIDEO_SECONDS,
    "post": MAX_STORED_VIDEO_SECONDS,
    "feed": MAX_STORED_VIDEO_SECONDS,
    "reel": MAX_STORED_VIDEO_SECONDS,
    "status": SHORT_FORM_SECONDS,
    "pulse_status": SHORT_FORM_SECONDS,
    "marketplace": MARKETPLACE_SECONDS,
    "seller_listing": MARKETPLACE_SECONDS,
    "ad_creative": MARKETPLACE_SECONDS,
}

# Per-surface env overrides, so operations can tighten a surface without a deploy.
_SURFACE_ENV: dict[str, str] = {
    "messenger": "MESSENGER_VIDEO_MAX_SECONDS",
    "post": "POST_VIDEO_MAX_SECONDS",
    "reel": "REEL_VIDEO_MAX_SECONDS",
    "status": "STATUS_VIDEO_MAX_SECONDS",
}

# The keys here are the `context_type` strings the product actually sends, not
# tidy names invented for this table. Getting one wrong is not a cosmetic miss:
# an unregistered name falls through to the strictest cap, so mapping "pulse"
# (what every feed composer posts) would silently cap feed video at 60 seconds
# while looking like a deliberate product decision. Before adding a surface,
# grep for the literal that reaches the server.
_ALIASES: dict[str, str] = {
    "photo": "post",
    "video": "post",
    "pulse": "post",
    "pulse_video": "post",
    "pulse_post": "post",
    "pulse_camera": "post",
    "pulse_group": "post",
    "creator_studio": "post",
    "reels": "reel",
    "pulse_reel": "reel",
    "chat": "messenger",
    "message": "messenger",
    "private_message": "messenger",
    "private_chat": "messenger",
    "pulse_message": "messenger",
    "marketplace_product": "marketplace",
    "pulse_ad_creative": "ad_creative",
}


def _canonical(surface: str) -> str:
    name = str(surface or "").strip().lower()
    # comm_v2 context types carry a conversation discriminator on the end
    # ("pulse_comm_v2_direct", "pulse_comm_v2_group"), so they cannot be
    # enumerated as aliases -- a new conversation kind would land on the
    # strictest cap and shorten Messenger without anyone changing this file.
    if name.startswith("pulse_comm_v2"):
        return "comm_v2"
    return _ALIASES.get(name, name)


def is_known_surface(surface: str) -> bool:
    return _canonical(surface) in _SURFACE_SECONDS


# Surfaces whose uploads are published through pulse_posts, and therefore possibly
# through a pulse_reels row that keeps its own copy of the video URL. Blocking the
# upload is not enough for these: the copy outlives the block.
_FEED_SURFACES = frozenset({"post", "feed", "reel"})


def publishes_through_reels(surface: str) -> bool:
    """Whether blocking an upload on this surface can leave a Reel playable.

    A feed upload's `context_id` is the post id, and `pulse_reels.video_url` is a
    denormalized copy of the playback URL that overrides the post's withheld media
    on read. So the caller has a second row to take down, and only for these
    surfaces -- a messenger upload's `context_id` is a message id, and reading it
    as a post id would block an unrelated stranger's Reel.
    """
    return _canonical(surface) in _FEED_SURFACES


def strictest_seconds() -> int:
    return min(_SURFACE_SECONDS.values())


def max_duration_seconds(surface: str) -> int:
    """Return the duration ceiling for a surface, in whole seconds."""
    name = _canonical(surface)
    if name not in _SURFACE_SECONDS:
        return strictest_seconds()
    configured = _SURFACE_SECONDS[name]
    env_name = _SURFACE_ENV.get(name)
    if env_name:
        raw = os.getenv(env_name, "")
        if raw:
            try:
                override = int(float(raw))
            except (TypeError, ValueError):
                override = 0
            # An override may only tighten. A misconfigured variable must not be
            # able to lift a surface above the platform maximum.
            if 0 < override <= MAX_STORED_VIDEO_SECONDS:
                configured = override
    return int(configured)


def max_duration_ms(surface: str) -> int:
    return max_duration_seconds(surface) * 1000


def exceeds_limit(surface: str, duration_seconds: float | int | None) -> bool:
    """True when a known duration is over the surface's ceiling.

    An unknown duration (None/0/negative) is not a violation -- it is an absent
    measurement, and refusing on absence would reject every upload whose duration
    is only discovered after the bytes land.
    """
    try:
        seconds = float(duration_seconds or 0)
    except (TypeError, ValueError):
        return False
    if seconds <= 0:
        return False
    # Whole-second comparison so that 90:00 exactly is accepted and 90:01 is not.
    # A float 5400.4 arriving from a container's metadata is still 90:00.
    return int(seconds) > max_duration_seconds(surface)


def declared_seconds(duration_ms: float | int | str | None) -> float:
    """Read a client's duration claim, in the unit the wire uses.

    Every upload path declares `duration_ms` in milliseconds, because that is the
    unit both `expo-image-picker` and the browser's HTMLMediaElement report.
    Converting on the client is precisely where a factor of 1000 would hide, and
    the mistake fails open: 5400 read as milliseconds is 5.4 seconds, which passes
    every check. So the conversion lives here once rather than at each call site.
    """
    try:
        return max(0.0, float(duration_ms or 0)) / 1000.0
    except (TypeError, ValueError):
        return 0.0


def exceeds_limit_ms(surface: str, duration_ms: float | int | str | None) -> bool:
    return exceeds_limit(surface, declared_seconds(duration_ms))


def limit_message(surface: str) -> str:
    seconds = max_duration_seconds(surface)
    if seconds % 60 == 0:
        minutes = seconds // 60
        unit = "minute" if minutes == 1 else "minutes"
        return f"Videos can be up to {minutes} {unit} long."
    return f"Videos can be up to {seconds} seconds long."


# The code every surface records when a *measured* duration breaks the ceiling.
# Distinct from the upload-time refusal only in where it is written, never in
# what it means -- a reader comparing a blocked asset against a refused upload
# should not have to work out whether two spellings are the same rule.
MEASURED_REJECTION_CODE = "video_too_long"


def measured_violation(surface: str, duration_seconds: float | int | None) -> str:
    """The reason to record when a measurement breaks the ceiling, else "".

    Why a reason string rather than a bool: the measurement arrives after the
    bytes are already stored, so every call site has to *write down* why it took
    an asset away from its owner. Handing back the sentence keeps that wording
    identical across the Mux webhook, the Mux reconciler and the worker's ffprobe
    pass, and makes the call site `if reason:` -- which cannot be misread the way
    `if not within_limit(...)` can.

    Absence is not a violation. A measurement of 0/None means nobody has measured
    yet, and treating that as over-long would block every video in the window
    between finalize and the first probe. The distinction lives in
    `exceeds_limit`; this function must not add a second opinion about it.

    An unregistered surface is not a violation either, and this is the one place
    that departs from the strictest-cap fallback. That fallback is right when the
    question is "may this upload start" -- the caller still has its bytes and gets
    an error. It inverts once the question is "take this stored video away from its
    owner", because `context_type` is free-form data written by many call sites:
    `asset_focus`, `native` and `pulse_comment` all reach this table today without
    being in the table above, so convicting on the 60s fallback would silently
    delete valid video on a name nobody registered. Refusing to judge leaves the
    asset alone and the surface visible in the log.
    """
    if not is_known_surface(surface):
        return ""
    if not exceeds_limit(surface, duration_seconds):
        return ""
    try:
        measured = int(float(duration_seconds or 0))
    except (TypeError, ValueError):
        measured = 0
    return f"Video runs {measured}s, over the {max_duration_seconds(surface)}s limit for this surface."


def measured_violation_ms(surface: str, duration_ms: float | int | str | None) -> str:
    return measured_violation(surface, declared_seconds(duration_ms))
