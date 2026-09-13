#!/usr/bin/env python3
"""Break each rule the media delivery foundation rests on, and prove a test notices.

§61 names ten defects. Each one below is the smallest edit that reintroduces one of
them, paired with the test that has to go red. A protection test nobody has watched
fail is a comment with a test runner attached, and three of the guards here were
written in this mission -- the ones most likely to be vacuous.

Two runners, because the foundation is two languages. The mutation targets and the
assertions that catch them do not line up one-to-one across that boundary: a rule
enforced on the server can be guarded by a Python test, a client-side rule cannot,
and the two duration tables are pinned to each other by a drift test rather than
shared code.

Never writes the repository.

* Python targets use the symlink sandbox shared with the UNDX harnesses: the repo is
  symlinked and only the mutated file is real. Its closing `raise` is what makes that
  sentence true rather than intended -- see the docstring on `build_sandbox`, which
  records the run that truncated real files when a target shape outgrew it.
* JavaScript targets cannot use that sandbox. Node and Jest resolve a module through
  its realpath, so a symlinked test file would import the *real* module and the
  mutation would be invisible -- a harness that reported SURVIVED for every mutation
  while proving nothing. They get an APFS clone of `mobile-native/` instead (~20s,
  copy-on-write, so writes there cannot reach the original), built once and reused,
  with the mutated file restored from the repo between runs.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from undx_call_domain_mutation_check import build_sandbox  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parent.parent

FOUNDATION = "services/messenger_media_foundation.py"
POLICY = "services/stored_video_policy.py"
MEDIA_SERVICE = "services/media_service.py"
# The first depth-1 targets this harness has been given. `build_sandbox` walks an
# arbitrary depth now and its closing `raise` evaluates the claim, but a root-level
# module is a new shape for it -- see that docstring for the run where an unhandled
# shape truncated real files.
BOT = "bot.py"
MEDIA_WORKER = "media_worker.py"
CHAT_SCREEN = "mobile-native/src/screens/ChatScreen.tsx"
MEDIA_ACTIONS = "mobile-native/src/media/mediaActions.ts"
MEDIA_CONTRACT = "mobile-native/src/media/mediaContract.ts"
MESSENGER_API = "mobile-native/src/api/messenger.ts"

#: (label, file, old, new, test that must fail, test path, runner)
#:
#: The label is §61's wording. `expect` is the whole point: a mutation that fails
#: *some* test proves the suite is not empty, while one that fails *the named* test
#: proves the assertion written for that defect is the one doing the work.
MUTATIONS = [
    (
        # §1, the reported bug. One missing allowlist row answered "That file type is
        # not supported for Messenger media." to the most ordinary video an iPhone can
        # produce, and nothing in the suite could have noticed it missing.
        "video/quicktime classified unsupported",
        FOUNDATION,
        '    "video/quicktime": {"media_type": "video", "extensions": {"mov", "qt"}, "disposition": "inline"},\n',
        "",
        "test_the_honest_picker_spelling_resolves_to_video",
        "tests/test_messenger_media_type_resolution.py",
        "pytest",
    ),
    (
        # §2. A URL minted at upload time and persisted is dead by the time anyone
        # taps it. The client must be handed a stable route it can re-authorize.
        "PDF persisted with unusable stale URL",
        FOUNDATION,
        '        "download_url": f"/api/messages/media/{attachment_id}/download",',
        '        "download_url": f"/media/private/{_row_get(row, \'storage_key\', \'\') or \'\'}",',
        "test_a_document_travels_the_ordinary_attachment_lifecycle",
        "tests/test_messenger_document_attachment_identity.py",
        "pytest",
    ),
    (
        # §4. The original defect exactly: the card renders, the tap does nothing.
        "document viewer removed",
        MEDIA_ACTIONS,
        """    await Sharing.shareAsync(fileUri, {
      mimeType,
      UTI: utiFor("file", mimeType),
      dialogTitle: target.title || "Open document"
    });""",
        "    /* viewer removed */",
        "opens the downloaded file, which is the whole point of the tap",
        "src/media/__tests__/mediaActions.test.ts",
        "jest",
    ),
    (
        # §37. Off-by-one at the boundary, in the direction that refuses a video the
        # product promises to accept.
        "90:00 rejected",
        POLICY,
        "    return int(seconds) > max_duration_seconds(surface)",
        "    return int(seconds) >= max_duration_seconds(surface)",
        "test_exactly_ninety_minutes_is_accepted",
        "tests/test_stored_video_policy.py",
        "pytest",
    ),
    (
        # §37, the other direction, and the one a passing suite would hide: a ceiling
        # that is slightly too generous looks exactly like a working ceiling.
        "90:01 accepted",
        POLICY,
        "    return int(seconds) > max_duration_seconds(surface)",
        "    return int(seconds) > max_duration_seconds(surface) + 60",
        "test_one_second_over_is_refused",
        "tests/test_stored_video_policy.py",
        "pytest",
    ),
    (
        # §11. The performance defect, which is invisible in review because the
        # fallback renders correctly -- it merely costs the entire movie to paint a
        # card a few hundred pixels wide.
        "full videos eagerly downloaded in thread",
        CHAT_SCREEN,
        """        {thumbnailUrl ? (
          <Image source={{ uri: thumbnailUrl }} style={styles.videoPoster} resizeMode="cover" onError={retryMedia} />
        ) : null}""",
        """        {thumbnailUrl || mediaUrl ? (
          <Image source={{ uri: thumbnailUrl || mediaUrl }} style={styles.videoPoster} resizeMode="cover" onError={retryMedia} />
        ) : null}""",
        "posters a video from the derived thumbnail only, never from the movie",
        "src/screens/__tests__/ChatScreenAttachmentWiring.test.ts",
        "jest",
    ),
    (
        # §53. A signed URL is transport authorization. Stop verifying the signature
        # and it becomes an authorization bypass that still passes every happy path.
        "signed URL authorization bypassed",
        FOUNDATION,
        '    if not hmac.compare_digest(parts[3], _access_token_signature(secret, body)):\n        return "invalid", 0',
        '    if False:\n        return "invalid", 0',
        "test_tampered_signature_is_refused",
        "tests/test_messenger_media_auth_isolation.py",
        "pytest",
    ),
    (
        # §57. Minting a fresh identity on retry is what turns a lost response into a
        # genuine duplicate: the retry no longer looks like the message that may
        # already have landed, so the server's uniqueness index cannot help.
        "failed upload creates duplicate retry message",
        MESSENGER_API,
        "    client_message_id: clientMessageId || mintClientMessageId()",
        "    client_message_id: mintClientMessageId()",
        "adopts a caller-supplied id so a retry keeps the identity of the first attempt",
        "src/api/__tests__/messengerIdentity.test.ts",
        "jest",
    ),
    (
        # §45. Requiring a thumbnail to consider a record drawable blanks every card
        # whose preview has not been generated yet, even though the source media is
        # right there. The empty-rectangle failure, arrived at from the other side.
        "thumbnail unavailable causes black card despite source media",
        MEDIA_CONTRACT,
        "  if (!media) return false;\n  const candidates = [",
        "  if (!media) return false;\n  if (!media.thumbnail_url) return false;\n  const candidates = [",
        "accepts media carried on any single one of the url fields",
        "src/media/__tests__/mediaContract.test.ts",
        "jest",
    ),
    (
        # §34. The inverse of the reported bug, and the worse one: a normalizer that
        # answers something for every input is a bypass with a docstring.
        "UNKNOWN mime automatically trusted",
        FOUNDATION,
        '    if not resolved:\n        raise MessengerMediaError("unsupported_mime_type", '
        '"That file type is not supported for Messenger media.", 415)',
        '    if not resolved:\n        resolved = "application/pdf"',
        "test_a_type_and_extension_nobody_allowlists_is_refused",
        "tests/test_messenger_media_type_resolution.py",
        "pytest",
    ),
    (
        # §13, resumability. The resume point is the provider's account of what it
        # stored; a client-supplied list is a claim about someone else's storage.
        # Trusting it commits an object with a hole where the skipped range was,
        # and marks the attachment uploaded. The first version of the guard below
        # asserted on an echoed response field and survived this exact edit.
        "resume point read from the client's parts list",
        FOUNDATION,
        '    declared = int(_row_get(row, "size_bytes", 0) or 0)\n'
        "    stored = _provider_parts(row, provider_upload_id)\n",
        '    declared = int(_row_get(row, "size_bytes", 0) or 0)\n'
        '    stored = [{"part_number": int(p.get("part_number") or 0), "etag": str(p.get("etag") or ""), '
        '"size_bytes": int(p.get("size_bytes") or 0)} for p in (payload.get("parts") or [])] '
        "or _provider_parts(row, provider_upload_id)\n",
        "test_a_client_cannot_claim_a_part_it_never_sent",
        "tests/test_messenger_resumable_upload.py",
        "pytest",
    ),
    (
        # §13. Completing first and checking after is the difference between a
        # refused upload and a truncated object that is valid at the key, passes
        # every later read, and is only discovered by whoever watches the video.
        "byte total verified after the object is committed",
        FOUNDATION,
        '    if declared and total != declared:\n        raise MessengerMediaError(\n            "upload_incomplete",',
        '    if False:\n        raise MessengerMediaError(\n            "upload_incomplete",',
        "test_finishing_early_is_refused_before_a_truncated_object_exists",
        "tests/test_messenger_resumable_upload.py",
        "pytest",
    ),
    (
        # §53. Read access and write access are not the same grant. Every member of
        # a conversation can read an attachment; only the sender may sign parts into
        # its upload or decide it is finished.
        "any conversation member can write to the upload",
        FOUNDATION,
        "    _require_attachment_access(cur, row, user_id, require_sender=True)\n    provider_upload_id",
        "    _require_attachment_access(cur, row, user_id, require_sender=False)\n    provider_upload_id",
        "test_another_member_of_the_conversation_cannot_sign_parts",
        "tests/test_messenger_resumable_upload.py",
        "pytest",
    ),
    (
        # §12-13. The server chose a transport and the client ignored it. Nothing
        # errors: a 2 GB body is handed to one POST, Flask holds it in memory, and
        # the first dropped connection starts the whole hour over.
        "resumable transport ignored by the client",
        MESSENGER_API,
        '  if (init.upload_method === "resumable") {',
        "  if (false) {",
        "sends a long video as parts and never through the single-request route",
        "src/api/__tests__/messengerResumableUpload.test.ts",
        "jest",
    ),
    (
        # §13. Resuming from part one re-sends everything already stored. On a
        # 2 GB upload that is the difference between finishing and never
        # finishing, and it looks identical in review.
        "resume restarts from the first part",
        MESSENGER_API,
        "  const pending = Array.isArray(state.missing_parts) && state.missing_parts.length\n"
        "    ? state.missing_parts.map((value) => Number(value)).filter((value) => value >= 1 && value <= partCount)\n"
        "    : Array.from({ length: partCount }, (_, index) => index + 1);",
        "  const pending = Array.from({ length: partCount }, (_, index) => index + 1);",
        "resumes from what the server says is stored, not from part one",
        "src/api/__tests__/messengerResumableUpload.test.ts",
        "jest",
    ),
    (
        # §13. The final part is short. Slicing a full part width past the end pads
        # the object with zeros, and the byte total then disagrees with the declared
        # size -- so the server refuses an upload that was actually complete.
        "final part sliced past the end of the file",
        MESSENGER_API,
        "        const end = Math.min(sizeBytes, start + partSize);",
        "        const end = start + partSize;",
        "sends a long video as parts and never through the single-request route",
        "src/api/__tests__/messengerResumableUpload.test.ts",
        "jest",
    ),
    # ------------------------------------------------------------------
    # The measured duration. Everything above enforces the ceiling against a
    # number the client sent; these nine defend the one number it cannot choose.
    # The failure they all share is silence: every one of them leaves a server
    # that measures the video, writes the measurement down, and never compares it
    # to the limit -- which reads exactly like a platform whose videos are all
    # within the limit.
    # ------------------------------------------------------------------
    (
        # The defect this whole seam exists for. The measurement arrives, is stored,
        # and decides nothing.
        "measured duration recorded but never enforced",
        MEDIA_SERVICE,
        "        reason = stored_video_policy.measured_violation(surface, measured)",
        '        reason = ""',
        "test_a_lying_client_does_not_help_itself",
        "tests/test_measured_video_duration_enforcement.py",
        "pytest",
    ),
    (
        # The ceiling is per-surface, so the verdict cannot be reached once for the
        # platform. Collapsing it lets a 10-minute marketplace listing run 90.
        "per-surface ceiling collapsed to one global limit",
        MEDIA_SERVICE,
        '        surface = str(row.get("context_type") or "")',
        '        surface = "post"',
        "test_the_ceiling_is_read_per_row_not_once_for_the_platform",
        "tests/test_measured_video_duration_enforcement.py",
        "pytest",
    ),
    (
        # A takedown that leaves the asset available is not a takedown. This is also
        # what keeps a redelivered `video.asset.ready` from republishing the video,
        # since that handler sets is_available back to 1 on every delivery.
        "blocked asset left available",
        MEDIA_SERVICE,
        "            SET moderation_status='blocked', moderation_reason=?, is_available=0,",
        "            SET moderation_status='blocked', moderation_reason=?,",
        "test_the_block_is_reasserted_on_every_delivery",
        "tests/test_measured_video_duration_enforcement.py",
        "pytest",
    ),
    (
        # The inverted fallback. `max_duration_seconds` answers an unregistered
        # surface with the *strictest* cap, which is right for refusing an upload and
        # catastrophic for a post-hoc takedown: `asset_focus`, `native` and
        # `pulse_comment` all reach this table today, so convicting on the fallback
        # silently blocks valid video against a 60s limit nobody chose.
        "unregistered surface convicted on the strictest cap",
        POLICY,
        '    if not is_known_surface(surface):\n        return ""\n',
        "",
        "test_an_unregistered_surface_is_not_convicted_however_long",
        "tests/test_measured_video_duration_enforcement.py",
        "pytest",
    ),
    (
        # Mux's `duration` was parsed by this route long before this mission and spent
        # only on live-replay rows. Removing the call restores that: the one
        # measurement the uploader could not choose, thrown away again.
        "Mux webhook discards the measurement",
        BOT,
        "                    media_service.enforce_measured_video_duration(\n"
        "                        cur, asset_id=mux_asset_id, duration_seconds=mux_duration_seconds)",
        "                    pass",
        "test_an_over_long_asset_is_blocked_by_the_delivery_that_reports_it",
        "tests/test_measured_duration_wiring.py",
        "pytest",
    ),
    (
        # Ordering, not presence. Enforcement sits after the UPDATE that restores
        # `is_available=1` for a ready asset. Hoisted above it, every delivery blocks
        # the video and then immediately republishes it -- and no unit test of either
        # statement alone would see anything wrong.
        "measurement overwritten by the ready update",
        BOT,
        """                cur.execute(
                    \"\"\"
                    UPDATE chat_media_uploads
                    SET mux_status=?, mux_playback_id=COALESCE(NULLIF(?, ''), mux_playback_id),
                        playback_url=COALESCE(NULLIF(?, ''), playback_url),
                        processing_status=?, is_available=CASE WHEN ?='ready' THEN 1 ELSE is_available END,
                        error_message=CASE WHEN ?='errored' THEN 'Mux video asset errored.' ELSE COALESCE(error_message, '') END,
                        updated_at=?
                    WHERE mux_asset_id=?
                    \"\"\",
                    (status, playback_id, playback_url, processing_status, status, status, now, mux_asset_id),
                )
