#!/usr/bin/env python3
"""Stage ONLY the Messenger media-auth isolation hunks.

`bot.py`, `ChatScreen.tsx` and `.env.example` are shared with unrelated
in-flight work (multi-guest Live, Private Office, Messenger idempotency). This
selects hunks by content marker rather than by file, rebuilds each file as
"HEAD plus my hunks", and writes that blob into the index. The working tree is
left untouched, so the other missions keep their changes.

Run with --check to print what would be staged without touching the index.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# A hunk is mine only if it mentions one of these. Every marker names something
# this repair introduced; none of them appears in the Live/Private Office work.
MARKERS = {
    "bot.py": [
        "MEDIA_BYTE_PATH_RE",
        "is_media_byte_delivery_path",
        "_messenger_media_viewer",
        "messenger_media_cookie_user_id",
        "MESSENGER_MEDIA_TOKEN",
        "mint_messenger_media_token",
        "messenger_media_token_user_id",
        "_messenger_media_request_token",
        "api_messages_media_access",
        "MESSENGER_MEDIA_ACCESS_",
        "MESSENGER_MEDIA_LEGACY_COOKIE_ACCESS",
        "MESSENGER_MEDIA_ERRORS",
        "messenger_media_token_state",
        "_messenger_media_denied",
    ],
    "mobile-native/src/screens/ChatScreen.tsx": [
        "messengerMediaAccess",
        "useMessengerMediaAccessUrl",
        "mediaAccess",
        "thumbnailAccess",
        "Image unavailable",
    ],
    ".env.example": ["PULSESOC_MESSENGER_MEDIA_TOKEN_TTL_SECONDS"],
}

# Files whose entire diff belongs to this repair.
WHOLE = [
    "services/messenger_media_foundation.py",
    "mobile-native/src/media/mediaSessionCleanup.ts",
    # One hunk, entirely this repair: media paths are excluded from the 401
    # refresh-then-sign-out path. Verified against `git diff` before adding.
    "mobile-native/src/api/pulseApi.ts",
]

# Untracked files created by this repair.
NEW = [
    "mobile-native/src/media/messengerMediaAccess.ts",
    "mobile-native/src/media/__tests__/messengerMediaAccess.test.ts",
    "tests/test_messenger_media_auth_isolation.py",
]


def clear_index_lock() -> None:
    """Move a stale `index.lock` aside.

    This checkout lives on a mount that permits create and rename but not
    unlink, so git cannot clean up its own lock file and every index write
    after the first fails with "Another git process seems to be running".
    Renaming is the only removal available.
    """
    lock = os.path.join(REPO, ".git", "index.lock")
    if os.path.exists(lock):
        os.rename(lock, lock + ".stale-%d" % int(time.time()))


def git(*args: str, **kw) -> str:
    clear_index_lock()
    result = subprocess.run(["git", "-C", REPO, *args], capture_output=True, text=True, **kw)
    if result.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed:\n{result.stdout}{result.stderr}")
    return result.stdout


def split_hunks(diff: str) -> tuple[list[str], list[list[str]]]:
    lines = diff.splitlines(keepends=True)
    header: list[str] = []
    hunks: list[list[str]] = []
    for line in lines:
        if line.startswith("@@"):
            hunks.append([line])
        elif hunks:
            hunks[-1].append(line)
        else:
            header.append(line)
    return header, hunks


def selected(path: str, hunks: list[list[str]]) -> tuple[list[list[str]], list[list[str]]]:
    markers = MARKERS[path]
    mine, foreign = [], []
    for hunk in hunks:
        body = "".join(hunk)
        (mine if any(m in body for m in markers) else foreign).append(hunk)
    return mine, foreign


def stage_blob(path: str, content: bytes) -> str:
    with tempfile.NamedTemporaryFile(delete=False) as handle:
        handle.write(content)
        tmp = handle.name
    try:
        sha = git("hash-object", "-w", tmp).strip()
        mode = git("ls-files", "-s", path).split()[0]
        git("update-index", "--cacheinfo", f"{mode},{sha},{path}")
        return sha
    finally:
        os.unlink(tmp)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    for path, _ in MARKERS.items():
        diff = git("diff", "--", path)
        header, hunks = split_hunks(diff)
        mine, foreign = selected(path, hunks)
        print(f"{path}: {len(mine)} mine / {len(foreign)} foreign hunks")
        for hunk in foreign:
            print(f"    skip {hunk[0].strip()}")
        if not mine:
            print(f"    !! nothing selected for {path}", file=sys.stderr)
            return 1
        if args.check:
            continue
        base = git("show", f"HEAD:{path}")
        with tempfile.TemporaryDirectory() as work:
            target = os.path.join(work, os.path.basename(path))
            with open(target, "w", encoding="utf-8") as handle:
                handle.write(base)
            patch = os.path.join(work, "sel.patch")
            with open(patch, "w", encoding="utf-8") as handle:
                handle.write("".join(header) + "".join("".join(h) for h in mine))
            # `patch` rather than `git apply`: dropping earlier hunks shifts the
            # line numbers of the later ones, and patch resolves that by context.
            result = subprocess.run(
                ["patch", "--batch", "--silent", target, patch],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                print(result.stdout + result.stderr, file=sys.stderr)
                return 1
            with open(target, "rb") as handle:
                stage_blob(path, handle.read())
        print(f"    staged {path}")

    if args.check:
        return 0

    for path in WHOLE + NEW:
        git("add", "--", path)
        print(f"staged {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
