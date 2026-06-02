# Pulse Reels and Status Publish Truth

Date: 2026-06-02

## Scope

This report intentionally covers only:

- Pulse Status publishing
- Pulse Reels publishing

Regular feed post publishing was not modified.

## Deployment State

The latest deployed production commit is:

- `1fb0b1a Fix Pulse Status media save persistence`

Railway deployment is now unblocked after the production variables were cleaned
up.

- Deployment: `98a0e88f-fae0-4f8d-862f-d6bb86c27163`
- Status: `SUCCESS`
- Time: 2026-06-02 13:18:03 -04:00

The previous `secret Access not found` build failure is no longer active.

## Feed Posts

Classification: **PASS**

Evidence from production HTTP logs:

- `POST /api/pulse/posts` returned `200`
- Request ID: `Bams_Eb_T6CK__950ubPiw`
- Request ID: `7IbHJNVzTUCPUXiXAXC71g`
- Request ID: `p7JLpFl8SWmBOu6xDcO5xA`

Feed post logic was not changed during this pass.

## Pulse Status

Classification: **PASS**

Production evidence before deployment of `1fb0b1a`:

- `POST /api/pulse/media/upload` returned `200`
- `POST /api/pulse/status` returned `500`
- Desktop request ID: `xOJqjJvuScuYShdmVOLIQQ`
- Mobile request IDs:
  - `6APoRXaCSE-mYh9L0ubPiw`
  - `2xSOFGMtQ6ic1DlQV7rehQ`
- User-facing message: `Server could not save Pulse Status. Try again or contact support with this trace ID.`

Root cause fixed locally and pushed:

- The Postgres compatibility layer did not include Pulse Status tables in the
  auto-primary-key `RETURNING id` map.
- Status rows inserted successfully, but the save path lost `cur.lastrowid` and
  failed downstream.
- Commit `1fb0b1a` added:
  - `pulse_status`
  - `pulse_statuses`
  - `pulse_status_views`
  - `pulse_status_reactions`
  - `pulse_status_replies`
  - `pulse_status_music`
  - `pulse_status_media`
  - `pulse_status_live`

Local validation for Status passed:

- Text-only Status post
- Image Status post
- `.mov` Status upload and post contract
- Text + media Status post
- Recent Status rail updates after local publish

Production verification after successful Railway deployment:

- Authenticated `POST /api/pulse/media/upload` returned `200`.
- Follow-up authenticated `POST /api/pulse/status` returned `200`.
- Request IDs:
  - Upload: `LP4OcTXyQrCfVSQKmrpb1w`
  - Status save: `Sk-bVegmR2KNtVgSVOLIQQ`
- Browser text-only Status test posted successfully.
- Text-only request ID: `g0qN12CWQWqLnOFZJH0Vcg`
- Recent Status refreshed and showed the new status immediately.
- No browser console errors were observed during the text-only production test.

## Reels

Classification: **PARTIAL PASS / PUBLISH FLOW STILL NEEDS FILE QA**

Production browser evidence before successful deployment:

- Internal user-facing text is still visible:
  - `Adaptive playback · poster first`
- Current upload modal does not show the rebuilt video picker:
  - Missing `Upload Reel Video`
  - Missing `Choose a video from your device`
  - Missing selected video preview card
- Current modal order still shows:
  - Title
  - Caption
  - Community
  - Privacy
  - Sound controls
  - Publish Reel
  - Cancel
- `Publish Reel` is visible even before a selected video appears.
- Visible Reels in the feed show media failures:
  - `Media could not load.`
  - `Trace media-84`
  - `Trace media-86`

No successful `POST /api/pulse/reels/create` request was observed in the recent
production POST log window.

This indicates production is still serving the old Reels experience. The rebuilt
Reels upload UI and layout cannot be verified in production until Railway
successfully deploys the current main branch.

Production browser evidence after successful deployment:

- `Adaptive playback` / `poster first` text is no longer visible.
- Reels stage renders a full-screen reel with right action rail.
- Follow button is visible.
- Upload button is visible.
- Upload modal opens.
- Upload modal shows:
  - `Upload Reel Video`
  - `Choose a video from your device`
  - `MP4, MOV, or WEBM`
- `Publish Reel` is disabled before video selection.
- No browser console errors were observed during page/modal inspection.

Open items:

- A real MP4/MOV file selection is still needed to verify preview metadata,
  publish request, Reel creation, and playback persistence in production.
- Production logs show the Reels feed endpoint returns `200`, but no successful
  `POST /api/pulse/reels/create` was observed in the checked window.
- Production logs also showed a non-publishing Postgres grouping warning in the
  Pulse creator summary query. That query was patched to group by the displayed
  user columns as well as `p.user_id`.

## Required Next Production Verification

Remaining production verification:

1. Reels:
   - Select MP4.
   - Confirm preview metadata.
   - Publish Reel.
   - Confirm Reel appears.
   - Confirm video plays.
   - Confirm sound behavior works.
2. Reels:
   - Repeat with MOV.

## Local Validation

Completed during this pass:

- Python compile check
- JavaScript parse check
- Pulse Status posting audit
- Reels upload UI audit
- Reels media audit
- Reels layout audit
- Site functional audit
- Performance audit
- `git diff --check`

Result:

- Local validation failures: `0`
- Performance warnings: `1` existing warning for `static/js/pulse_live_studio.js`
  polling every 2000ms

Additional production validation completed after Railway recovery:

- `/pulse/status` browser check
- `/pulse/reels?tab=for_you` browser check
- Railway HTTP log check for Status upload/save
- Railway deployment status check

## Summary

- Feed Posts: **PASS**
- Pulse Status: **PASS**
- Reels UI: **PASS**
- Reels publish: **NEEDS FILE QA**
- Deployment: **PASS**
