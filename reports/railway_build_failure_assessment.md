# Railway Build Failure Assessment

## Authenticated Follow-Up: 2026-06-01

Railway production access is restored.

Verified services include:

- `CoinPilotX`
- `coinpilotx-media-engine`
- `coinpilotx-pulse-worker`
- `coinpilotx-undx-worker`
- `Postgres`

The build failure remains active on `coinpilotx-media-engine`.

Exact failed deployment evidence:

- Deployment `867edcf0-97df-45b5-95c6-fe3443f7f233`
- Commit `a4456b69a064eae54293be1cc9b98bd366be8872`
- Status: `FAILED`
- Build driver: `railpack-v0.24.0`

Important build log lines:

```text
using build driver railpack-v0.24.0
install mise packages: python
install apt packages: ffmpeg
[ERRO] install mise packages: python
[ERRO] install apt packages: ffmpeg
Build Failed: build daemon returned an error < failed to solve: secret Access not found >
```

The media-engine service now has `RAILPACK_DEPLOY_APT_PACKAGES` present with `ffmpeg` configured, but Railway still fails during Railpack package setup before application startup.

Current classification:

| Category | Classification | Evidence |
| --- | --- | --- |
| Infrastructure issue | Confirmed | Failure happens inside Railpack package setup before app startup. |
| Missing secret issue | Confirmed at Railway build layer | BuildKit/Railpack reports `secret Access not found`; no repository file requests that secret. |
| Railway platform issue | Likely | The failure happens while installing managed Python/apt packages. |
| Application issue | Not supported | Source has no Dockerfile, BuildKit secret mount, private package registry, or mise config requesting `Access`. |

Phase 2 impact:

- `ffmpeg` cannot be proven in production because the deploy that installs it cannot complete.
- The last successful media-engine deployment continues to run and logs `ffmpeg present= False`.
- Media upload to R2/CDN can pass independently, but transcoding, thumbnails, and replay processing remain blocked.

## Latest Investigation Update

Date: 2026-06-01

Scope requested:

- Compare current Railway variables against the last known working deployment.
- Inspect project-level, service-level, and shared variables.
- Check whether a deleted or renamed variable is being requested externally.
- Attempt redeploy of the last successful commit.
- Capture exact build log lines immediately before the failure.
- Classify the issue as infrastructure, missing secret, Railway platform, or application.

## Current Access Result

Railway-side verification is currently blocked by local Railway CLI state:

```text
railway whoami
Warning: failed to refresh OAuth token: Token refresh failed: invalid_grant: grant request is invalid. Please run `railway login` again.
Unauthorized. Please run `railway login` again.
```

```text
railway status
Warning: failed to refresh OAuth token: Token refresh failed: invalid_grant: grant request is invalid. Please run `railway login` again.
No linked project found. Run railway link to connect to a project
```

The same blocker prevented these required Railway checks from completing:

```text
railway variable list --service coinpilotx-undx-worker
railway deployment list --service coinpilotx-undx-worker
railway logs --build --latest --service coinpilotx-undx-worker --lines 80
railway redeploy --service coinpilotx-undx-worker --yes
```

Observed result:

```text
Warning: failed to refresh OAuth token: Token refresh failed: invalid_grant: grant request is invalid. Please run `railway login` again.
No linked project found. Run railway link to connect to a project
```

No Railway variables, shared variables, deployment metadata, build logs, or redeploy result could be inspected from this local environment until Railway is re-authenticated and the project is linked.

## Repository Evidence Rechecked

The repository still contains only:

- `nixpacks.toml`
- `Procfile`
- `requirements.txt`

The repository still does not contain:

- `Dockerfile`
- `Dockerfile.*`
- `railway.json`
- `.railway/*`
- `mise.toml`
- `.mise.toml`
- `.python-version`
- `runtime.txt`
- `.npmrc`
- `pip.conf`
- private package registry configuration
- BuildKit secret mount configuration

Current `nixpacks.toml`:

```toml
[phases.setup]
nixPkgs = ["python311", "ffmpeg"]
```

