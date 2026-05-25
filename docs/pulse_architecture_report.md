# CoinPilotXAI Pulse Architecture Report

Date: 2026-05-25

## Executive Summary

Pulse is moving from feature-level social tools toward a unified real-time social ecosystem. The correct long-term architecture is: upload once, process in the background, distribute globally through CDN-ready media URLs, render instantly on mobile and desktop, and keep every interaction event-driven and AI-ready.

The current stabilization pass restored Pulse Messenger onto the working Dashboard chat foundation, while preserving direct messages, group chats, and global chat rooms. Pulse Status now supports a Facebook-style creation path: Create Status opens the device media picker, accepts image/video selection, previews the media in a full-screen Pulse editor, exposes story tools, and publishes through the existing status API without changing Pulse branding, colors, or theme.

## Product Philosophy

Pulse should evolve around these principles:

- Upload once, distribute globally.
- Real-time interactions should feel instant, with HTTP fallback when realtime transports fail.
- Media rendering should be deterministic across browsers and devices.
- Mobile should be first-class, not a compressed desktop layout.
- Background processing should handle compression, thumbnails, transcoding, moderation, ranking, and AI enrichments.
- CDN/object storage should be the public media source of truth in production.
- AI systems should attach to canonical events and media records, not scrape unstable UI state.
- Every feature should be event-driven enough to scale from local polling to Redis Pub/Sub, WebSockets, or dedicated realtime services.

## Implemented Foundation

### Pulse Chat

Pulse Messenger now uses the stable Dashboard chat architecture as the active shell:

- Dashboard thread loading through `/api/chat/threads`.
- Dashboard-compatible message load/send bridge through `/api/messages/<conversation_id>`.
- Stable polling through `pollActiveConversation`.
- Direct conversations remain functional.
- Global rooms remain functional through `/api/pulse/messages/room/open` and room message bridges.
- Group chats remain functional through existing Pulse group conversation APIs.
- The broken Pulse-specific recovery shell is not active in the Messenger page.

### Pulse Status / Story Creation

The Create Status path now matches the requested Facebook-style flow while preserving Pulse identity:

- `Create Status` opens the device gallery/media picker directly.
- Image/video selection is supported.
- Selected media opens a full-screen Pulse preview/editor.
- Story tools are present for text, stickers, music, filters, mentions, links, effects, audience, duration, and Share.
- Publishing uses `/api/pulse/status`.
- Upload progress, processing, publishing, success, and retry states use the existing Pulse upload manager.
- Existing Pulse colors, dark theme, gradients, rounded language, and controls are preserved.

## Missing Infrastructure

### Realtime

Current realtime behavior is partly polling/SSE oriented. This is acceptable for stability, but not enough for global scale.

Recommended upgrades:

- Add a dedicated realtime gateway for chat, reactions, typing, presence, live comments, and notifications.
- Use Redis Pub/Sub or Redis Streams as the first shared event bus.
- Add socket authentication refresh and reconnect replay.
- Store event sequence IDs for missed-message recovery.
- Keep HTTP polling fallback for weak networks and mobile Safari.

### Media Pipeline

The codebase has media service abstractions, upload progress, and R2/CDN readiness checks, but production depends on correct environment and worker setup.

Recommended upgrades:

- Enforce `MEDIA_STORAGE_PROVIDER=r2` in production.
- Use Cloudflare R2 bucket `pulse-media`.
- Serve public media through `https://cdn.coinpilotx.app`.
- Add mandatory background jobs for thumbnails, video posters, transcoding, and adaptive renditions.
- Add HLS/LL-HLS generation for long video, live replay, and Reels.
- Keep local storage as development-only.

### Workers and Queues

The repo contains worker foundations and queue-like tables, but production needs a stronger queue layer.

Recommended technologies:

- Redis Queue, Celery, Dramatiq, or RQ for Python background jobs.
- Redis Streams for ordered realtime event replay.
- A dead-letter queue table for failed media, notification, and AI jobs.
- Separate Railway services for web, media worker, realtime worker, and notification worker.

## Performance Bottlenecks

Likely bottlenecks as traffic grows:

- SQLite/local development assumptions will not scale like production Postgres.
- Polling every active user without Pub/Sub will create avoidable backend pressure.
- Large media processing inside request/response paths would cause slow uploads.
- Feed hydration and media metadata must stay compact to preserve mobile speed.
- Long pages need virtualization/windowing as feeds grow.

Recommended optimizations:

- Cache hot feed slices by user/lane/cursor.
- Use stable cursor pagination everywhere.
- Move media processing out of request handlers.
- Precompute reaction/comment counts for hot posts.
- Use compact event payloads for realtime updates.
- Defer AI enrichment to background jobs.

## Security Risks

Primary risks:

- Stream keys and external destination tokens must never be logged or exposed.
- Upload MIME validation must reject unsafe files before storage.
- Public media URLs should avoid leaking local filesystem paths.
- WebSocket joins need authenticated room membership checks.
- Message send endpoints need rate limiting and abuse throttling.
- Story links and captions need scam/link safety scanning.

Recommended controls:

- Encrypt external platform tokens and RTMP keys.
- Add per-user/per-IP upload and message rate limits.
- Validate file type by content sniffing, not filename alone.
- Add signed moderation events for admin actions.
- Keep trace IDs in logs and responses, but never include secrets.

## CDN and Storage Limitations

Production must not rely on Railway ephemeral disk for Pulse media.

Required production environment:

- `MEDIA_STORAGE_PROVIDER=r2`
- `R2_ACCOUNT_ID`
- `R2_ACCESS_KEY_ID`
- `R2_SECRET_ACCESS_KEY`
- `R2_BUCKET=pulse-media`
- `R2_PUBLIC_BASE_URL=https://cdn.coinpilotx.app`

Current local audit can pass with local storage fallback, but production should fail closed if R2 variables are absent.

## Database Inefficiencies and Scaling Risks

Areas to watch:

- Feed ranking, visibility, and reaction counts need indexes and cached counters.
- Chat message queries need indexes by conversation, room, sender, and created time.
- Status expiration needs scheduled cleanup or query-level expiry enforcement.
- Media rows need immutable storage keys and canonical resolved URLs.
- Live sessions need strict state-machine indexes for active discovery.

Recommended upgrades:

- Postgres for production.
- Composite indexes for all high-traffic timelines.
- Read replicas when feed and discovery traffic grows.
- Partition high-volume event/log tables later.

## API Weaknesses

Risks:

- Some endpoints still combine UI concerns and data concerns.
- Realtime fallback depends on stable JSON contracts.
- Upload APIs need consistent progress and retry semantics across feed, Status, Reels, Chat, and Live thumbnails.

Recommended API direction:

- Keep canonical media objects identical across all surfaces.
- Use one message schema for private, room, and group chat.
- Use one visibility resolver for feed, profile, search, status, live, and websocket events.
- Return clear errors with trace IDs and actionable messages.

## Mobile Rendering Notes

Pulse must continue to protect:

- `100dvh` fullscreen surfaces for Status, Reels, Camera, and Live.
- Safe-area top/bottom handling.
- Compact message composer behavior.
- No horizontal overflow.
- Touch-friendly tab and rail behavior.
- Media preview with `object-fit: contain` for user-selected Story media.

## AI Readiness

Pulse is AI-ready when AI systems consume stable events and canonical records:

- Post created.
- Media uploaded.
- Status published.
- Reel watched.
- Message sent.
- Reaction added.
- User followed.
- Live started/ended.

Recommended AI services:

- Feed ranking service.
- Scam/risk classification service.
- Creator coaching service.
- Status caption/story suggestion service.
- Live highlights and replay summary service.
- Search and recommendation embedding service.

## Suggested Architecture Roadmap

### Phase 1: Stabilized Monolith

- Keep Flask monolith.
- Keep Dashboard chat foundation inside Pulse.
- Keep canonical media service.
- Keep status/gallery preview flow.
- Keep audits for chat, media, status, feed, and mobile.

### Phase 2: Durable Media and Background Jobs

- Enforce R2 in production.
- Add media worker queue.
- Generate thumbnails, posters, and responsive variants.
- Add video transcode hooks.
- Add media repair and health dashboards.

### Phase 3: Realtime Gateway

- Add Redis.
- Add websocket/SSE gateway.
- Add event replay by sequence ID.
- Move chat typing, reactions, comments, notifications, and live chat to event bus.

### Phase 4: Feed and Discovery Engine

- Add cursor-based feed services.
- Add hot feed caching.
- Add ranking signals.
- Add creator scoring and anti-spam scoring.
- Add infinite-scroll windowing and stale-while-revalidate behavior.

### Phase 5: Creator and AI Systems

- Add AI story generation pipeline.
- Add live clipping/replay summaries.
- Add recommendations.
- Add creator analytics.
- Add AI moderation and trust scoring.

## Technical Debt Concerns

- `bot.py` is large and still owns too much UI, routing, and business logic.
- Some frontend behavior is inline script instead of modular JS.
- Some production-grade features are scaffolded but need dedicated services.
- Local audit fallbacks can hide missing production R2 configuration.
- Realtime chat should eventually move from polling to shared event streams.

## Recommended Technology Stack

- Postgres for production data.
- Redis for cache, Pub/Sub, rate limits, and event replay.
- Cloudflare R2 for object storage.
- Cloudflare CDN for public media delivery.
- FFmpeg in media workers for video, thumbnails, posters, and HLS.
- Dedicated websocket/SSE worker for realtime.
- Queue worker framework such as RQ, Celery, Dramatiq, or Redis Streams consumers.
- OpenTelemetry-style structured logs and trace IDs.

## Current Verification Coverage

The following audit classes validate the current foundation:

- Chat system load/send/parity/mobile audits.
- Status system and Create Status flow audits.
- Media attachment and integrity audits.
- Feed layout, visibility, and cross-device media audits.
- Full platform audit.
- Site functional and performance audits.

## Final Recommendation

Continue building Pulse as a modular social operating system:

- Keep current Pulse visual identity.
- Move infrastructure toward event-driven services.
- Keep media canonical and CDN-first.
- Keep chat stable before adding voice/media/reactions.
- Add future features behind audits so failures are caught before users see them.

