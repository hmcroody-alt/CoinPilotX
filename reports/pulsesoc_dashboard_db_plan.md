# PulseSoc Dashboard Database Plan

The Mission Control dashboard is implemented with a code-driven registry plus additive database tables for future customization.

## Existing Tables Reused

- `users`
- `admin_users`
- `posts`
- `pulse_reels`
- `videos`
- `comments`
- `friendships`
- `conversation_participants`
- `notifications`
- existing creator, marketplace, media, and safety tables when present

All reads are scoped to the current authenticated user unless the user is confirmed as admin/moderator server-side.

## Additive Tables

### `dashboard_widget_access_rules`

Stores future editable widget metadata:

- `widget_key`
- `display_name`
- `category`
- `route`
- `api_endpoint`
- `required_role`
- `premium_required`
- `creator_required`
- `seller_required`
- `admin_only`
- `moderator_only`
- `free_visible_locked`
- `sort_order`
- `is_active`
- `updated_at`

### `user_dashboard_widget_state`

Stores user-specific widget customization:

- `user_id`
- `widget_key`
- `pinned`
- `dismissed`
- `sort_order`
- `updated_at`

### `user_dashboard_metrics`

Stores future cached user metrics:

- `user_id`
- `metric_key`
- `metric_value`
- `computed_at`

### `creator_dashboard_metrics`

Stores future cached creator metrics:

- `user_id`
- `metric_key`
- `metric_value`
- `computed_at`

### `dashboard_events`

Stores future dashboard activity events:

- `user_id`
- `event_type`
- `widget_key`
- `metadata_json`
- `created_at`

### `dashboard_recommendations`

Stores future safe dashboard recommendations:

- `user_id`
- `recommendation_key`
- `title`
- `body`
- `status`
- `created_at`
- `updated_at`

### `dashboard_entitlements`

Stores future entitlement overrides:

- `user_id`
- `entitlement_key`
- `active`
- `source`
- `updated_at`

## Migration Strategy

- All tables are created with `CREATE TABLE IF NOT EXISTS`.
- No existing table is dropped or rewritten.
- Missing optional feature tables do not break dashboard rendering.
- PostgreSQL and SQLite compatibility is preserved by avoiding alias-sensitive SQL and destructive schema changes.

## Access Strategy

- The code registry remains authoritative for this phase.
- Database tables are future-ready for customization and admin-managed dashboard inventory.
- Admin-only widgets remain hidden, not locked, for standard accounts.
- Premium widgets may be visible but locked for free users when `free_visible_locked` is enabled.