""",
        """                if status == "ready":
                    media_service.enforce_measured_video_duration(
                        cur, asset_id=mux_asset_id, duration_seconds=mux_duration_seconds)
                cur.execute(
                    \"\"\"
                    UPDATE chat_media_uploads
                    SET mux_status=?, mux_playback_id=COALESCE(NULLIF(?, ''), mux_playback_id),
                        playback_url=COALESCE(NULLIF(?, ''), playback_url),
                        processing_status=?, is_available=CASE WHEN ?='ready' THEN 1 ELSE is_available END,
                        error_message=CASE WHEN ?='errored' THEN 'Mux video asset errored.' ELSE COALESCE(error_message, '') END,
                        updated_at=?
                    WHERE mux_asset_id=?
                    \"\"\",
                    (status, playback_id, playback_url, processing_status, status, status, now, mux_asset_id),
                )
""",
        "test_the_measurement_outranks_the_ready_update_in_the_same_request",
        "tests/test_measured_duration_wiring.py",
        "pytest",
    ),
    (
        # The webhook is the fast path, not the guaranteed one -- it needs a secret set
        # and an endpoint registered, and one lost delivery leaves a video permanently
        # unmeasured, which is indistinguishable from a video within the limit.
        "worker cycle never polls for unmeasured video",
        MEDIA_WORKER,
        '    durations = reconcile_stored_video_durations(int(os.getenv("MEDIA_WORKER_DURATION_RECONCILE_BATCH", "25")))',
        '    durations = {"skipped": "disabled"}',
        "test_the_cycle_calls_the_duration_pass_and_reports_it",
        "tests/test_measured_duration_wiring.py",
        "pytest",
    ),
    (
        # Messenger's ffprobe pass has always produced the real length and only ever
        # written it down. This is that original defect, restored.
        "Messenger records its probe without enforcing it",
        FOUNDATION,
        '    reason = stored_video_policy.measured_violation_ms(MESSENGER_VIDEO_SURFACE, updates.get("duration_ms")) if media_type == "video" else ""',
        '    reason = ""',
        "test_an_over_long_video_is_blocked_after_measurement",
        "tests/test_measured_video_duration_enforcement.py",
        "pytest",
    ),
    (
        # `deferred` reschedules without spending the error budget, so treating a
        # blocked row as merely incomplete retries it every two minutes for as long as
        # the row exists. The queue never drains and nothing ever reports an error.
        "blocked attachment retried forever",
        FOUNDATION,
        '    if str(_row_get(row, "upload_status", "")).lower() == "blocked":\n        return {"status": "skipped", "reason": "blocked"}',
        "    pass",
        "test_a_blocked_attachment_is_settled_not_deferred",
        "tests/test_measured_video_duration_enforcement.py",
        "pytest",
    ),
    (
        # Every read of an attachment passes through this gate, which is the only
        # reason an over-long video can be taken back after it was already attached to
        # a message. Gating the download route alone would leave the thumbnail and the
        # metadata still serving it.
        "blocked attachment still served to readers",
        FOUNDATION,
        '    if str(_row_get(row, "upload_status", "")).lower() == "blocked":\n        raise MessengerMediaError(\n            "attachment_blocked",',
        '    if False:\n        raise MessengerMediaError(\n            "attachment_blocked",',
        "test_a_blocked_attachment_is_refused_to_every_reader",
        "tests/test_measured_video_duration_enforcement.py",
        "pytest",
    ),
]


def _python() -> str:
    """Prefer the checkout's venv: system python3 lacks pytest and fakes a failure."""
    venv = REPO / ".venv" / "bin" / "python"
    return str(venv) if venv.exists() else sys.executable


