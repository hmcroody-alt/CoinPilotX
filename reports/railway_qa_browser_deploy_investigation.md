# Railway QA Browser Deploy Investigation

Date: 2026-06-02

## Scope

This investigation was view-only.

No services were deleted. No databases were deleted. No secrets were rotated. No
variables were edited. No redeploy or restart was triggered. No production data
was modified.

## Goal

Find why Railway cannot deploy latest `main`, so the pushed Pulse Status and
Reels fixes can reach production.

## Current Production State

Production is still running the last successful deployment:

- Service: `CoinPilotX`
- Active deployment: `c33682b6-ad06-4029-9f53-22b787fdb5ac`
- Active deployment message: `Run full system audit and harden CoinPilotX Pulse foundation`
- Active deployment source: GitHub
- Active deployment age shown in dashboard: about 2 days
- Active status: deployment successful

Latest failed deployment:

- Deployment: `d7a45daf-5db5-4964-8ee6-e4dc80cbf122`
- Commit/message: `Fix Pulse Status media save persistence`
- Source: GitHub
- Dashboard state: failed
- Failure stage: build process
- Duration: about 6 seconds
- Error displayed in dashboard: `install mise packages: python` / `secret Access not found`

Recent failed deployments show the same pattern across multiple commits after the
active deployment, including:

- `Fix Pulse Status media save persistence`
- `Record authenticated Cloudflare upload retest`
- `Document Railway upload fix deployment blocker`
- `Handle Cloudflare media upload blocks`
- `Fix Pulse media uploads`

## Dashboard Evidence

The Railway dashboard shows:

- Builder: `Railpack`
- Builder package: `python@3.13.13`
- Branch: `main`
- Auto deploys: enabled
- Custom build command: none visible
- Start command comes from `Procfile`
- Public domain: `coinpilotx.app`
- Dashboard incident banner: `Dashboard Logs Loading Slowly. We have pushed a fix and are now monitoring the incident.`

The failed deployment details panel shows:

```text
Deployment failed during build process
Build image
Failed to build an image.
install mise packages: python
secret Access not found
```

## Build Log Evidence

Latest failed build log:

```text
using build driver railpack-v0.26.0
Detected Python
Using pip
Found web command in Procfile
Packages: python 3.13.13 railpack default (3.13)
install: python -m venv /app/.venv
install: pip install -r requirements.txt
install mise packages: python
secret Access not found
Build Failed: build daemon returned an error < failed to solve: secret Access not found >
```

Important detail:

- The build fails before `pip install -r requirements.txt`.
- The build fails before application startup.
- The build fails before deploy/post-deploy stages.
- The error occurs inside Railpack/BuildKit/mise package setup.

## Last Successful Build Comparison

Last successful active deployment used:

- Build driver: `railpack-v0.24.0`
- Package: `python 3.13.13`
- Step: `install mise packages: python`
- The Python/mise install step was cached successfully.
- The build continued through:
  - `python -m venv /app/.venv`
  - `pip install -r requirements.txt`
  - image export
  - image push

Latest failed deployment used:

- Build driver: `railpack-v0.26.0`
- Package: `python 3.13.13`
- Fails at `install mise packages: python`
- The failing step is not completing from cache.

This points to a Railpack/BuildKit/mise build-time issue, not an application
runtime issue.

## Repository Build Configuration

Repository files checked:

- `Procfile`
- `requirements.txt`
- `nixpacks.toml`
- scripts and CI-like files

Relevant repository content:

```toml
# nixpacks.toml
[phases.setup]
nixPkgs = ["python311", "ffmpeg"]
```

```text
# Procfile
web: sh -c 'gunicorn bot:app --bind 0.0.0.0:${PORT:-8080} --workers 2 --threads 4 --timeout 120 --access-logfile - --error-logfile -'
undx_worker: python undx_worker.py
```

Repository findings:

- No Dockerfile was found.
- No BuildKit `--mount=type=secret` usage was found.
- No repository `mise.toml` or `.mise.toml` file was found.
- No private pip index or private npm registry config was found.
- The checked-in build config does not request a secret named `Access`.
- The checked-in build config does request `ffmpeg` through `nixpacks.toml`, but
  the active Railway service is currently using Railpack, not a visible Nixpacks
  builder.

## Railway Variable Name Findings

Variable values were not viewed or exposed.

Variable names were inspected because the build error references a missing
secret named `Access`.

Invalid/non-shell-safe service variable names found:

```text
Access Token ID
MUX ACCESS TOKEN
MUX SECRET KEY
```

These names contain spaces and are not valid normal environment variable names.
They are the strongest service-level clue because the BuildKit error is:

```text
secret Access not found
```

That error looks like a build secret reference being split at the first word of
`Access Token ID`.

Code expectation for Mux:

```text
MUX_TOKEN_ID
MUX_TOKEN_SECRET
```

Current service variable names:

```text
MUX ACCESS TOKEN
MUX SECRET KEY
```

Those current Mux variable names are not read by the application code and may
also confuse Railpack/BuildKit secret mounting.

## Most Likely Cause

Most likely category: **Mixed Railway/service configuration issue**

