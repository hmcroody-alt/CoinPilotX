# PulseSoc Ad Review Board Report

## Delivered

- Admin route: `/admin/pulse-ads-review-board`.
- Admin API: `/api/admin/pulse/ads/review-board`.
- Admin actions: approve, reject, needs changes, suspend campaign, disable advertiser account, kill switch.
- Admin finance API: `/api/admin/pulse/ads/finance`.

## Review Flow

1. Advertiser creates creative.
2. Automated policy scan records flags/risk.
3. Advertiser submits creative.
4. Creative enters moderation queue and review board.
5. Admin/moderator approves, rejects, or requests changes.
6. Only approved creatives attached to active/funded campaigns can serve.

## Safety

- Advertisers cannot self-approve.
- Admin actions require admin permissions and CSRF.
- Broad review board list does not expose destination URL.
- Every moderation and finance action writes an audit log where applicable.
