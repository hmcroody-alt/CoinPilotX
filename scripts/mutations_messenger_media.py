#!/usr/bin/env python3
"""Prove the messenger media tests fail when the fix is taken back out.

Each entry breaks exactly one guarantee from the mission and names the test that
has to notice. A mutation nothing catches is a guarantee nothing holds.

Run from the repo root. Restores every file it touched, including on failure.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NATIVE = ROOT / "mobile-native"

CHAT = "mobile-native/src/screens/ChatScreen.tsx"
WORKER = "media_worker.py"
ACCESS = "mobile-native/src/media/messengerMediaAccess.ts"
FOUNDATION = "services/messenger_media_foundation.py"
WEB = "static/js/pulse_messages_v2.js"

JEST_PREVIEW = ["npx", "jest", "--ci", "src/screens/__tests__/ChatScreenMediaPreview.test.tsx"]
JEST_ACCESS = ["npx", "jest", "--ci", "src/media/__tests__/messengerMediaAccess.test.ts"]
PYTEST = [sys.executable, "-m", "pytest", "-q",
          "tests/test_messenger_media_processing_pipeline.py"]
PYTEST_WEB = [sys.executable, "-m", "pytest", "-q",
              "tests/web_surface/test_messenger_bubble_body.py"]


def cut_voice_controls(source: str) -> str:
    """Delete the voice control row, as if the label removal took it along."""
    start = source.index("      <View style={styles.voiceControls}>")
    end = source.index("\n      </View>\n    </View>\n  );\n});", start)
    return source[:start] + source[end + len("\n      </View>") :]


MUTATIONS = [
    (
        "image preview hidden until tap",
        CHAT,
        ("<MediaPreviewImage uri={photoPreviewUrl} onRetry={retryMedia} />",
         "<View style={styles.mediaFill} />"),
        JEST_PREVIEW,
    ),
    (
        "video poster missing despite valid media",
        CHAT,
        ("  const poster = absoluteMediaUrl(access.thumbnailUrl);",
         "  const poster = \"\";"),
        JEST_PREVIEW,
    ),
    (
        "expired preview URL never refreshes",
        ACCESS,
        ("    if (isExpiredGrant(error)) {", "    if (false) {"),
        JEST_ACCESS,
    ),
    (
        "the bubble pulls the full original instead of the rendition",
        CHAT,
        ("const photoPreviewUrl = thumbnailUrl || (isPreviewTerminal(mediaAccess.meta.processingStatus) ? mediaUrl : \"\");",
         "const photoPreviewUrl = thumbnailUrl || mediaUrl;"),
        JEST_PREVIEW,
    ),
    (
        "a posterless frame goes back to being a silent dark block",
        CHAT,
        ("  const processing = !poster && isPosterPending(access.meta.processingStatus);",
         "  const processing = !poster;"),
        JEST_PREVIEW,
    ),
    (
        "'Voice message' label restored",
        CHAT,
        ("      <View style={styles.voiceControls}>",
         "      <Text>Voice message</Text>\n      <View style={styles.voiceControls}>"),
        JEST_PREVIEW,
    ),
    (
        "voice filename displayed",
        CHAT,
        ("  if (isVoiceType(type)) return \"\";\n", ""),
        JEST_PREVIEW,
    ),
    (
        "removing the label breaks playback",
        CHAT,
        cut_voice_controls,
        JEST_PREVIEW,
    ),
    (
        "web: 'Voice message' label restored",
        WEB,
        ("      body: body.trim(),",
         "      body: body.trim() || (hasVoice ? \"Voice message\" : \"Attachment\"),"),
        PYTEST_WEB,
    ),
    (
        "web: voice filename displayed",
        WEB,
        ("${bubbleBodyText(item) ? `<p>${linkifiedMessageHtml(bubbleBodyText(item))}</p>` : \"\"}",
         "${item.body ? `<p>${linkifiedMessageHtml(item.body)}</p>` : \"\"}"),
        PYTEST_WEB,
    ),
    (
        "private preview accessible outside the conversation",
        FOUNDATION,
        ("    _require_attachment_access(cur, row, user_id, require_sender=False)\n"
         "    thumbnail_key = str(_row_get(row, \"thumbnail_key\", \"\") or \"\")",
         "    thumbnail_key = str(_row_get(row, \"thumbnail_key\", \"\") or \"\")"),
        PYTEST,
    ),
    (
        "a job retiring without processing anything goes back to being silent",
        WORKER,
        ("    _warn_if_still_unprocessed(cur, job_id, target_id, status, result.get(\"reason\"))\n", ""),
        PYTEST,
    ),
    (
        "the detector cries wolf on healthy completions",
        WORKER,
        ("    if processing_status not in {\"queued\", \"processing\"}:\n        return\n", ""),
        PYTEST,
    ),
    (
        "the sweep duplicates work it has already queued",
        FOUNDATION,
        ("              AND LOWER(COALESCE(j.status,'')) IN ('pending','processing')",
         "              AND LOWER(COALESCE(j.status,'')) IN ('no_such_status')"),
        PYTEST,
    ),
    (
        "the sweep re-queues an unrecoverable attachment forever",
        FOUNDATION,
        ("        if rounds >= MAX_PROCESSING_ROUNDS:", "        if False:"),
        PYTEST,
    ),
]


def apply(source: str, edit) -> str:
    if callable(edit):
        return edit(source)
    old, new = edit
    if source.count(old) != 1:
        raise SystemExit(f"anchor is not unique ({source.count(old)} hits): {old[:70]!r}")
    return source.replace(old, new)


def run(command: list[str]) -> bool:
    cwd = NATIVE if command[0] == "npx" else ROOT
    done = subprocess.run(command, cwd=cwd, capture_output=True, text=True)
    return done.returncode == 0


def main() -> int:
    failures = []
    for name, relative, edit, command in MUTATIONS:
        path = ROOT / relative
        original = path.read_text(encoding="utf-8")
        try:
            path.write_text(apply(original, edit), encoding="utf-8")
            caught = not run(command)
        finally:
            path.write_text(original, encoding="utf-8")
        print(f"{'CAUGHT ' if caught else 'ESCAPED'}  {name}")
        if not caught:
            failures.append(name)
    print(f"\n{len(MUTATIONS) - len(failures)}/{len(MUTATIONS)} mutations caught")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
