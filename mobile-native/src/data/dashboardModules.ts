export type DashboardModuleStatus = "PRODUCTION_READY" | "BETA" | "PARTIAL" | "COMING_SOON";
export type DashboardModuleAccess = "active" | "locked" | "fallback";
export type DashboardModuleItem = {
  key: string; title: string; description: string; route: string; icon: string; status: DashboardModuleStatus; accent: string; access: DashboardModuleAccess; lockReason?: string; actionLabel: string;
};
export type DashboardModuleGroup = { key: string; title: string; label: string; icon: string; accent: string; modules: DashboardModuleItem[]; };
export type DashboardQuickAction = { label: string; route: string; capability: "all" | "creator" | "free"; };

export const dashboardModuleGroups: DashboardModuleGroup[] = [
  {
    key: "account-command-center", title: "Account Command Center", label: "Account", icon: "ID", accent: "cyan",
    modules: [
      { key: "profile", title: "Profile", description: "Manage your public identity, bio, avatar, and profile surface.", route: "/dashboard/account/profile", icon: "ID", status: "PRODUCTION_READY", accent: "cyan", access: "active", actionLabel: "Manage Account" },
      { key: "verification", title: "Verification", description: "Check email, identity, and premium verification state.", route: "/dashboard/account/verification", icon: "OK", status: "PRODUCTION_READY", accent: "emerald", access: "active", actionLabel: "Manage Account" },
      { key: "account_health", title: "Account Health", description: "Review account safety, recovery, and activity protections.", route: "/dashboard/account/health", icon: "98", status: "PRODUCTION_READY", accent: "emerald", access: "active", actionLabel: "Manage Account" },
      { key: "security", title: "Security", description: "Password, sessions, devices, and sensitive action protection.", route: "/dashboard/account/security", icon: "LK", status: "PRODUCTION_READY", accent: "cyan", access: "active", actionLabel: "Manage Account" },
      { key: "settings", title: "Settings", description: "Profile, notification, privacy, and account settings.", route: "/dashboard/account/settings", icon: "GE", status: "PRODUCTION_READY", accent: "cyan", access: "active", actionLabel: "Manage Account" },
      { key: "advanced_security", title: "Advanced Security", description: "Premium device intelligence and risk hardening.", route: "/dashboard/account/security", icon: "2F", status: "PRODUCTION_READY", accent: "purple", access: "locked", lockReason: "Premium required", actionLabel: "Manage Account" },
      { key: "identity_protection", title: "Identity Protection", description: "Identity, impersonation, username, avatar, and badge protection.", route: "/dashboard/account/identity-protection", icon: "ID", status: "BETA", accent: "purple", access: "locked", lockReason: "Premium required", actionLabel: "Manage Account" },
      { key: "session_intelligence", title: "Session Intelligence", description: "Session trust, active-device behavior, and suspicious session review.", route: "/dashboard/account/session-intelligence", icon: "SI", status: "BETA", accent: "purple", access: "locked", lockReason: "Premium required", actionLabel: "Manage Account" },
      { key: "device_intelligence", title: "Device Intelligence", description: "Known-device trust, stale devices, and redacted push registration health.", route: "/dashboard/account/device-intelligence", icon: "DV", status: "BETA", accent: "purple", access: "locked", lockReason: "Premium required", actionLabel: "Manage Account" },
      { key: "security_timeline", title: "Security Timeline", description: "Safe owner-visible timeline for login, device, profile, verification, and admin events.", route: "/dashboard/account/security-timeline", icon: "TL", status: "BETA", accent: "purple", access: "locked", lockReason: "Premium required", actionLabel: "Manage Account" },
      { key: "threat_detection", title: "Threat Detection", description: "Unusual login, device, identity, and account-risk warnings.", route: "/dashboard/account/threat-detection", icon: "TH", status: "BETA", accent: "red", access: "locked", lockReason: "Premium required", actionLabel: "Manage Account" },
      { key: "login_analytics", title: "Login Analytics", description: "Login history, failed-login trends, new-device counts, and safe region signals.", route: "/dashboard/account/login-analytics", icon: "LA", status: "BETA", accent: "purple", access: "locked", lockReason: "Premium required", actionLabel: "Manage Account" },
    ]
  },
  {
    key: "pulse-network", title: "Pulse Network", label: "Network", icon: "PN", accent: "purple",
    modules: [
      { key: "notifications", title: "Notifications", description: "Friend requests, reactions, follows, security alerts, and updates.", route: "/dashboard/network/notifications", icon: "AL", status: "PRODUCTION_READY", accent: "cyan", access: "active", actionLabel: "Review Network" },
      { key: "messages", title: "Messages", description: "Open Messenger with unread counts and private chat controls.", route: "/dashboard/network/messages", icon: "MS", status: "PRODUCTION_READY", accent: "emerald", access: "active", actionLabel: "Review Network" },
      { key: "friends", title: "Friends", description: "Review friend requests and your connection graph.", route: "/dashboard/network/friends", icon: "FR", status: "PRODUCTION_READY", accent: "cyan", access: "active", actionLabel: "Review Network" },
      { key: "followers", title: "Followers / Following", description: "Inspect your public network counts without exposing private data.", route: "/dashboard/network/followers", icon: "NW", status: "PRODUCTION_READY", accent: "cyan", access: "active", actionLabel: "Review Network" },
      { key: "groups", title: "Groups", description: "Communities and rooms you are allowed to access.", route: "/dashboard/network/groups", icon: "GR", status: "PRODUCTION_READY", accent: "cyan", access: "active", actionLabel: "Review Network" },
      { key: "status_activity", title: "Status Activity", description: "Your status rail, story signals, and viewer activity.", route: "/dashboard/network/status-activity", icon: "ST", status: "PRODUCTION_READY", accent: "purple", access: "active", actionLabel: "Review Network" },
      { key: "community_activity", title: "Community Activity", description: "Your feed participation, replies, and community signals.", route: "/dashboard/network/community-activity", icon: "CM", status: "PRODUCTION_READY", accent: "cyan", access: "active", actionLabel: "Review Network" },
      { key: "network_health", title: "Network Health", description: "Connection, relationship, delivery, audience, community, and trust health.", route: "/dashboard/network/network-health", icon: "MC", status: "BETA", accent: "emerald", access: "active", actionLabel: "Review Network" },
      { key: "delivery_intelligence", title: "Delivery Intelligence", description: "Push, email, SMS, Telegram, socket, retry, and latency diagnostics.", route: "/dashboard/network/delivery-intelligence", icon: "MC", status: "BETA", accent: "emerald", access: "active", actionLabel: "Review Network" },
      { key: "notification_intelligence", title: "Notification Intelligence", description: "Priority learning, quiet-hour guidance, notification fatigue, and useful alert routing.", route: "/dashboard/network/notification-intelligence", icon: "MC", status: "BETA", accent: "purple", access: "active", actionLabel: "Review Network" },
      { key: "relationship_intelligence", title: "Relationship Intelligence", description: "Strong connections, dormant relationships, reconnect guidance, and trust signals.", route: "/dashboard/network/relationship-intelligence", icon: "RI", status: "BETA", accent: "purple", access: "active", actionLabel: "Review Network" },
      { key: "connection_analytics", title: "Connection Analytics", description: "Connection growth, retention, acceptance, friend conversion, and follower conversion.", route: "/dashboard/network/connection-analytics", icon: "CA", status: "BETA", accent: "purple", access: "active", actionLabel: "Review Network" },
      { key: "audience_mapping", title: "Audience Mapping", description: "Interest clusters, creator communities, overlap, expansion, and distribution.", route: "/dashboard/network/audience-mapping", icon: "AM", status: "BETA", accent: "purple", access: "active", actionLabel: "Review Network" },
      { key: "growth_signals", title: "Growth Signals", description: "Growth opportunities, recommended actions, audience momentum, and connection opportunities.", route: "/dashboard/network/growth-signals", icon: "GS", status: "BETA", accent: "emerald", access: "active", actionLabel: "Review Network" },
      { key: "delivery_matrix", title: "Pulse Delivery Matrix", description: "Live communication matrix across notifications, messages, queues, retries, and worker health.", route: "/dashboard/network/delivery-matrix", icon: "MC", status: "BETA", accent: "emerald", access: "active", actionLabel: "Review Network" },
      { key: "network_security", title: "Network Security", description: "Spam, scam, abuse, blocks, mutes, hidden requests, privacy controls, and trust signals.", route: "/dashboard/network/network-security", icon: "MC", status: "BETA", accent: "red", access: "active", actionLabel: "Review Network" },
      { key: "community_intelligence", title: "Community Intelligence", description: "Community health, spam level, moderator health, growth, engagement, and suggested improvements.", route: "/dashboard/network/community-intelligence", icon: "MC", status: "BETA", accent: "purple", access: "active", actionLabel: "Review Network" },
      { key: "creator_reach", title: "Creator Reach", description: "Reach, shares, audience spread, virality, engagement, and network expansion.", route: "/dashboard/network/creator-reach", icon: "MC", status: "BETA", accent: "emerald", access: "active", actionLabel: "Review Network" },
      { key: "connection_recovery", title: "Connection Recovery", description: "Failed requests, broken connections, lost followers, and relationship recovery guidance.", route: "/dashboard/network/connection-recovery", icon: "MC", status: "BETA", accent: "gold", access: "active", actionLabel: "Review Network" },
    ]
  },
  {
    key: "creator-studio", title: "Creator Studio", label: "Creator", icon: "CS", accent: "blue",
    modules: [
      { key: "my_posts", title: "My Posts", description: "Review and manage your published posts.", route: "/dashboard/creator/posts", icon: "PO", status: "PRODUCTION_READY", accent: "cyan", access: "active", actionLabel: "Open" },
      { key: "reels", title: "Reels", description: "Create, review, and manage short-form reels.", route: "/dashboard/creator/reels", icon: "RL", status: "PRODUCTION_READY", accent: "cyan", access: "active", actionLabel: "Open" },
      { key: "videos", title: "Videos", description: "Upload and manage long-form video signals.", route: "/dashboard/creator/videos", icon: "VD", status: "PRODUCTION_READY", accent: "cyan", access: "active", actionLabel: "Open" },
      { key: "statuses", title: "Statuses", description: "Build and inspect immersive status stories.", route: "/dashboard/creator/statuses", icon: "SS", status: "PRODUCTION_READY", accent: "cyan", access: "active", actionLabel: "Open" },
      { key: "live_studio", title: "Live Studio", description: "Go live or schedule a live creator session.", route: "/dashboard/creator/live-studio", icon: "LV", status: "PRODUCTION_READY", accent: "red", access: "locked", lockReason: "Creator access required", actionLabel: "Open" },
      { key: "audience_analytics", title: "Audience Analytics", description: "Premium analytics for reach, retention, and followers.", route: "/dashboard/creator/audience-intelligence", icon: "AN", status: "PRODUCTION_READY", accent: "purple", access: "locked", lockReason: "Premium required", actionLabel: "Open" },
      { key: "content_performance", title: "Content Performance", description: "Track posts, reels, videos, comments, and saves.", route: "/dashboard/creator/content-performance", icon: "CP", status: "PRODUCTION_READY", accent: "purple", access: "locked", lockReason: "Premium required", actionLabel: "Open" },
      { key: "best_posting_time", title: "Best Posting Time", description: "AI-assisted timing insights for creators.", route: "/dashboard/creator/best-posting-time", icon: "BT", status: "PRODUCTION_READY", accent: "purple", access: "locked", lockReason: "Premium required", actionLabel: "Open" },
      { key: "creator_score", title: "Creator Score", description: "Creator readiness and audience health.", route: "/dashboard/creator/creator-score", icon: "SC", status: "PRODUCTION_READY", accent: "emerald", access: "locked", lockReason: "Creator access required", actionLabel: "Open" },
      { key: "creator_tools", title: "Creator Tools", description: "Creator workflows, media, and publishing tools.", route: "/dashboard/creator/creator-tools", icon: "TL", status: "PRODUCTION_READY", accent: "cyan", access: "locked", lockReason: "Creator access required", actionLabel: "Open" },
      { key: "trend_intelligence", title: "Trend Intelligence", description: "Premium trend signals for creators.", route: "/dashboard/creator/trend-intelligence", icon: "TR", status: "BETA", accent: "purple", access: "locked", lockReason: "Premium required", actionLabel: "Open" },
      { key: "content_planner", title: "Content Planner", description: "Plan future posts, reels, videos, and statuses.", route: "/dashboard/creator/content-planner", icon: "PL", status: "PARTIAL", accent: "purple", access: "locked", lockReason: "Premium required", actionLabel: "Open" },
      { key: "post_scheduler", title: "Post Scheduler", description: "Schedule future PulseSoc posts.", route: "/dashboard/creator/post-scheduler", icon: "PS", status: "PARTIAL", accent: "purple", access: "locked", lockReason: "Premium required", actionLabel: "Open" },
      { key: "draft_studio", title: "Draft Studio", description: "Save and manage creator drafts.", route: "/dashboard/creator/draft-studio", icon: "DR", status: "PARTIAL", accent: "purple", access: "locked", lockReason: "Premium required", actionLabel: "Open" },
      { key: "ai_creator_assistant", title: "AI Creator Assistant", description: "AI support for captions and creator workflows.", route: "/dashboard/creator/ai-creator-assistant", icon: "AC", status: "BETA", accent: "purple", access: "locked", lockReason: "Premium required", actionLabel: "Open" },
      { key: "engagement_prediction", title: "Engagement Prediction", description: "Predictive engagement estimates for creator content.", route: "/dashboard/creator/engagement-prediction", icon: "EP", status: "PARTIAL", accent: "purple", access: "locked", lockReason: "Premium required", actionLabel: "Open" },
      { key: "creator_reputation", title: "Creator Reputation", description: "Private creator reputation and trust signals.", route: "/dashboard/creator/creator-reputation", icon: "CR", status: "BETA", accent: "emerald", access: "locked", lockReason: "Premium required", actionLabel: "Open" },
      { key: "viral_opportunity_scanner", title: "Viral Opportunity Scanner", description: "Find safe high-opportunity content windows.", route: "/dashboard/creator/viral-opportunity-scanner", icon: "VO", status: "PARTIAL", accent: "purple", access: "locked", lockReason: "Premium required", actionLabel: "Open" },
    ]
  },
  {
    key: "intelligence", title: "Intelligence", label: "Intelligence", icon: "AI", accent: "emerald",
    modules: [
      { key: "pulse_alerts", title: "Alerts", description: "Review high-confidence signals ranked by relevance, urgency, and your preferences.", route: "/pulse/intelligence", icon: "AL", status: "PRODUCTION_READY", accent: "emerald", access: "active", actionLabel: "View Signals" },
      { key: "pulse_forecasts", title: "Forecasts", description: "See confidence-labeled forecasts only when trusted signals support them.", route: "/pulse/forecasts", icon: "FC", status: "PRODUCTION_READY", accent: "gold", access: "active", actionLabel: "View Signals" },
      { key: "pulse_watchlists", title: "Watchlists", description: "Track assets and topics you care about without changing alert ownership.", route: "/dashboard/crypto/watchlists", icon: "WL", status: "PRODUCTION_READY", accent: "cyan", access: "active", actionLabel: "View Signals" },
      { key: "pulse_advisor", title: "Pulse Advisor", description: "Ask for clear guidance based on safe PulseSoc knowledge and your approved settings.", route: "/dashboard/intelligence/ai-advisor", icon: "AI", status: "PRODUCTION_READY", accent: "purple", access: "active", actionLabel: "View Signals" },
      { key: "security_signals", title: "Security Signals", description: "Review defensive security, patch, scam, and account protection signals.", route: "/pulse/signals/security", icon: "SH", status: "PRODUCTION_READY", accent: "emerald", access: "active", actionLabel: "View Signals" },
      { key: "crypto_signals", title: "Crypto Signals", description: "Follow high-confidence crypto activity without investment recommendations.", route: "/pulse/signals/crypto", icon: "BTC", status: "PRODUCTION_READY", accent: "gold", access: "active", actionLabel: "View Signals" },
      { key: "market_signals", title: "Market Signals", description: "Follow major market and economic events without stock-by-stock noise.", route: "/pulse/signals/market", icon: "MK", status: "PRODUCTION_READY", accent: "cyan", access: "active", actionLabel: "View Signals" },
      { key: "world_events", title: "World Events", description: "Review heavily filtered major world, science, emergency, and infrastructure events.", route: "/pulse/signals/world", icon: "WE", status: "PRODUCTION_READY", accent: "blue", access: "active", actionLabel: "View Signals" },
      { key: "daily_briefing", title: "Daily Briefing", description: "Scan the strongest available signals and forecasts in one compact briefing.", route: "/pulse/briefing", icon: "DB", status: "PRODUCTION_READY", accent: "purple", access: "active", actionLabel: "View Signals" },
    ]
  },
  {
    key: "economy-earnings", title: "Economy & Earnings", label: "Economy", icon: "$", accent: "gold",
    modules: [
      { key: "wallet", title: "Wallet", description: "Balances, transactions, holds, refunds, credits, payment methods, and wallet protection.", route: "/dashboard/economy/wallet", icon: "WL", status: "PRODUCTION_READY", accent: "gold", access: "active", actionLabel: "Manage Economy" },
      { key: "earnings", title: "Earnings", description: "Owner-only earnings, source breakdowns, payout timeline, taxes, and projections.", route: "/dashboard/economy/earnings", icon: "ER", status: "BETA", accent: "gold", access: "locked", lockReason: "Creator access required", actionLabel: "Manage Economy" },
      { key: "marketplace", title: "Marketplace", description: "Products, orders, inventory, refunds, disputes, seller reputation, and fraud checks.", route: "/dashboard/economy/marketplace", icon: "MK", status: "PRODUCTION_READY", accent: "cyan", access: "active", actionLabel: "Manage Economy" },
      { key: "seller_tools", title: "Seller Tools", description: "Seller onboarding, verification, KYC readiness, store policies, and trust state.", route: "/dashboard/economy/seller-tools", icon: "SL", status: "PRODUCTION_READY", accent: "gold", access: "locked", lockReason: "Seller approval required", actionLabel: "Manage Economy" },
      { key: "subscriptions", title: "Subscriptions", description: "Plan state, invoices, renewals, cancellations, history, and entitlements.", route: "/dashboard/economy/subscriptions", icon: "SB", status: "PRODUCTION_READY", accent: "cyan", access: "locked", lockReason: "Premium required", actionLabel: "Manage Economy" },
      { key: "premium", title: "Premium", description: "Premium center, benefits, recommendations, and iOS-safe access state.", route: "/dashboard/economy/premium", icon: "PR", status: "PRODUCTION_READY", accent: "gold", access: "active", actionLabel: "Manage Economy" },
      { key: "creator_revenue", title: "Creator Revenue", description: "Creator revenue sources, trends, sponsorships, and opportunities.", route: "/dashboard/economy/creator-revenue", icon: "RV", status: "PRODUCTION_READY", accent: "gold", access: "locked", lockReason: "Premium required", actionLabel: "Manage Economy" },
      { key: "payouts", title: "Payouts", description: "Payout readiness, verification, schedules, failed payouts, and retry state.", route: "/dashboard/economy/payouts", icon: "PY", status: "PRODUCTION_READY", accent: "gold", access: "locked", lockReason: "Premium required", actionLabel: "Manage Economy" },
      { key: "revenue_analytics", title: "Revenue Analytics", description: "Revenue charts, summaries, projections, seasonality, and benchmarks.", route: "/dashboard/economy/revenue-analytics", icon: "RA", status: "BETA", accent: "gold", access: "locked", lockReason: "Premium required", actionLabel: "Manage Economy" },
      { key: "ad_revenue", title: "Ad Revenue", description: "Advertising eligibility, RPM, CPM, impressions, fill rate, and payout history.", route: "/dashboard/economy/ad-revenue", icon: "AR", status: "COMING_SOON", accent: "gold", access: "locked", lockReason: "Premium required", actionLabel: "Manage Economy" },
      { key: "affiliate_revenue", title: "Affiliate Revenue", description: "Referrals, commissions, conversions, campaign performance, and payout state.", route: "/dashboard/economy/affiliate-revenue", icon: "AF", status: "COMING_SOON", accent: "gold", access: "locked", lockReason: "Premium required", actionLabel: "Manage Economy" },
      { key: "store_analytics", title: "Store Analytics", description: "Sales, visitors, conversion, abandoned carts, repeat customers, and refunds.", route: "/dashboard/economy/store-analytics", icon: "SA", status: "BETA", accent: "gold", access: "locked", lockReason: "Seller approval required", actionLabel: "Manage Economy" },
      { key: "product_intelligence", title: "Product Intelligence", description: "Top products, pricing, inventory alerts, demand prediction, and recommendations.", route: "/dashboard/economy/product-intelligence", icon: "PI", status: "BETA", accent: "gold", access: "locked", lockReason: "Seller approval required", actionLabel: "Manage Economy" },
      { key: "revenue_forecasting", title: "Revenue Forecasting", description: "Monthly/yearly forecasts, best/worst cases, confidence, and recommendations.", route: "/dashboard/economy/revenue-forecast", icon: "RF", status: "COMING_SOON", accent: "gold", access: "locked", lockReason: "Premium required", actionLabel: "Manage Economy" },
    ]
  },
  {
    key: "pulse-radio-media", title: "Pulse Radio & Media", label: "Media", icon: "FM", accent: "purple",
    modules: [
      { key: "pulse_radio", title: "Pulse Radio", description: "Listen to approved PulseSoc music and radio streams.", route: "/pulse/music", icon: "FM", status: "PRODUCTION_READY", accent: "emerald", access: "active", actionLabel: "Open" },
      { key: "music_library", title: "Music Library", description: "Approved tracks, creator-safe sounds, and uploads.", route: "/pulse/music", icon: "MU", status: "PRODUCTION_READY", accent: "cyan", access: "active", actionLabel: "Open" },
      { key: "video_library", title: "Video Library", description: "Your videos and public video discovery.", route: "/pulse/videos", icon: "VL", status: "PRODUCTION_READY", accent: "cyan", access: "active", actionLabel: "Open" },
      { key: "saved_media", title: "Saved Media", description: "Owner-only saved content and media.", route: "/pulse/saved", icon: "SV", status: "PRODUCTION_READY", accent: "cyan", access: "active", actionLabel: "Open" },
      { key: "upload_music", title: "Upload Music", description: "Partner upload workflow for approved music.", route: "/pulse/music", icon: "UP", status: "PRODUCTION_READY", accent: "purple", access: "locked", lockReason: "Premium required", actionLabel: "Open" },
      { key: "playlists", title: "Playlists", description: "Build radio-ready playlists.", route: "/pulse/music", icon: "PL", status: "PRODUCTION_READY", accent: "purple", access: "locked", lockReason: "Premium required", actionLabel: "Open" },
      { key: "radio_studio", title: "Radio Studio", description: "Tools for radio-ready music workflows.", route: "/pulse/music", icon: "RS", status: "BETA", accent: "purple", access: "locked", lockReason: "Premium required", actionLabel: "Open" },
      { key: "creator_music_distribution", title: "Creator Music Distribution", description: "Prepared distribution layer for approved creators.", route: "/pulse/music", icon: "MD", status: "COMING_SOON", accent: "purple", access: "locked", lockReason: "Premium required", actionLabel: "Open" },
      { key: "audio_analytics", title: "Audio Analytics", description: "Track plays, saves, and use count.", route: "/pulse/music", icon: "AA", status: "BETA", accent: "purple", access: "locked", lockReason: "Premium required", actionLabel: "Open" },
      { key: "media_analytics", title: "Media Analytics", description: "Creator media analytics.", route: "/pulse/creator/analytics", icon: "MA", status: "BETA", accent: "purple", access: "locked", lockReason: "Premium required", actionLabel: "Open" },
      { key: "broadcast_tools", title: "Broadcast Tools", description: "Broadcast and live media tool entry point.", route: "/pulse/live", icon: "BT", status: "BETA", accent: "purple", access: "locked", lockReason: "Premium required", actionLabel: "Open" },
      { key: "sponsored_audio", title: "Sponsored Audio", description: "Prepared sponsored audio surface.", route: "/pulse/music", icon: "SA", status: "COMING_SOON", accent: "gold", access: "locked", lockReason: "Premium required", actionLabel: "Open" },
    ]
  },
  {
    key: "crypto-command-center", title: "Crypto Command Center", label: "Crypto", icon: "BTC", accent: "gold",
    modules: [
      { key: "crypto_market_pulse", title: "Market Pulse", description: "BTC, ETH, SOL, market health, sentiment, and provider freshness.", route: "/dashboard/crypto/market-pulse", icon: "BTC", status: "BETA", accent: "gold", access: "active", actionLabel: "Review Crypto" },
      { key: "crypto_create_alert", title: "Create Alert", description: "Create owner-scoped crypto price and 24h movement alerts.", route: "/dashboard/crypto/alerts/create", icon: "+A", status: "PRODUCTION_READY", accent: "emerald", access: "active", actionLabel: "Review Crypto" },
      { key: "crypto_my_alerts", title: "My Alerts", description: "Pause, resume, edit, delete, duplicate, and inspect your crypto alerts.", route: "/dashboard/crypto/alerts", icon: "AL", status: "PRODUCTION_READY", accent: "emerald", access: "active", actionLabel: "Review Crypto" },
      { key: "crypto_watchlists", title: "Watchlists", description: "Create watchlists, add assets, notes, favorites, and alert context.", route: "/dashboard/crypto/watchlists", icon: "WL", status: "PRODUCTION_READY", accent: "gold", access: "active", actionLabel: "Review Crypto" },
      { key: "crypto_ask_ai", title: "Ask Crypto AI", description: "Ask educational crypto questions with safety boundaries.", route: "/dashboard/crypto/ask-ai", icon: "AI", status: "PARTIAL", accent: "purple", access: "active", actionLabel: "Review Crypto" },
      { key: "crypto_portfolio", title: "Portfolio Intelligence", description: "Owner-entered or connected portfolio visibility and risk guidance.", route: "/dashboard/crypto/portfolio", icon: "PF", status: "PARTIAL", accent: "gold", access: "active", actionLabel: "Review Crypto" },
      { key: "crypto_wallet", title: "Wallet", description: "Wallet readiness and safety without exposing private keys, seed phrases, or secrets.", route: "/dashboard/crypto/wallet", icon: "CW", status: "PARTIAL", accent: "gold", access: "active", actionLabel: "Review Crypto" },
      { key: "crypto_market_scanner", title: "Market Scanner", description: "Scan volume, volatility, trend, and market movement signals.", route: "/dashboard/crypto/market-scanner", icon: "MS", status: "BETA", accent: "cyan", access: "active", actionLabel: "Review Crypto" },
      { key: "crypto_whale_alerts", title: "Whale Alerts", description: "Prepared whale intelligence surface; live provider required.", route: "/dashboard/crypto/whale-alerts", icon: "WH", status: "PARTIAL", accent: "purple", access: "active", actionLabel: "Review Crypto" },
      { key: "crypto_trending_coins", title: "Trending Coins", description: "Trending assets from available market data and PulseSoc signals.", route: "/dashboard/crypto/trending", icon: "TR", status: "BETA", accent: "cyan", access: "active", actionLabel: "Review Crypto" },
      { key: "crypto_top_gainers", title: "Top Gainers", description: "Assets with strongest available 24h movement.", route: "/dashboard/crypto/gainers", icon: "UP", status: "BETA", accent: "emerald", access: "active", actionLabel: "Review Crypto" },
      { key: "crypto_top_losers", title: "Top Losers", description: "Assets with weakest available 24h movement.", route: "/dashboard/crypto/losers", icon: "DN", status: "BETA", accent: "red", access: "active", actionLabel: "Review Crypto" },
      { key: "crypto_token_scanner", title: "Token Scanner", description: "Inspect a token symbol or contract with transparent risk limits.", route: "/dashboard/crypto/token-scanner", icon: "TS", status: "BETA", accent: "purple", access: "active", actionLabel: "Review Crypto" },
      { key: "crypto_news", title: "Crypto News", description: "Market news, asset news, source links, and summaries when providers are available.", route: "/dashboard/crypto/news", icon: "NW", status: "PARTIAL", accent: "cyan", access: "active", actionLabel: "Review Crypto" },
      { key: "crypto_economic_calendar", title: "Economic Calendar", description: "Macro and crypto event calendar prepared for reminders.", route: "/dashboard/crypto/calendar", icon: "EC", status: "PARTIAL", accent: "gold", access: "active", actionLabel: "Review Crypto" },
      { key: "crypto_ai_market_analysis", title: "AI Market Analysis", description: "Educational market analysis, risk summary, confidence, and explanations.", route: "/dashboard/crypto/ai-analysis", icon: "MA", status: "PARTIAL", accent: "purple", access: "active", actionLabel: "Review Crypto" },
      { key: "crypto_favorite_coins", title: "Favorite Coins", description: "Favorite assets, notes, alerts, and quick actions.", route: "/dashboard/crypto/favorites", icon: "FV", status: "PRODUCTION_READY", accent: "gold", access: "active", actionLabel: "Review Crypto" },
      { key: "crypto_recent_assets", title: "Recently Viewed Assets", description: "Private recently viewed assets with quick alert and watchlist actions.", route: "/dashboard/crypto/recent", icon: "RC", status: "PRODUCTION_READY", accent: "cyan", access: "active", actionLabel: "Review Crypto" },
    ]
  },
  {
    key: "moderation-safety", title: "Moderation / Safety", label: "Safety", icon: "SH", accent: "emerald",
    modules: [
      { key: "reports_submitted", title: "Reports Submitted", description: "Your submitted reports and support requests.", route: "/support", icon: "RP", status: "PRODUCTION_READY", accent: "cyan", access: "active", actionLabel: "Open" },
      { key: "blocked_users", title: "Blocked Users", description: "People you blocked and privacy controls.", route: "/pulse/profile/security", icon: "BL", status: "PRODUCTION_READY", accent: "cyan", access: "active", actionLabel: "Open" },
      { key: "appeals", title: "Appeals", description: "Submit or review your own moderation appeals.", route: "/support", icon: "AP", status: "PRODUCTION_READY", accent: "cyan", access: "active", actionLabel: "Open" },
      { key: "moderation_status", title: "Moderation Status", description: "Your own moderation and report status only.", route: "/support", icon: "MD", status: "PRODUCTION_READY", accent: "cyan", access: "active", actionLabel: "Open" },
      { key: "content_removals", title: "Content Removals", description: "Owner-visible content action history when available.", route: "/support", icon: "RM", status: "PRODUCTION_READY", accent: "cyan", access: "active", actionLabel: "Open" },
    ]
  },
  {
    key: "ads-sponsorships", title: "Ads & Sponsorships", label: "Ads", icon: "AD", accent: "gold",
    modules: [
      { key: "view_sponsored_signals", title: "Sponsored Signal Intelligence", description: "Approved sponsored signals, transparency, sponsor trust, relevance, privacy explanation, and public feedback.", route: "/dashboard/ads/sponsored-signals", icon: "VS", status: "BETA", accent: "gold", access: "active", actionLabel: "Review Commerce" },
      { key: "ads_manager", title: "Commercial Mission Control", description: "Campaign health, budget pacing, spend, delivery diagnostics, review state, creative health, and recommendations.", route: "/dashboard/ads/manager", icon: "AM", status: "BETA", accent: "gold", access: "active", actionLabel: "Review Commerce" },
      { key: "campaign_builder", title: "Campaign Builder", description: "Guided objectives, budget, schedule, audience, placement, creative, privacy checks, and review-readiness workflow.", route: "/dashboard/ads/campaign-builder", icon: "CB", status: "BETA", accent: "gold", access: "active", actionLabel: "Review Commerce" },
      { key: "sponsored_signal_studio", title: "Sponsored Signal Studio", description: "Creative upload, copy, CTA, media preview, compliance scan, community relevance, and approval readiness.", route: "/dashboard/ads/signal-studio", icon: "SS", status: "BETA", accent: "gold", access: "active", actionLabel: "Review Commerce" },
      { key: "ad_analytics", title: "Ad Analytics", description: "Impressions, viewability, clicks, CTR, spend, creative comparison, placement performance, and conversion events.", route: "/dashboard/ads/analytics", icon: "AN", status: "BETA", accent: "gold", access: "active", actionLabel: "Review Commerce" },
      { key: "brand_deals", title: "Brand Deals", description: "Verified brand and creator partnerships, deliverables, milestones, trust, and performance history.", route: "/dashboard/ads/brand-deals", icon: "BD", status: "BETA", accent: "gold", access: "locked", lockReason: "Creator access required", actionLabel: "Review Commerce" },
      { key: "creator_sponsorships", title: "Creator Sponsorships", description: "Sponsorship readiness, sponsor fit, estimated earnings, application state, and deliverable tracking.", route: "/dashboard/ads/creator-sponsorships", icon: "CS", status: "BETA", accent: "gold", access: "locked", lockReason: "Creator access required", actionLabel: "Review Commerce" },
      { key: "ad_revenue_center", title: "Revenue Intelligence", description: "Projected earnings, spend signals, trend, source mix, creator share, and growth recommendations.", route: "/dashboard/ads/revenue-intelligence", icon: "RC", status: "BETA", accent: "gold", access: "locked", lockReason: "Creator access required", actionLabel: "Review Commerce" },
      { key: "audience_targeting", title: "Audience Targeting", description: "Privacy-first aggregate targeting, contextual clusters, saturation, overlap, timing, language, and device rules.", route: "/dashboard/ads/audience-targeting", icon: "AT", status: "BETA", accent: "gold", access: "active", actionLabel: "Review Commerce" },
      { key: "conversion_tracking", title: "Conversion Tracking", description: "View, click, profile, follow, save, share, comment, purchase, drop-off, and funnel diagnostics.", route: "/dashboard/ads/conversion-tracking", icon: "CT", status: "BETA", accent: "gold", access: "active", actionLabel: "Review Commerce" },
    ]
  },
  {
    key: "pulsesoc-ai", title: "PulseSoc AI", label: "AI", icon: "AI", accent: "purple",
    modules: [
      { key: "undx", title: "UNDX Core Intelligence", description: "Mission workspace, project memory, agent coordination, and execution readiness.", route: "/dashboard/ai/undx", icon: "UX", status: "BETA", accent: "purple", access: "locked", lockReason: "Premium required", actionLabel: "Open AI Mission Control" },
      { key: "ai_assistant", title: "Adaptive AI Companion", description: "Context-aware assistant with privacy-safe guidance and fallback behavior.", route: "/dashboard/ai/assistant", icon: "AI", status: "BETA", accent: "purple", access: "locked", lockReason: "Premium required", actionLabel: "Open AI Mission Control" },
      { key: "ai_research", title: "Research Intelligence Lab", description: "Evidence, confidence, knowledge maps, research memory, and saved findings.", route: "/dashboard/ai/research", icon: "RS", status: "BETA", accent: "purple", access: "locked", lockReason: "Premium required", actionLabel: "Open AI Mission Control" },
      { key: "ai_content_generator", title: "Creative Intelligence Studio", description: "AI-assisted posts, scripts, captions, strategies, presentations, and creator drafts.", route: "/dashboard/ai/creative-studio", icon: "CG", status: "BETA", accent: "purple", access: "locked", lockReason: "Premium required", actionLabel: "Open AI Mission Control" },
      { key: "ai_image_tools", title: "Visual Intelligence Engine", description: "Visual prompts, thumbnail guidance, asset safety, and media-ready image workflows.", route: "/dashboard/ai/visual-engine", icon: "IM", status: "BETA", accent: "purple", access: "locked", lockReason: "Premium required", actionLabel: "Open AI Mission Control" },
      { key: "ai_music_tools", title: "Music Intelligence Studio", description: "Music recommendations, mood, metadata, radio readiness, and creator sound guidance.", route: "/dashboard/ai/music-studio", icon: "MU", status: "BETA", accent: "purple", access: "locked", lockReason: "Premium required", actionLabel: "Open AI Mission Control" },
      { key: "ai_video_tools", title: "Video Intelligence Studio", description: "Video scripts, reels, captions, thumbnails, pacing, and publishing readiness.", route: "/dashboard/ai/video-studio", icon: "VI", status: "BETA", accent: "purple", access: "locked", lockReason: "Premium required", actionLabel: "Open AI Mission Control" },
      { key: "ai_intelligence_center", title: "AI Mission Control", description: "Cross-platform AI hub for safe summaries, recommendations, automations, and system health.", route: "/dashboard/ai/mission-control", icon: "IC", status: "BETA", accent: "purple", access: "locked", lockReason: "Premium required", actionLabel: "Open AI Mission Control" },
    ]
  },
  {
    key: "system-status", title: "System Status", label: "System", icon: "SYS", accent: "cyan",
    modules: [
      { key: "feed_status", title: "Feed Intelligence", description: "Feed latency, ranking health, realtime hydration, discovery confidence, and prediction graph.", route: "/dashboard/system/feed", icon: "FD", status: "BETA", accent: "emerald", access: "active", actionLabel: "Review System" },
      { key: "messenger_status", title: "Messenger Intelligence", description: "Realtime delivery, receipts, typing, push health, media delivery, and network quality.", route: "/dashboard/system/messenger", icon: "MS", status: "BETA", accent: "cyan", access: "active", actionLabel: "Review System" },
      { key: "live_status", title: "Live Network", description: "Streams, viewers, encoder health, Agora, Mux, bitrate, recording, audio, and prediction.", route: "/dashboard/system/live", icon: "LV", status: "BETA", accent: "emerald", access: "active", actionLabel: "Review System" },
      { key: "radio_status", title: "Radio Intelligence", description: "Listeners, now playing, buffer health, creator uploads, recommendations, and licensing readiness.", route: "/dashboard/system/radio", icon: "FM", status: "BETA", accent: "purple", access: "active", actionLabel: "Review System" },
      { key: "marketplace_status", title: "Marketplace Intelligence", description: "Listings, orders, transactions, fraud detection, seller health, revenue, and trust score.", route: "/dashboard/system/marketplace", icon: "MK", status: "BETA", accent: "gold", access: "active", actionLabel: "Review System" },
      { key: "notifications_status", title: "Notification Intelligence", description: "Push, SMS, email, in-app, retries, queues, failures, delivery rate, and prediction.", route: "/dashboard/system/notifications", icon: "NT", status: "BETA", accent: "gold", access: "active", actionLabel: "Review System" },
      { key: "ai_status", title: "AI Intelligence", description: "Models, routing, memory, knowledge graph, agents, context, and response health.", route: "/dashboard/system/ai", icon: "AI", status: "BETA", accent: "purple", access: "active", actionLabel: "Review System" },
      { key: "scam_shield_status", title: "Scam Shield", description: "Threats, analyzed signals, confidence, risk map, realtime alerts, and community safety.", route: "/dashboard/system/scam-shield", icon: "SH", status: "BETA", accent: "emerald", access: "active", actionLabel: "Review System" },
      { key: "ads_status", title: "Advertising Intelligence", description: "Campaigns, delivery, revenue, conversions, brand safety, budget pacing, and optimization.", route: "/dashboard/system/advertising", icon: "AD", status: "BETA", accent: "gold", access: "active", actionLabel: "Review System" },
      { key: "creator_studio_status", title: "Creator Intelligence", description: "Publishing, uploads, processing, creator health, performance, revenue, and AI optimization.", route: "/dashboard/system/creator", icon: "CS", status: "BETA", accent: "emerald", access: "locked", lockReason: "Creator access required", actionLabel: "Review System" },
    ]
  },
];

export const dashboardQuickActions: DashboardQuickAction[] = [
  { label: "Create Post", route: "/pulse/compose", capability: "all" },
  { label: "Go Live", route: "/pulse/live/studio", capability: "creator" },
  { label: "Upload Video", route: "/pulse/camera/video?target=feed", capability: "all" },
  { label: "Add Status", route: "/pulse/status?openCreator=1", capability: "all" },
  { label: "Invite Friends", route: "/dashboard/network/friends", capability: "all" },
  { label: "Create Crypto Alert", route: "/dashboard/crypto/alerts/create", capability: "all" },
  { label: "Ask Crypto AI", route: "/dashboard/crypto/ask-ai", capability: "all" },
  { label: "Scan Token", route: "/dashboard/crypto/token-scanner", capability: "all" },
  { label: "Add Watchlist Asset", route: "/dashboard/crypto/watchlists", capability: "all" },
  { label: "Upgrade to Premium", route: "/pulse/premium", capability: "free" },
  { label: "Open Scam Shield", route: "/scam-shield", capability: "all" },
  { label: "Open Pulse Radio", route: "/pulse/music", capability: "all" },
];