This asks Railway/Nixpacks for public system packages only. It does not request a secret named `Access`, any BuildKit secret, or any `mise` package configuration.

## Required Railway-Side Checks Still Pending

These checks must be performed after `railway login` and `railway link`:

1. Compare project-level variables from the failing deployment to the last successful deployment.
2. Compare service-level variables for the web service and `coinpilotx-undx-worker`.
3. Inspect shared variables for references to a missing or renamed key named `Access`.
4. Inspect Railway variable references such as `${{ Access }}`, `${{shared.Access}}`, or any equivalent Railway reference syntax.
5. Capture the failed deployment ID and last successful deployment ID.
6. Pull build logs for the failed deployment with at least 80 lines before:

   ```text
   install mise packages: python
   failed to solve:
   secret Access not found
   ```

7. Redeploy the exact last successful deployment or commit and compare result.

## Current Root-Cause Classification

| Category | Classification | Evidence |
| --- | --- | --- |
| Infrastructure issue | Likely | Failure happens during Railway build setup, before app startup. Same commit reportedly deployed successfully earlier. |
| Missing secret issue | Possible, Railway-side only | The message `secret Access not found` suggests a secret named or labeled `Access`, but no repository file requests it. |
| Railway platform issue | Likely | The error occurs while Railway is installing managed `mise`/Python packages, not while running app code. |
| Application issue | Not supported by current evidence | No Dockerfile, BuildKit secret, mise config, private registry, or app build instruction references `Access`. |

## Current Determination

The failure is still best classified as a Railway infrastructure/platform-secret resolution issue, not an application-code issue.

The only unresolved question is whether the missing `Access` secret is:

1. a Railway internal/platform secret used by the build system, or
2. a Railway project/service/shared variable reference that was deleted, renamed, or moved outside this repository.

That distinction cannot be proven from source control. It requires authenticated Railway dashboard or CLI access.

## Exact Next Action Needed

Run these after authenticating Railway:

```text
railway login
railway link
railway status
railway deployment list --service coinpilotx-undx-worker
railway logs --build --latest --service coinpilotx-undx-worker --lines 120
railway variable list --service coinpilotx-undx-worker
```

When inspecting variables, do not paste raw secret values into reports. Record only:

- variable name
- scope: project, shared, or service
- present: yes/no
- references another variable: yes/no
- changed since last working deployment: yes/no

Do not modify application code unless the authenticated Railway evidence proves a repository-level setting is involved.

## Summary

Category: **A. Infrastructure Failure**

The reported Railway deployment failure is most consistent with a Railway/Nixpacks/BuildKit infrastructure or platform-secret resolution failure, not an application-code or repository-configuration failure.

The strongest evidence is the Railway diagnostic:

> "The same commit deployed successfully 16 minutes earlier."

That indicates the application source and build configuration for the failing commit had already produced a successful deployment shortly before the failure. The reported build error:

```text
install mise packages: python

Build Failed:
failed to solve:
secret Access not found
```

also occurs during Railway's build image/package setup phase, before application startup. The repository does not define BuildKit secret mounts or custom build-secret requirements.

## Files Audited

Build and deployment files found in the repository:

- `nixpacks.toml`
- `Procfile`
- `requirements.txt`

Build and deployment files not found:

- `Dockerfile`
- `Dockerfile.*`
- `railway.json`
- `.railway/*`
- `.github/*`
- `mise.toml`
- `.mise.toml`
- `pyproject.toml`
- `package.json`
- `package-lock.json`
- `.npmrc`
- `pip.conf`

## Repository Build Configuration

### `nixpacks.toml`

```toml
[phases.setup]
nixPkgs = ["python311", "ffmpeg"]
```

Assessment:

- Uses Railway/Nixpacks managed packages only.
- Requests public Nix packages: `python311` and `ffmpeg`.
- Does not reference secrets.
- Does not reference `mise`.
- Does not reference private package registries.
- Does not configure BuildKit secret mounts.

### `Procfile`

