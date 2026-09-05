"""The authorization surface as it stood when a human last looked at it.

This file is a boundary marker. It is not a cache and not a snapshot for
convenience — it exists so that widening what UNDX may do cannot happen without
an edit here, in a diff, that a reviewer has to read.

The rule it enforces is asymmetric on purpose. Narrowing a boundary, or removing
a capability, does not fail the test that reads this file: neither can increase
what the agent is able to reach, and a check that fires on every change teaches
people to regenerate the baseline without reading it. Only widening fails —
risk lowered, confirmation weakened, a capability dropping out of
``HIGH_IMPACT_TOOLS``, scope changed, authentication no longer required, a
verifier or verified field dropped, a feature gate removed, or a capability
becoming reachable that was not here before.

Regenerate with::

    python3 -c "import sys; sys.path.insert(0,'.'); \
from services import undx_capability_registry as r; \
print(r.authorization_baseline())"

but regenerating is the last step of a decision, not a way to make a test pass.

Columns, in order:
    capability_id, risk, confirmation, permission, authorization_scope,
    is_write, requires_authentication, policy_confirms, verifier,
    verified_fields, feature_flag
"""

from typing import Any

AUTHORIZATION_SURFACE: tuple[tuple[Any, ...], ...] = (
    ('account.health.summary', 'read_only', 'never', 'self_account_only', 'self_account_only', False, True, False, '', (), 'UNDX_AGENT_READS_ENABLED'),
    ('activity.daily_summary', 'read_only', 'never', 'self_account_only', 'self_account_only', False, True, False, '', (), 'UNDX_AGENT_READS_ENABLED'),
    ('ads.performance.summary', 'read_only', 'never', 'self_account_only', 'self_account_only', False, True, False, '', (), 'UNDX_AGENT_READS_ENABLED'),
    ('business.campaign.pause', 'reversible_write', 'contextual', 'self_account_only', 'self_account_only', True, True, False, 'campaign_operational_status', ('operational_status',), 'UNDX_AGENT_WRITES_ENABLED'),
    ('business.campaign.resume', 'reversible_write', 'contextual', 'self_account_only', 'self_account_only', True, True, False, 'campaign_operational_status', ('operational_status',), 'UNDX_AGENT_WRITES_ENABLED'),
    ('business.profile.update', 'consequential_write', 'always', 'self_account_only', 'self_account_only', True, True, True, 'business_profile_field_value', ('value',), 'UNDX_AGENT_WRITES_ENABLED'),
    ('comments.list', 'read_only', 'never', 'self_account_only', 'self_account_only', False, True, False, '', (), 'UNDX_AGENT_READS_ENABLED'),
    ('conversations.list', 'read_only', 'never', 'self_account_only', 'self_account_only', False, True, False, '', (), 'UNDX_AGENT_READS_ENABLED'),
    ('conversations.summarize', 'read_only', 'never', 'self_account_only', 'membership_scoped', False, True, False, '', (), 'UNDX_AGENT_READS_ENABLED'),
    ('creator.analytics.summary', 'read_only', 'never', 'self_account_only', 'self_account_only', False, True, False, '', (), 'UNDX_AGENT_READS_ENABLED'),
    ('crypto.alerts.activity', 'read_only', 'never', 'self_account_only', 'self_account_only', False, True, False, '', (), 'UNDX_AGENT_READS_ENABLED'),
    ('crypto.alerts.create', 'consequential_write', 'always', 'self_account_only', 'self_account_only', True, True, True, 'crypto_alert_exists', ('condition', 'threshold'), 'UNDX_AGENT_WRITES_ENABLED'),
    ('crypto.alerts.delete', 'consequential_write', 'always', 'self_account_only', 'self_account_only', True, True, True, 'crypto_alert_deleted', (), 'UNDX_AGENT_WRITES_ENABLED'),
    ('crypto.alerts.get', 'read_only', 'never', 'self_account_only', 'self_account_only', False, True, False, '', (), 'UNDX_AGENT_READS_ENABLED'),
    ('crypto.alerts.list', 'read_only', 'never', 'self_account_only', 'self_account_only', False, True, False, '', (), 'UNDX_AGENT_READS_ENABLED'),
    ('crypto.alerts.pause', 'reversible_write', 'contextual', 'self_account_only', 'self_account_only', True, True, False, 'crypto_alert_status', (), 'UNDX_AGENT_WRITES_ENABLED'),
    ('crypto.alerts.resume', 'reversible_write', 'contextual', 'self_account_only', 'self_account_only', True, True, False, 'crypto_alert_status', (), 'UNDX_AGENT_WRITES_ENABLED'),
    ('crypto.alerts.update', 'consequential_write', 'always', 'self_account_only', 'self_account_only', True, True, True, 'crypto_alert_threshold', ('condition', 'threshold'), 'UNDX_AGENT_WRITES_ENABLED'),
    ('crypto.market.compare', 'read_only', 'never', 'self_account_only', 'public', False, True, False, '', (), 'UNDX_AGENT_READS_ENABLED'),
    ('crypto.market.history', 'read_only', 'never', 'self_account_only', 'public', False, True, False, '', (), 'UNDX_AGENT_READS_ENABLED'),
    ('crypto.market.observations', 'read_only', 'never', 'self_account_only', 'public', False, True, False, '', (), 'UNDX_AGENT_READS_ENABLED'),
    ('crypto.market.overview', 'read_only', 'never', 'self_account_only', 'public', False, True, False, '', (), 'UNDX_AGENT_READS_ENABLED'),
    ('crypto.market.quote', 'read_only', 'never', 'self_account_only', 'public', False, True, False, '', (), 'UNDX_AGENT_READS_ENABLED'),
    ('crypto.market.window', 'read_only', 'never', 'self_account_only', 'self_account_only', False, True, False, '', (), 'UNDX_AGENT_READS_ENABLED'),
    ('crypto.portfolio.history', 'read_only', 'never', 'self_account_only', 'self_account_only', False, True, False, '', (), 'UNDX_AGENT_READS_ENABLED'),
    ('crypto.portfolio.holding.add', 'reversible_write', 'always', 'self_account_only', 'self_account_only', True, True, True, 'crypto_holding_exists', ('amount', 'average_buy_price'), 'UNDX_AGENT_WRITES_ENABLED'),
    ('crypto.portfolio.holding.delete', 'consequential_write', 'always', 'self_account_only', 'self_account_only', True, True, True, 'crypto_holding_deleted', (), 'UNDX_AGENT_WRITES_ENABLED'),
    ('crypto.portfolio.holding.update', 'reversible_write', 'always', 'self_account_only', 'self_account_only', True, True, True, 'crypto_holding_values', ('amount', 'average_buy_price'), 'UNDX_AGENT_WRITES_ENABLED'),
    ('crypto.portfolio.holdings.list', 'read_only', 'never', 'self_account_only', 'self_account_only', False, True, False, '', (), 'UNDX_AGENT_READS_ENABLED'),
    ('crypto.portfolio.summary', 'read_only', 'never', 'self_account_only', 'self_account_only', False, True, False, '', (), 'UNDX_AGENT_READS_ENABLED'),
    ('crypto.watchlist.add', 'reversible_write', 'contextual', 'self_account_only', 'self_account_only', True, True, False, 'crypto_watchlist_contains', (), 'UNDX_AGENT_WRITES_ENABLED'),
    ('crypto.watchlist.list', 'read_only', 'never', 'self_account_only', 'self_account_only', False, True, False, '', (), 'UNDX_AGENT_READS_ENABLED'),
    ('crypto.watchlist.remove', 'reversible_write', 'contextual', 'self_account_only', 'self_account_only', True, True, False, 'crypto_watchlist_contains', (), 'UNDX_AGENT_WRITES_ENABLED'),
    ('events.upcoming', 'read_only', 'never', 'self_account_only', 'self_account_only', False, True, False, '', (), 'UNDX_AGENT_READS_ENABLED'),
    ('feed.comments.summary', 'read_only', 'never', 'self_account_only', 'self_account_only', False, True, False, '', (), 'UNDX_AGENT_READS_ENABLED'),
    ('feed.post.performance.summary', 'read_only', 'never', 'self_account_only', 'self_account_only', False, True, False, '', (), 'UNDX_AGENT_READS_ENABLED'),
    ('feed.posts.delete', 'consequential_write', 'always', 'self_account_only', 'self_account_only', True, True, True, 'feed_post_deleted', (), 'UNDX_AGENT_WRITES_ENABLED'),
    ('feed.posts.get', 'read_only', 'never', 'self_account_only', 'self_account_only', False, True, False, '', (), 'UNDX_AGENT_READS_ENABLED'),
    ('feed.posts.hide', 'reversible_write', 'contextual', 'self_account_only', 'self_account_only', True, True, False, 'feed_post_hidden_value', ('hidden',), 'UNDX_AGENT_WRITES_ENABLED'),
    ('feed.posts.like', 'reversible_write', 'never', 'self_account_only', 'self_account_only', True, True, False, 'feed_post_like_value', ('liked',), 'UNDX_AGENT_WRITES_ENABLED'),
    ('feed.posts.list', 'read_only', 'never', 'self_account_only', 'self_account_only', False, True, False, '', (), 'UNDX_AGENT_READS_ENABLED'),
    ('feed.posts.unlike', 'reversible_write', 'never', 'self_account_only', 'self_account_only', True, True, False, 'feed_post_like_value', ('liked',), 'UNDX_AGENT_WRITES_ENABLED'),
    ('feed.report', 'consequential_write', 'always', 'self_account_only', 'self_account_only', True, True, True, 'content_reported', ('content_type', 'reason'), 'UNDX_AGENT_WRITES_ENABLED'),
    ('groups.list', 'read_only', 'never', 'self_account_only', 'self_account_only', False, True, False, '', (), 'UNDX_AGENT_READS_ENABLED'),
    ('groups.search', 'read_only', 'never', 'self_account_only', 'self_account_only', False, True, False, '', (), 'UNDX_AGENT_READS_ENABLED'),
    ('learning.progress', 'read_only', 'never', 'self_account_only', 'self_account_only', False, True, False, '', (), 'UNDX_AGENT_READS_ENABLED'),
    ('learning.search', 'read_only', 'never', 'self_account_only', 'self_account_only', False, True, False, '', (), 'UNDX_AGENT_READS_ENABLED'),
    ('live.performance', 'read_only', 'never', 'self_account_only', 'self_account_only', False, True, False, '', (), 'UNDX_AGENT_READS_ENABLED'),
    ('live.search', 'read_only', 'never', 'self_account_only', 'self_account_only', False, True, False, '', (), 'UNDX_AGENT_READS_ENABLED'),
    ('live.summary', 'read_only', 'never', 'self_account_only', 'self_account_only', False, True, False, '', (), 'UNDX_AGENT_READS_ENABLED'),
    ('localization.preferences', 'read_only', 'never', 'self_account_only', 'self_account_only', False, True, False, '', (), 'UNDX_AGENT_READS_ENABLED'),
    ('localization.region.update', 'reversible_write', 'contextual', 'self_account_only', 'self_account_only', True, True, False, 'region_preference_value', ('value',), 'UNDX_AGENT_WRITES_ENABLED'),
    ('localization.translation.update', 'reversible_write', 'contextual', 'self_account_only', 'self_account_only', True, True, False, 'translation_preference_value', ('policy',), 'UNDX_AGENT_WRITES_ENABLED'),
    ('marketplace.listing.create', 'consequential_write', 'always', 'self_account_only', 'self_account_only', True, True, True, 'marketplace_listing_created', ('description', 'fulfillment_type', 'price_cents'), 'BUSINESS_OS_MARKETPLACE'),
    ('marketplace.listing.delete', 'consequential_write', 'always', 'self_account_only', 'self_account_only', True, True, True, 'marketplace_listing_status', ('status',), 'BUSINESS_OS_MARKETPLACE'),
    ('marketplace.listing.pause', 'reversible_write', 'contextual', 'self_account_only', 'self_account_only', True, True, False, 'marketplace_listing_status', ('status',), 'BUSINESS_OS_MARKETPLACE'),
    ('marketplace.listing.resume', 'reversible_write', 'contextual', 'self_account_only', 'self_account_only', True, True, False, 'marketplace_listing_status', ('status',), 'BUSINESS_OS_MARKETPLACE'),
    ('marketplace.listing.summary', 'read_only', 'never', 'self_account_only', 'self_account_only', False, True, False, '', (), 'UNDX_AGENT_READS_ENABLED'),
    ('marketplace.listing.update', 'reversible_write', 'contextual', 'self_account_only', 'self_account_only', True, True, False, 'marketplace_listing_field_value', ('field', 'value'), 'BUSINESS_OS_MARKETPLACE'),
    ('marketplace.order.status', 'read_only', 'never', 'self_account_only', 'self_account_only', False, True, False, '', (), 'UNDX_AGENT_READS_ENABLED'),
    ('marketplace.search', 'read_only', 'never', 'self_account_only', 'self_account_only', False, True, False, '', (), 'UNDX_AGENT_READS_ENABLED'),
    ('memory.activity.inspect', 'read_only', 'never', 'self_account_only', 'self_account_only', False, True, False, '', (), 'UNDX_AGENT_READS_ENABLED'),
    ('messages.draft', 'read_only', 'never', 'self_account_only', 'membership_scoped', False, True, False, '', (), 'UNDX_AGENT_READS_ENABLED'),
    ('messages.list', 'read_only', 'never', 'self_account_only', 'membership_scoped', False, True, False, '', (), 'UNDX_AGENT_READS_ENABLED'),
    ('messages.mark_read', 'reversible_write', 'contextual', 'self_account_only', 'membership_scoped', True, True, False, 'conversation_read_state', ('unread_count',), 'UNDX_AGENT_WRITES_ENABLED'),
    ('messages.search', 'read_only', 'never', 'self_account_only', 'membership_scoped', False, True, False, '', (), 'UNDX_AGENT_READS_ENABLED'),
    ('messages.send', 'consequential_write', 'always', 'self_account_only', 'membership_scoped', True, True, True, 'message_exists', ('body', 'message_id'), 'UNDX_AGENT_WRITES_ENABLED'),
    ('messages.suggest', 'read_only', 'never', 'self_account_only', 'membership_scoped', False, True, False, '', (), 'UNDX_AGENT_READS_ENABLED'),
    ('music.search', 'read_only', 'never', 'self_account_only', 'self_account_only', False, True, False, '', (), 'UNDX_AGENT_READS_ENABLED'),
    ('notifications.explain', 'read_only', 'never', 'self_account_only', 'self_account_only', False, True, False, '', (), 'UNDX_AGENT_READS_ENABLED'),
    ('notifications.group_summary', 'read_only', 'never', 'self_account_only', 'self_account_only', False, True, False, '', (), 'UNDX_AGENT_READS_ENABLED'),
    ('notifications.inbox.list', 'read_only', 'never', 'self_account_only', 'self_account_only', False, True, False, '', (), 'UNDX_AGENT_READS_ENABLED'),
    ('notifications.mark_all_read', 'reversible_write', 'always', 'self_account_only', 'self_account_only', True, True, True, 'notifications_unread_count', (), 'UNDX_AGENT_WRITES_ENABLED'),
    ('notifications.mark_read', 'reversible_write', 'contextual', 'self_account_only', 'self_account_only', True, True, False, 'notification_read_state', (), 'UNDX_AGENT_WRITES_ENABLED'),
    ('notifications.preference.read', 'read_only', 'never', 'self_account_only', 'self_account_only', False, True, False, '', (), 'UNDX_AGENT_READS_ENABLED'),
    ('notifications.preference.update', 'reversible_write', 'always', 'self_account_only', 'self_account_only', True, True, True, 'notification_preference_value', ('push',), 'UNDX_AGENT_WRITES_ENABLED'),
    ('premium.entitlements', 'read_only', 'never', 'self_account_only', 'self_account_only', False, True, False, '', (), 'UNDX_AGENT_READS_ENABLED'),
    ('premium.status', 'read_only', 'never', 'self_account_only', 'self_account_only', False, True, False, '', (), 'UNDX_AGENT_READS_ENABLED'),
    ('presence.privacy.status', 'read_only', 'never', 'self_account_only', 'self_account_only', False, True, False, '', (), 'UNDX_AGENT_READS_ENABLED'),
    # Private Office. Eight owner-scoped reads and no writes: the facts read,
    # the six Batch C record views, and the Capital Graph portfolio projection.
    # Owner scope is structural (no field names an account), the second lock
    # applies inside the executor, and the record views read through
    # retrieval's general intent at an INTERNAL ceiling. The capital read
    # declares zero fields — nothing can widen it — and relays totals.value as
    # null whenever any holding lacks a live quote.
    ('private.capital.portfolio', 'read_only', 'never', 'self_account_only', 'self_account_only', False, True, False, '', (), 'UNDX_AGENT_READS_ENABLED'),
    ('private.decisions.list', 'read_only', 'never', 'self_account_only', 'self_account_only', False, True, False, '', (), 'UNDX_AGENT_READS_ENABLED'),
    ('private.events.list', 'read_only', 'never', 'self_account_only', 'self_account_only', False, True, False, '', (), 'UNDX_AGENT_READS_ENABLED'),
    ('private.facts.list', 'read_only', 'never', 'self_account_only', 'self_account_only', False, True, False, '', (), 'UNDX_AGENT_READS_ENABLED'),
    ('private.obligations.list', 'read_only', 'never', 'self_account_only', 'self_account_only', False, True, False, '', (), 'UNDX_AGENT_READS_ENABLED'),
    ('private.opportunities.list', 'read_only', 'never', 'self_account_only', 'self_account_only', False, True, False, '', (), 'UNDX_AGENT_READS_ENABLED'),
    ('private.requests.list', 'read_only', 'never', 'self_account_only', 'self_account_only', False, True, False, '', (), 'UNDX_AGENT_READS_ENABLED'),
    ('private.risks.list', 'read_only', 'never', 'self_account_only', 'self_account_only', False, True, False, '', (), 'UNDX_AGENT_READS_ENABLED'),
    ('profile.activity.summary', 'read_only', 'never', 'self_account_only', 'self_account_only', False, True, False, '', (), 'UNDX_AGENT_READS_ENABLED'),
    ('profile.bio.update', 'consequential_write', 'always', 'self_account_only', 'self_account_only', True, True, True, 'profile_bio_value', ('bio',), 'UNDX_AGENT_WRITES_ENABLED'),
    ('profile.block', 'reversible_write', 'contextual', 'other_user_target', 'directed_at_other_user', True, True, False, 'profile_block_value', ('blocked',), 'UNDX_AGENT_WRITES_ENABLED'),
    ('profile.get', 'read_only', 'never', 'self_account_only', 'self_account_only', False, True, False, '', (), 'UNDX_AGENT_READS_ENABLED'),
    ('profile.preferences.update', 'reversible_write', 'contextual', 'self_account_only', 'self_account_only', True, True, False, 'profile_preference_value', ('preferred_language',), 'UNDX_AGENT_READS_ENABLED'),
    ('profile.relationship.summary', 'read_only', 'never', 'self_account_only', 'self_account_only', False, True, False, '', (), 'UNDX_AGENT_READS_ENABLED'),
    ('profile.unblock', 'reversible_write', 'contextual', 'other_user_target', 'directed_at_other_user', True, True, False, 'profile_block_value', ('blocked',), 'UNDX_AGENT_WRITES_ENABLED'),
    ('reels.comment.create', 'consequential_write', 'always', 'self_account_only', 'self_account_only', True, True, True, 'reel_comment_body', ('body',), 'UNDX_AGENT_WRITES_ENABLED'),
    ('reels.comment.delete', 'consequential_write', 'always', 'self_account_only', 'self_account_only', True, True, True, 'reel_comment_deleted', (), 'UNDX_AGENT_WRITES_ENABLED'),
    ('reels.comment.update', 'consequential_write', 'always', 'self_account_only', 'self_account_only', True, True, True, 'reel_comment_body', ('body',), 'UNDX_AGENT_WRITES_ENABLED'),
    ('reels.comments.summary', 'read_only', 'never', 'self_account_only', 'self_account_only', False, True, False, '', (), 'UNDX_AGENT_READS_ENABLED'),
    ('reels.delete', 'consequential_write', 'always', 'self_account_only', 'self_account_only', True, True, True, 'reel_deleted', (), 'UNDX_AGENT_WRITES_ENABLED'),
    ('reels.get', 'read_only', 'never', 'self_account_only', 'self_account_only', False, True, False, '', (), 'UNDX_AGENT_READS_ENABLED'),
    ('reels.like', 'reversible_write', 'contextual', 'self_account_only', 'self_account_only', True, True, False, 'reel_liked_value', ('liked',), 'UNDX_AGENT_READS_ENABLED'),
    ('reels.performance.summary', 'read_only', 'never', 'self_account_only', 'self_account_only', False, True, False, '', (), 'UNDX_AGENT_READS_ENABLED'),
    ('reels.save', 'reversible_write', 'contextual', 'self_account_only', 'self_account_only', True, True, False, 'reel_saved_value', ('saved',), 'UNDX_AGENT_READS_ENABLED'),
    ('reels.search', 'read_only', 'never', 'self_account_only', 'self_account_only', False, True, False, '', (), 'UNDX_AGENT_READS_ENABLED'),
    ('reels.unlike', 'reversible_write', 'contextual', 'self_account_only', 'self_account_only', True, True, False, 'reel_liked_value', ('liked',), 'UNDX_AGENT_READS_ENABLED'),
    ('reels.unsave', 'reversible_write', 'contextual', 'self_account_only', 'self_account_only', True, True, False, 'reel_saved_value', ('saved',), 'UNDX_AGENT_READS_ENABLED'),
    ('saved.items.list', 'read_only', 'never', 'self_account_only', 'self_account_only', False, True, False, '', (), 'UNDX_AGENT_READS_ENABLED'),
    ('saved.post.set', 'reversible_write', 'never', 'self_account_only', 'self_account_only', True, True, False, 'saved_post_value', ('saved',), 'UNDX_AGENT_WRITES_ENABLED'),
    ('search.activity', 'read_only', 'never', 'self_account_only', 'self_account_only', False, True, False, '', (), 'UNDX_AGENT_READS_ENABLED'),
    ('search.content', 'read_only', 'never', 'self_account_only', 'self_account_only', False, True, False, '', (), 'UNDX_AGENT_READS_ENABLED'),
    ('search.global', 'read_only', 'never', 'self_account_only', 'self_account_only', False, True, False, '', (), 'UNDX_AGENT_READS_ENABLED'),
    ('search.messages', 'read_only', 'never', 'self_account_only', 'self_account_only', False, True, False, '', (), 'UNDX_AGENT_READS_ENABLED'),
    ('search.people', 'read_only', 'never', 'self_account_only', 'self_account_only', False, True, False, '', (), 'UNDX_AGENT_READS_ENABLED'),
    ('security.activity.summary', 'read_only', 'never', 'self_account_only', 'self_account_only', False, True, False, '', (), 'UNDX_AGENT_READS_ENABLED'),
    ('security.device.list', 'read_only', 'never', 'self_account_only', 'self_account_only', False, True, False, '', (), 'UNDX_AGENT_READS_ENABLED'),
    ('security.sessions.list', 'read_only', 'never', 'self_account_only', 'self_account_only', False, True, False, '', (), 'UNDX_AGENT_READS_ENABLED'),
    ('settings.appearance.theme.update', 'reversible_write', 'contextual', 'self_account_only', 'self_account_only', True, True, False, 'settings_preference_value', (), 'UNDX_AGENT_WRITES_ENABLED'),
    ('settings.explain', 'read_only', 'never', 'self_account_only', 'self_account_only', False, True, False, '', (), 'UNDX_AGENT_READS_ENABLED'),
    ('settings.inspect', 'read_only', 'never', 'self_account_only', 'self_account_only', False, True, False, '', (), 'UNDX_AGENT_READS_ENABLED'),
    ('settings.privacy.audience.update', 'consequential_write', 'always', 'self_account_only', 'self_account_only', True, True, True, 'settings_preference_value', ('audience',), 'UNDX_AGENT_WRITES_ENABLED'),
    ('settings.recommend', 'read_only', 'never', 'self_account_only', 'self_account_only', False, True, False, '', (), 'UNDX_AGENT_READS_ENABLED'),
    ('social.follow', 'reversible_write', 'never', 'other_user_target', 'directed_at_other_user', True, True, False, 'social_following_value', (), 'UNDX_AGENT_WRITES_ENABLED'),
    ('social.followers.list', 'read_only', 'never', 'self_account_only', 'self_account_only', False, True, False, '', (), 'UNDX_AGENT_READS_ENABLED'),
    ('social.unfollow', 'reversible_write', 'never', 'other_user_target', 'directed_at_other_user', True, True, False, 'social_following_value', (), 'UNDX_AGENT_WRITES_ENABLED'),
    ('status.get', 'read_only', 'never', 'self_account_only', 'self_account_only', False, True, False, '', (), 'UNDX_AGENT_READS_ENABLED'),
    ('status.list', 'read_only', 'never', 'self_account_only', 'self_account_only', False, True, False, '', (), 'UNDX_AGENT_READS_ENABLED'),
    ('status.reaction.summary', 'read_only', 'never', 'self_account_only', 'self_account_only', False, True, False, '', (), 'UNDX_AGENT_READS_ENABLED'),
    ('status.viewer.summary', 'read_only', 'never', 'self_account_only', 'self_account_only', False, True, False, '', (), 'UNDX_AGENT_READS_ENABLED'),
    ('support.tickets.list', 'read_only', 'never', 'self_account_only', 'self_account_only', False, True, False, '', (), 'UNDX_AGENT_READS_ENABLED'),
    ('translation.content.translate', 'read_only', 'never', 'self_account_only', 'self_account_only', False, True, False, '', (), 'UNDX_AGENT_READS_ENABLED'),
    ('verification.status', 'read_only', 'never', 'self_account_only', 'self_account_only', False, True, False, '', (), 'UNDX_AGENT_READS_ENABLED'),
)
