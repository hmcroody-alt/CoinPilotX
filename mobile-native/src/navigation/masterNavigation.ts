export type MasterNavigationActionStatus = "native" | "shell" | "provider" | "gated";

export type MasterNavigationAction = {
  label: string;
  route: string;
  status: MasterNavigationActionStatus;
  description: string;
  badge?: string;
};

export type MasterNavigationSection = {
  title: string;
  description: string;
  actions: MasterNavigationAction[];
};

export const masterNavigationSections: MasterNavigationSection[] = [
  {
    title: "Primary",
    description: "Core PulseSoc command surfaces.",
    actions: [
      { label: "Home", route: "/pulse", status: "native", description: "Native PulseSoc Home and feed." },
      { label: "Dashboard", route: "/pulse/dashboard", status: "native", description: "User dashboard command center." },
      { label: "Search", route: "/pulse/search", status: "native", description: "Find people, posts, groups, commerce, and signals." },
      { label: "Activity Inbox", route: "/pulse/activity", status: "native", description: "Unified notifications, messages, calls, commerce, and safety events." },
      { label: "Settings", route: "/pulse/settings", status: "native", description: "Preferences, account, support, and legal routing." }
    ]
  },
  {
    title: "Social",
    description: "Messaging, identity, network, and communities.",
    actions: [
      { label: "Messages", route: "/pulse/messages", status: "native", description: "Native Messenger inbox." },
      { label: "Calls", route: "/pulse/calls/qa-call-1", status: "shell", description: "Native calls foundation and call room shell." },
      { label: "Profile", route: "/pulse/profile", status: "native", description: "Digital identity hub." },
      { label: "Profile Edit", route: "/pulse/profile/edit", status: "native", description: "Edit public identity and profile state." },
      { label: "Groups", route: "/pulse/groups", status: "native", description: "Communities and rooms." },
      { label: "Saved", route: "/pulse/saved", status: "native", description: "Saved posts, media, and collections." }
    ]
  },
  {
    title: "Creator / Business",
    description: "Publishing, growth, learning, and scheduled content.",
    actions: [
      { label: "Create Post", route: "/pulse/compose", status: "native", description: "Open the Home composer." },
      { label: "Camera", route: "/pulse/camera/photo?target=feed", status: "native", description: "Native Camera Studio entry." },
      { label: "Creator Studio", route: "/pulse/creator-studio", status: "native", description: "Creator dashboard and shortcuts." },
      { label: "Content Planner", route: "/dashboard/creator/content-planner", status: "shell", description: "Planner, scheduler, and draft shell." },
      { label: "Draft Studio", route: "/dashboard/creator/draft-studio", status: "shell", description: "Creator drafts and content staging." },
      { label: "Growth Center", route: "/pulse/growth", status: "native", description: "Promotion, analytics, eligibility, and growth overview." },
      { label: "Courses", route: "/pulse/courses", status: "native", description: "Courses and learning gateway." },
      { label: "Events", route: "/pulse/events", status: "native", description: "Events and scheduled Live gateway." }
    ]
  },
  {
    title: "Content",
    description: "Feed, reels, status, live, and media gateways.",
    actions: [
      { label: "Reels", route: "/pulse/reels", status: "native", description: "Native Reels viewer." },
      { label: "Status", route: "/pulse/status", status: "native", description: "Status viewer and rail." },
      { label: "Add Status", route: "/pulse/status/create", status: "native", description: "Status creator entry." },
      { label: "Live Viewer", route: "/pulse/live", status: "native", description: "Native Live discovery and viewer foundation." },
      { label: "Live Studio", route: "/pulse/live/studio", status: "native", description: "Native pre-flight (device/camera/network readiness + setup) that starts a native Agora broadcast in-app." },
      { label: "Pulse Radio", route: "/pulse/music#pulse-radio", status: "native", description: "Native PulseSoc Music, upload portal, and approved radio pool." }
    ]
  },
  {
    title: "Economy",
    description: "Marketplace, seller, buyer, premium, and orders.",
    actions: [
      { label: "Marketplace", route: "/pulse/marketplace", status: "native", description: "Browse marketplace listings and their photos." },
      { label: "Seller Store", route: "/pulse/seller-store", status: "native", description: "Seller lifecycle and store management." },
      { label: "Create Listing", route: "/pulse/marketplace/create", status: "native", description: "Seller listing composer." },
      { label: "Seller Inventory", route: "/dashboard/economy/seller-tools", status: "shell", description: "Seller-owned listings and inventory controls." },
      { label: "Buyer Orders", route: "/pulse/orders", status: "native", description: "Purchase history and buyer commerce state." },
      { label: "Premium", route: "/pulse/premium", status: "native", description: "Premium plan, entitlements, and billing boundaries." }
    ]
  },
  {
    title: "Intelligence",
    description: "UNDX, alerts, crypto, watchlists, and safety intelligence.",
    actions: [
      { label: "UNDX", route: "/pulse/ai", status: "native", description: "Digital Intelligence Companion.", badge: "LogiNexus" },
      { label: "UNDX Action Center", route: "/pulse/undx/actions", status: "native", description: "Governed actions, approvals, receipts, and Marketplace workflow state." },
      { label: "Intelligence Center", route: "/pulse/intelligence", status: "native", description: "Streams, events, forecasts, sources, and alert overview." },
      { label: "Alert Management", route: "/pulse/alerts", status: "native", description: "Crypto, market, and intelligence alert controls." },
      { label: "Crypto Command", route: "/dashboard/crypto/alerts", status: "shell", description: "Crypto alerts and command-center shell." },
      { label: "Watchlists", route: "/dashboard/crypto/watchlists", status: "shell", description: "Crypto and market watchlist shell." },
      { label: "Scam Shield", route: "/scam-shield/scan", status: "native", description: "Scam and safety scan gateway." }
    ]
  },
  {
    title: "Trust",
    description: "Identity, account, safety, verification, and support.",
    actions: [
      { label: "Account Center", route: "/dashboard/account/settings", status: "native", description: "Account, settings, and preferences." },
      { label: "Security Center", route: "/dashboard/account/security", status: "native", description: "Password, sessions, devices, and active security state." },
      { label: "Privacy Center", route: "/pulse/settings/privacy", status: "native", description: "Privacy settings and boundaries." },
      { label: "Verification", route: "/pulse/verification", status: "native", description: "Identity and badge verification." },
      { label: "Account Health", route: "/pulse/account-health", status: "native", description: "Standing, enforcement history, and appeals." },
      { label: "Safety Hub", route: "/pulse/safety", status: "native", description: "Blocks, mutes, reports, and safety controls." },
      { label: "Support", route: "/pulse/support", status: "native", description: "Trust, safety, and support shell." }
    ]
  },
  {
    title: "Utility",
    description: "Provider and legal boundaries.",
    actions: [
      { label: "Notifications", route: "/pulse/notifications", status: "native", description: "Notification and activity categories." },
      { label: "Notification Preferences", route: "/dashboard/network/notifications", status: "shell", description: "Notification settings shell." },
      { label: "Terms", route: "/terms", status: "provider", description: "Opens on the PulseSoc website." },
      { label: "Privacy Policy", route: "/privacy", status: "provider", description: "Opens on the PulseSoc website." },
      { label: "System Status", route: "/dashboard/system/feed", status: "shell", description: "Native dashboard module shell." }
    ]
  }
];

export function flattenMasterNavigation() {
  return masterNavigationSections.flatMap((section) => section.actions.map((action) => ({ ...action, section: section.title })));
}