```text
web: sh -c 'gunicorn bot:app --bind 0.0.0.0:${PORT:-8080} --workers 2 --threads 4 --timeout 120 --access-logfile - --error-logfile -'
undx_worker: python undx_worker.py
```

Assessment:

- Runtime process definitions only.
- No build-time secret usage.
- No private registry usage.
- No Docker/BuildKit secret usage.

### `requirements.txt`

Assessment:

- Contains public Python package requirements only.
- No `--index-url`.
- No `--extra-index-url`.
- No private package registry.
- No embedded credentials.
- No build-secret references.

## Secret Usage Search Results

Searched for repository references to:

- `--mount=type=secret`
- `type=secret`
- `BuildKit`
- `BUILDKIT`
- `RUN --mount`
- build secrets
- `secret Access`
- `.npmrc`
- `_authToken`
- `NPM_TOKEN`
- `GITHUB_TOKEN`
- `PIP_INDEX_URL`
- `PIP_EXTRA_INDEX_URL`
- `index-url`
- `extra-index-url`
- private package registries
- `registry=`
- `mise`

Findings:

- No BuildKit secret mount usage was found.
- No Dockerfile exists in the repository.
- No `.npmrc` exists in the repository.
- No `pip.conf` exists in the repository.
- No private pip or npm registry configuration was found.
- No repository-controlled `mise.toml` or `.mise.toml` file was found.
- The only relevant `mise` signal is from Railway's own build output: `install mise packages: python`.

## A. Infrastructure Failure

Likelihood: **High**

Evidence:

1. Railway reports that the same commit deployed successfully 16 minutes earlier.
2. The error occurred during the platform build/package setup phase:

   ```text
   install mise packages: python
   failed to solve:
   secret Access not found
   ```

3. The repository does not contain a Dockerfile or any BuildKit secret mount directive.
4. The repository does not contain private package registry configuration.
5. The repository does not request build secrets in `nixpacks.toml`, `Procfile`, or `requirements.txt`.

Interpretation:

The `secret Access not found` message appears to originate from Railway's build infrastructure or BuildKit secret resolution, not from repository-authored build instructions. Since the same commit deployed successfully shortly before the failure, this points to a transient platform/build environment issue or a Railway-side missing internal build secret rather than a code regression.

## B. Repository Failure

Likelihood: **Low**

Evidence against repository failure:

1. No BuildKit secret mount syntax exists in the repository.
2. No custom Dockerfile exists.
3. No `railway.json` exists.
4. No mise configuration exists.
5. No private pip/npm registry configuration exists.
6. `requirements.txt` uses public packages.
7. `nixpacks.toml` only asks for public Nix packages.

Possible repository-related risks checked and not found:

- Private pip index requiring a secret.
- Private npm registry requiring a token.
- BuildKit `RUN --mount=type=secret`.
- Railway build secrets declared in project files.
- Custom mise package configuration.

Conclusion:

There is no evidence that repository code or configuration requires a Railway build secret.

## C. Mixed Failure

Likelihood: **Low to Medium**

The only mixed-failure possibility is external to this repository: Railway project-level configuration may reference a build secret named or related to `Access`, or Railway's builder may rely on an internal secret with that name. This cannot be confirmed from repository files because no such reference exists in the checked-in code.

If Railway project settings include custom build variables, build secrets, or deployment templates outside the repository, those should be checked in Railway directly. However, based on the repository audit alone, there is no code-level trigger.

## Determination

Final classification: **A. Infrastructure Failure**

The deployment failure most likely originated from Railway BuildKit/Nixpacks infrastructure or project-level secret resolution, not from application code.

## Recommended Next Actions

1. Retry the Railway deployment for the same commit.
2. If it fails again, inspect Railway project-level build settings for any build secret named `Access` or any custom builder configuration.
3. Open a Railway support ticket with:
   - the failed deployment ID,
   - the successful deployment ID from 16 minutes earlier,
   - the error text `secret Access not found`,
   - confirmation that the repository contains no Dockerfile, BuildKit secret mounts, private registries, or mise config.
4. Do not modify application code to address this specific failure unless Railway identifies a repository-level setting not visible in source control.