The repository does not request a secret named `Access`, but the Railway service
contains a variable named `Access Token ID`. Railpack/BuildKit appears to be
trying to mount or resolve build secrets and failing on `Access`, likely because
one or more variable names contain spaces.

Contributing factor:

- Railpack changed from `v0.24.0` on the last successful build to `v0.26.0` on
  the latest failed build.
- The newer Railpack/BuildKit path may be stricter about secret identifiers or
  may no longer tolerate variables with spaces.

Secondary factor:

- `ffmpeg` is requested in `nixpacks.toml`, but the dashboard uses Railpack.
- Latest build plan lists only Python, not `ffmpeg`.
- This does not appear to cause the current `secret Access not found` error, but
  it should be corrected after deployment is unblocked so media/Reels processing
  is reliable.

## What It Is Not

Current evidence does not support:

- Flask/Python app code causing the deploy failure.
- Pulse Status code causing the deploy failure.
- Reels code causing the deploy failure.
- `requirements.txt` dependency failure.
- Database migration failure.
- Cloudflare issue.
- Runtime healthcheck failure.

The build fails before those systems run.

## Recommended Recovery Plan

### Step 1: Fix invalid Railway variable names

In Railway service variables, create valid replacement variables before deleting
anything.

Recommended replacements:

```text
Access Token ID -> determine intended provider, then rename to a shell-safe key
MUX ACCESS TOKEN -> MUX_TOKEN_ID
MUX SECRET KEY -> MUX_TOKEN_SECRET
```

Important:

- Copy values carefully.
- Do not expose values in logs or screenshots.
- Do not delete the old variables until the replacement variables are saved and
  confirmed.
- After confirmation, remove or rename the invalid variables with spaces.

Why this is the first fix:

- The build error says `secret Access not found`.
- The service has a variable named `Access Token ID`.
- BuildKit secret IDs cannot safely contain spaces.
- The app code does not read the spaced Mux variable names anyway.

### Step 2: Confirm builder/package configuration

After variable names are fixed, run one normal GitHub auto-deploy or manually
redeploy the latest main commit.

Expected build behavior:

- `install mise packages: python` completes.
- `python -m venv /app/.venv` runs.
- `pip install -r requirements.txt` runs.
- Image export and image push complete.

### Step 3: Restore ffmpeg availability for Reels/media

After the build is unblocked, verify how Railway Railpack should install
`ffmpeg` for this service.

Options:

1. Add a Railway-supported Railpack package variable for `ffmpeg` if this
   workspace uses Railpack package variables.
2. Switch the service builder back to Nixpacks if the intended source of truth is
   `nixpacks.toml`.
3. Use Railway support/docs to confirm the current Railpack equivalent of:

```text
RAILPACK_DEPLOY_APT_PACKAGES=ffmpeg
```

Do this after the `secret Access not found` issue is resolved.

### Step 4: Verify production Pulse fixes

Once the deployment succeeds:

1. Open `/pulse/status`.
2. Test text-only Status.
3. Test image Status.
4. Test `.mov` Status.
5. Test text + media Status.
6. Open `/pulse/reels?tab=for_you`.
7. Confirm the rebuilt upload modal shows `Upload Reel Video`.
8. Test MP4 and MOV preview.
9. Confirm Publish enables only after video selection.
10. Publish a Reel and confirm it appears/plays.

## Exact Recommended Fix

The next manual Railway change should be:

1. Add valid duplicates for the invalid variables:
   - `MUX_TOKEN_ID`
   - `MUX_TOKEN_SECRET`
   - a valid replacement for `Access Token ID`, based on what provider it
     belongs to
2. Remove or rename the invalid variables:
   - `Access Token ID`
   - `MUX ACCESS TOKEN`
   - `MUX SECRET KEY`
3. Trigger a redeploy of latest `main`.

If the build still fails after removing invalid variable names, open a Railway
support ticket with:

- Project/service: CoinPilotX production service
- Failed deployment: `d7a45daf-5db5-4964-8ee6-e4dc80cbf122`
- Active successful deployment: `c33682b6-ad06-4029-9f53-22b787fdb5ac`
- Last successful Railpack: `v0.24.0`
- Failed Railpack: `v0.26.0`
- Error: `install mise packages: python` / `secret Access not found`
- Confirmation that the repository has no Dockerfile, no BuildKit secret mounts,
  no `mise.toml`, and no checked-in build secret named `Access`

## Conclusion

The deployment failure is not caused by Pulse Status, Reels, feed publishing, or
application runtime code.

The most likely blocker is Railway Railpack/BuildKit failing to resolve a build
secret due to invalid service variable names with spaces, especially
`Access Token ID`. Clean the variable names, then redeploy latest `main`.

## Resolution Update

After the Railway service variables were cleaned up:

- Latest CoinPilotX deployment: `98a0e88f-fae0-4f8d-862f-d6bb86c27163`
- Status: `SUCCESS`
- Time: 2026-06-02 13:18:03 -04:00
- Invalid variable names with spaces: no longer observed
- Expected Mux variables are present:
  - `MUX_TOKEN_ID`
  - `MUX_TOKEN_SECRET`

This confirms the `secret Access not found` deployment blocker was caused by
Railway/service variable configuration rather than application code.
