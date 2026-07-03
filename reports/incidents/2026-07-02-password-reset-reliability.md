# Incident: Password Reset Reliability Hardening

## What Happened

Password reset attempts were reported to bring PulseSoc down or into an upstream-error state.

## Root Cause

The active password-reset route performed reset-token creation, email enqueue, and audit logging directly inside the route without a route-local safety boundary. The reset-token table also stored raw reset tokens for active routes.

## Impact

A password-reset support feature could return failures and potentially contribute to worker instability during provider, database, or queue failures.

## Fix

- Added safe password reset request wrapper.
- Isolated email enqueue and audit logging failures.
- Added hashed reset-token storage.
- Added health endpoints that do not depend on optional providers.
- Added reliability audit coverage.

## Prevention

Keep optional provider failures out of public liveness checks and route success paths. Feature failures should be logged with trace context and degraded behavior, not process-level failure.
