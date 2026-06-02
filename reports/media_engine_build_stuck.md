# Media Engine Build Stuck

Date: 2026-06-02

## Summary

The `coinpilotx-media-engine` deployment for commit/message:

```text
Start Pulse media worker with focused schema check
Deployment id: 309e7f20-78fa-4570-9592-4140d7b1d84f
```

was stuck in Railway for 27+ minutes. It was aborted from the Railway dashboard.
The active media-engine service was left on the previous successful deployment:

```text
Fix Pulse media worker MOV backlog query
Deployment id: dfe32b79-1c18-4bc8-8072-0342cf15df8e
Status: ACTIVE / Deployment successful
```

The main `CoinPilotX` web service was not rolled back.

## Build Evidence

Railway dashboard showed:

```text
Building
Deployment in progress: Building the image...
Start Pulse media worker with focused schema check
```

The aborted build log contained only Railpack planning and snapshot upload:

```text
unpacking archive
using build driver railpack-v0.26.0
Detected Python
Using pip
Found web command in Procfile
Packages:
python 3.13.13
Steps:
install
  python -m venv /app/.venv
  pip install -r requirements.txt
Deploy:
  python media_worker.py
uploading snapshot
```

There was no observed movement into:

- pip install execution
- apt/ffmpeg package install
- image export
- worker startup

## Current Build Step

Most precise current step before abort:

```text
Railpack image build / snapshot upload stage
```

It was not proven to be stuck inside ffmpeg installation or worker startup.
The dashboard incident banner also stated:

```text
Dashboard Logs Loading Slowly. We have pushed a fix and are now monitoring the incident.
```

This suggests Railway dashboard/build-log infrastructure may have contributed to
poor visibility, but the service deployment itself remained stuck long enough to
meet the abort rule.

## Recovery Action

Action taken:

```text
Aborted deployment 309e7f20-78fa-4570-9592-4140d7b1d84f
```

Result:

```text
Your deployment was cancelled.
```

After abort, Railway showed:

```text
coinpilotx-media-engine
ACTIVE
Fix Pulse media worker MOV backlog query
Deployment successful
```

No rollback was needed beyond aborting the stuck build because Railway had kept
the previous successful media-engine deployment active.

## Web Service Status

The main web service remained online. Production route checks passed:

```text
https://coinpilotx.app/pulse
Title: Global Pulse Feed | CoinPilotXAI
Error state: none detected
Feed signal: present
```

```text
https://coinpilotx.app/pulse/status
Title: Pulse Status | CoinPilotXAI Pulse
Error state: none detected
Status creation UI: present
```

No live post/status publish was attempted because the recovery mission also
required that production database data not be modified.

## Impact

Current production state after recovery:

- Web app: running.
- Pulse feed route: loads.
- Pulse Status route: loads.
- Media engine: running on previous successful deployment.
- Stuck media-engine build: aborted/removed.

Remaining limitation:

- The smaller media worker startup fix from `Start Pulse media worker with
  focused schema check` is not deployed on `coinpilotx-media-engine` because its
  build was aborted.
- Existing `.mov` playback repair should be handled in a smaller follow-up that
  does not block production with a long Railpack build.

## Recommended Follow-Up

Create a smaller worker recovery patch that avoids forcing all Railway services
through a heavy rebuild if possible. Recommended approach:

1. Keep the current active media-engine deployment stable.
2. Move the focused media-worker startup change into a minimal follow-up.
3. If ffmpeg/transcoding still requires heavy Railpack work, split playback
   repair from worker startup so production is not blocked by image rebuilds.
4. Validate the media engine build duration before relying on it for production
   playback repair.