def clone_mobile_native(into: pathlib.Path) -> pathlib.Path:
    """APFS copy-on-write clone of `mobile-native/`, so Jest resolves real files."""
    destination = into / "mobile-native"
    subprocess.run(["cp", "-Rc", str(REPO / "mobile-native"), str(destination)], check=True)
    return destination


def failed_on(output: str, expect: str, runner: str) -> bool:
    """True when `expect` names a test the runner *reported as failing*.

    Plain containment is not good enough in either language: pytest echoes the whole
    test name in its collection and progress output, and Jest prints the names of
    passing tests too. Both mark a failure with a distinctive line prefix, so that is
    what gets matched -- otherwise a mutation the suite ignored would be recorded as
    caught by the very test that stayed green.
    """
    # SUBFAILED is how pytest reports a failing `subTest`. Without it, a guard
    # written as a parametrised sweep reads as "died, but not on <expect>" -- the
    # harness would call a caught mutation uncaught, which is the one direction of
    # error that sends someone off to fix a test that is already working.
    markers = ("●",) if runner == "jest" else ("FAILED", "ERROR", "SUBFAILED")
    for line in output.splitlines():
        stripped = line.strip()
        if any(stripped.startswith(marker) for marker in markers) and expect in stripped:
            return True
    return False


def main() -> int:
    only = ""
    args = sys.argv[1:]
    if args and args[0] == "--only":
        if len(args) < 2:
            print("--only needs a substring", file=sys.stderr)
            return 2
        only = args[1]

    selected = [m for m in MUTATIONS if not only or only in m[0]]
    if not selected:
        print(f"no mutation label contains {only!r}", file=sys.stderr)
        return 2

    failures: list[str] = []
    jest_root: pathlib.Path | None = None
    with tempfile.TemporaryDirectory() as jest_tmp:
        for label, target, old, new, expect, tests, runner in selected:
            if runner == "jest":
                if jest_root is None:
                    print(f"cloning mobile-native for the Jest mutations ...", flush=True)
                    jest_root = clone_mobile_native(pathlib.Path(jest_tmp))
                relative = target.split("mobile-native/", 1)[1]
                path = jest_root / relative
                pristine = path.read_text(encoding="utf-8")
                command = ["npx", "jest", tests, "--ci"]
                cwd, env = jest_root, dict(os.environ)
                restore = (path, pristine)
                sandbox_cleanup = None
            else:
                sandbox = build_sandbox(pathlib.Path(tempfile.mkdtemp()), target)
                path = sandbox / target
                pristine = path.read_text(encoding="utf-8")
                command = [_python(), "-m", "pytest", tests, "-q", "--no-header", "-p", "no:cacheprovider"]
                cwd = sandbox
                env = dict(os.environ, PYTHONPATH=str(sandbox), PYTHONDONTWRITEBYTECODE="1")
                restore = None
                sandbox_cleanup = sandbox.parent

            try:
                if pristine.count(old) != 1:
                    failures.append(f"{label}: anchor matched {pristine.count(old)}x, expected 1")
                    print(f"BAD {label}: anchor is not unique")
                    continue
                path.write_text(pristine.replace(old, new), encoding="utf-8")
                proc = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, timeout=900)
            finally:
                if restore:
                    restore[0].write_text(restore[1], encoding="utf-8")
                if sandbox_cleanup:
                    subprocess.run(["rm", "-rf", str(sandbox_cleanup)], check=False)

            output = proc.stdout + proc.stderr
            if proc.returncode == 0:
                verdict = "SURVIVED"
                failures.append(f"{label}: suite stayed green\n{output[-1200:]}")
            elif not failed_on(output, expect, runner):
                verdict = f"died, but not on {expect!r}"
                failures.append(f"{label}: expected {expect!r} to fail\n{output[-2000:]}")
            else:
                verdict = f"caught by {expect!r}"
            print(f"{'BAD' if failures and failures[-1].startswith(label + ':') else 'ok '} {label}: {verdict}", flush=True)

    print()
    if failures:
        print(f"{len(failures)} problem(s):")
        for item in failures:
            print(f"  - {item}")
        return 1
    scope = f" (filtered to {only!r}; {len(MUTATIONS)} exist)" if only else ""
    print(f"All {len(selected)} mutations behaved as specified{scope}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
