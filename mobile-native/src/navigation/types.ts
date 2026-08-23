import { NavigatorScreenParams } from "@react-navigation/native";

import type { MarketplaceListing } from "../api/marketplace";
import type { MarketplaceFulfillmentKind } from "../api/marketplaceFulfillment";

/**
 * Context carried by every destination opened from a Profile OS tile.
 *
 * `profileOwnerId` is the account the destination is about. It is optional in
 * the type because these routes are also reachable from tabs and deep links,
 * where the signed-in user is legitimately the subject — but when the params
 * are absent a screen must treat itself as the viewer's own surface, never
 * silently adopt whichever profile it saw last.
 *
 * `isOwnProfile` is a rendering hint only. Screens re-derive ownership from the
 * ids and gate on server-authored permissions; a client boolean is not a
 * security boundary.
 *
 * See `src/profile/profileContext.ts`.
 */
export type ProfileOsParams = {
  profileOwnerId?: string;
  sourceProfileId?: string;
  isOwnProfile?: boolean;
  entryPoint?: "PROFILE_OS";
  profileLookupKey?: string;
  displayName?: string;
  username?: string;
  avatarUrl?: string;
};

/** The twelve layers of the Progress Center, in the order they are presented. */
export type ProgressCenterSection =
  | "overview"
  | "milestones"
  | "referrals"
  | "invite"
  | "rewards"
  | "missions"
  | "badge"
  | "insights"
  | "activity"
  | "howItWorks"
  | "faq";

export type AuthStackParamList = {
  Login: undefined;
  Signup: undefined;
  AccountRecovery: undefined;
};

export type AppTabParamList = {
  Dashboard: undefined;
  Home: {
    openComposer?: boolean;
    composerMode?: "post" | "status" | "reel";
    composerReturnNonce?: string;
    shareHandoffNonce?: string;
  } | undefined;
  Search: { query?: string } | undefined;
  Saved: undefined;
  Groups: undefined;
  Live: undefined;
  // Params, unlike the root-stack registration of the same screen, because only
  // the tab instance sits inside `BottomNavVisibilityProvider`. A suggested reel
  // opened on the stack plays the right video with a dock that no longer
  // responds to swipes, so exact-reel navigation targets the tab.
  // `reelTransferNonce` separates two taps on the same reel; the reel payload
  // itself rides in `discovery/reelTransfer`, not in navigation state.
  Reels: { reelId?: number; title?: string; reelTransferNonce?: string } | undefined;
  Create: undefined;
  Status: { openCreator?: boolean; statusId?: number } | undefined;
  Messenger: undefined;
  Notifications: undefined;
  PulseAI: { taskId?: string } | undefined;
  Profile: undefined;
  Marketplace: undefined;
  Settings: undefined;
};

export type RootStackParamList = {
  Tabs: NavigatorScreenParams<AppTabParamList> | undefined;
  UserDashboard: { title?: string } | undefined;
  UserDashboardWeb: { title?: string } | undefined;
  DashboardComposeAlias: undefined;
  DashboardMusicAlias: undefined;
  Music: ({ trackId?: string; track?: string; artistId?: number; artist?: number; openUpload?: boolean; title?: string; surface?: "post" | "status" | "reel" } & ProfileOsParams) | undefined;
  PulseQueue: { title?: string } | undefined;
  DashboardLegacyModule: { legacyGroup?: string; legacyModule?: string; legacySubmodule?: string; title?: string } | undefined;
  DashboardModuleDetail: { groupKey: string; moduleKey: string; title?: string };
  CameraStudio: {
    target?: "feed" | "post" | "status" | "reel" | "message" | "avatar" | "cover" | "creator" | "marketplace";
    mode?: "photo" | "video" | "status" | "reel" | "live";
    captureMode?: "photo" | "video" | "live";
    returnToComposer?: boolean;
    composerMode?: "post" | "status" | "reel";
    conversationId?: number;
    title?: string;
    qaMedia?: "image";
    qaAutoPublish?: boolean;
    qaCaption?: string;
  } | undefined;
  Call: {
    callId?: string;
    conversationId?: number;
    callType?: "audio" | "video";
    direction?: "incoming" | "outgoing";
    title?: string;
  } | undefined;
  Chat: {
    conversationId: number;
    roomId?: string;
    title?: string;
    avatarUrl?: string;
    presence?: string;
    openControlCenter?: boolean;
    undxTaskId?: string;
  };
  NewChat: { initialQuery?: string; targetUserId?: number; title?: string } | undefined;
  PulseShare: {
    kind: "post" | "reel" | "status" | "live" | "profile" | "marketplace" | "business" | "event" | "music" | "media";
    url: string;
    title?: string;
    description?: string;
    author?: string;
    previewImageUrl?: string;
  };
  PostDetail: { postId: number; title?: string };
  ProfilePostViewer: {
    profileId: number;
    profileKey: string;
    postId: number;
    postIds: number[];
    nextOffset?: number;
    hasMore?: boolean;
    contentTab?: "posts" | "media";
    owner: boolean;
    source: "PROFILE_GRID";
  };
  Reels: { reelId?: number; title?: string; reelTransferNonce?: string } | undefined;
  ReelDetail: { reelId: number; title?: string; reelTransferNonce?: string };
  StatusDetail: { statusId: number; title?: string };
  MarketplaceDetail: { listingId?: number; sellerUserId?: number; title?: string } | undefined;
  /**
   * The buyer's product page. `listing` travels in the params because there is
   * no read-one endpoint — `/api/pulse/marketplace/search` is the only buyer
   * read path — so refetching here would put a spinner in front of data the
   * caller already holds. `listingId` is carried separately so identity (cart
   * writes, save state, reports) never depends on that snapshot.
   */
  MarketplaceProduct: { listingId: number; listing?: MarketplaceListing; title?: string };
  /**
   * The dark Business OS sections screen. `mode` is retained but currently
   * inert: the light hub redesign was reverted, so every value — including
   * `"hub"` — renders the dark screen while `HUB_LIVE_CARDS` is false.
   *
   * The param is kept rather than dropped because removing it would be a
   * breaking change to a route that push payloads and deep links already
   * target, and because it is the opt-in half of the switch if the redesign is
   * ever revisited. `"classic"` is now a synonym for the default.
   */
  BusinessOs: ({ title?: string; mode?: "hub" | "classic" } & ProfileOsParams) | undefined;
  BusinessProfile: { title?: string } | undefined;
  PulseIdentity: ({ title?: string } & ProfileOsParams) | undefined;
  /**
   * "View as buyer" — the public business profile, read-only, on its own route.
   *
   * A separate route rather than a `preview` param on `BusinessProfile` because the
   * two screens must not share a component tree: the preview is typed against the
   * server's public allowlist, so a component that tried to render `legal_name` or a
   * payout account would fail to compile rather than fail in review.
   *
   * `sellerUserId` is absent for the owner's own preview and set when a buyer opens
   * a real shop — the same screen serves both, because the owner's preview showing
   * anything the buyer's view does not would defeat its purpose.
   */
  BusinessBuyerPreview: ({ sellerUserId?: number; returnScrollY?: number } & ProfileOsParams) | undefined;
  /**
   * The seller-side Marketplace manager (Business "Sections" card #3). Distinct
   * from `Marketplace`/`MarketplaceDetail`, which are the consumer browse
   * surface, and from `MarketplaceCreateGateway`, which is the composer.
   */
  MarketplaceManager: { title?: string } | undefined;
  /**
   * The buyer's cart: server-backed lines grouped per seller, with per-seller
   * checkout — one Stripe session per seller group, matching how the backend
   * charges. Reached from the Marketplace buying header's cart badge.
   */
  MarketplaceCart: { title?: string } | undefined;
  MarketplaceCheckout: {
    mode: "cart" | "buy_now";
    title?: string;
    sellerUserId?: number;
    sellerName?: string;
    listingId?: number;
    itemTitle?: string;
    priceLabel?: string;
    subtotalMinor?: number;
    currency?: string;
    quantity?: number;
    // `both` means the seller offers pickup *or* shipping and the buyer has not
    // chosen yet — the checkout screen makes them choose before it will pay.
    fulfillment?: "digital" | "pickup" | "shipping" | "both";
    // The canonical order type, which decides what the buyer is asked for before
    // payment. `fulfillment` above only separates the physical lanes, so a
    // booking or an event arrives there indistinguishable from a shipped parcel.
    fulfillmentKind?: MarketplaceFulfillmentKind;
    // Ticket tiers the seller published, for the one field a buyer picks from.
    ticketOptions?: string[];
  };
  /**
   * One route, several screens. Default is the rebuilt two-sided ads manager;
   * `mode: "classic"` renders the previous screen, which still owns the
   * ad-account and campaign creation forms the manager routes into.
   *
   * The remaining modes are the manager's sub-pages. They live behind this one
   * route name rather than getting route names of their own so that every
   * existing deep link, notification and dashboard tile keeps resolving through
   * a single entry point — and so that a tile can never be pointed at a name
   * the navigator does not know, which is how a "destination" becomes a crash
   * instead of a page.
   *
   *   • `"audiences"`  — the audience manager: list, create (engagement or
   *     lookalike), detail with size estimate and in-use campaigns, edit,
   *     archive.
   *   • `"creatives"`  — the browsable creative library with filters, asset
   *     detail, metadata editing and "use in campaign".
   *   • `"account"`    — account standing plus the ad account number, which was
   *     removed from the dashboard header and had to land somewhere real.
   *   • `"policy"`     — the Policy Center: account status, review sections,
   *     rejection detail and the appeal flow.
   *   • `"reports"`    — the reporting table: range presets, breakdowns,
   *     totals, CSV export.
   *   • `"wallet"`     — ads wallet & billing: balance, funding, transactions,
   *     invoices, spending limits, auto top-up.
   *   • `"insights"`   — every optimization recommendation with why + Apply.
   */
  BusinessOsAdvertising:
    | {
        title?: string;
        accountId?: number;
        mode?:
          | "manager"
          | "classic"
          | "create"
          | "promote"
          | "audiences"
          | "creatives"
          | "account"
          | "policy"
          | "detail"
          | "reports"
          | "wallet"
          | "insights";
        /** Required when `mode` is `"detail"` — which campaign to open. */
        campaignId?: number;
        /** Presets the wizard's ad surface when `mode` is `"create"`. */
        surface?: "post" | "marketplace";
        /**
         * Seeds the "Promote your content" wizard when `mode` is `"promote"`.
         * References an already-published content object — the promotion boosts
         * the original; nothing is duplicated or reposted.
         */
        promoteContent?: {
          contentType: "post" | "reel" | "live_replay";
          contentId: number;
          title?: string;
          thumbnailUrl?: string;
          mediaKind?: string;
        };
      }
    | undefined;
  BusinessOsInsights: { title?: string } | undefined;
  BusinessOsPayments: { title?: string; accountId?: number } | undefined;
  /**
   * The money layers under the Payments hub — the screens a seller opens to
   * interrogate a figure rather than to read it.
   *
   * One route with a `layer` param rather than five routes, because the five
   * differ only in what they read and what they say; the header, the refresh,
   * the offline handling and the error card are identical, and five copies of
   * those drift. `layer` is validated by `isMoneyLayerId` on arrival, so a bad
   * deep link lands on the payout overview rather than a blank screen.
   */
  MoneyLayer:
    | {
        layer?:
          | "payout_overview"
          | "processing"
          | "move_money"
          | "payout_history"
          | "payout_onboarding"
          | "activity";
        title?: string;
        currency?: string;
      }
    | undefined;
  /**
   * One payout or one ledger row, in full.
   *
   * The record travels in the params rather than an id, because neither object
   * has a fetch-one endpoint — both are only ever returned inside a page. See
   * `MoneyDetailScreen`'s docstring for what that costs (no refresh on the
   * detail screen) and why the alternative is worse.
   */
  MoneyDetail:
    | { subject: "payout"; payout: import("../api/sellerPayouts").SellerPayout; title?: string }
    | { subject: "entry"; entry: import("../api/paymentsHub").LedgerEntry; title?: string }
    | undefined;
  /** Pulse Credits and cash rewards — reached from the Payments money hub. */
  Rewards: { title?: string } | undefined;
  /**
   * One route, two perspectives. The rebuilt two-sided Orders surface; the
   * header toggle switches between the seller fulfillment queue and the buyer's
   * "Your orders". `perspective` sets which side it opens on (default seller).
   */
  BusinessOsOrders: { title?: string; perspective?: "seller" | "buyer" } | undefined;
  /**
   * The rebuilt commerce inbox (Business "Messages" card). Draws its own navy
   * header; every row is about a money object surfaced via a context chip. The
   * old Messenger tab stays intact — this is the Business-surface entry point.
   *
   * `focusConversationId` is how a commerce entry point ("Contact Seller") hands
   * a thread over: the inbox is pushed first and opens the thread itself, so Back
   * lands in the commerce inbox rather than in the social messenger — which is the
   * whole reason the entry point routes through it.
   */
  BusinessOsMessages: { title?: string; focusConversationId?: number } | undefined;
  /**
   * The rebuilt hosted-events manager (Business "Events" card, EVENTS_CARD_CONFIG).
   * Draws its own navy header with the Upcoming/Past/Drafts tabs. Distinct from
   * the legacy `Events` live-discovery route, which is untouched.
   */
  BusinessOsEvents: { title?: string } | undefined;
  /**
   * The unified Activity feed (the bell in every seller header). Aggregates
   * social / marketplace / orders / ads / payments / system notifications; its
   * unread total is the same number the header bells show.
   */
  BusinessOsActivity: { title?: string; filter?: "all" | "social" | "marketplace" | "orders" | "system" } | undefined;
  SellerStore: { title?: string; mode?: "overview" | "apply" | "dashboard" | "profile" | "create" | "payouts" | "orders"; sellerId?: string; listingId?: number } | undefined;
  BuyerOrders: { orderId?: number; source?: string; title?: string } | undefined;
  BuyerOrderDetail: { orderId: number; source?: string; title?: string };
  BuyerPurchases: { title?: string } | undefined;
  BuyerOrdersDashboard: { orderId?: number; order_id?: number; id?: number; source?: string; title?: string } | undefined;
  MerchantApply: { title?: string } | undefined;
  MerchantDashboard: { title?: string } | undefined;
  MerchantProfile: { title?: string; sellerId?: string } | undefined;
  MarketplaceCreateGateway: { title?: string } | undefined;
  Search: { query?: string; title?: string } | undefined;
  Saved: ProfileOsParams | undefined;
  GroupDetail: { groupSlug: string; title?: string };
  LiveDetail: { liveId: number; title?: string };
  LiveStudio: { title?: string } | undefined;
  NativeLiveHost: { liveId: number; room?: string; tokenUrl?: string; title?: string };
  ReplayViewer: { liveId?: number; replayUrl?: string; poster?: string; title?: string; creator?: string };
  Events: ({ eventId?: number; mode?: "events" | "schedule" | "create"; title?: string } & ProfileOsParams) | undefined;
  EventDetail: { eventId: number; title?: string };
  LiveScheduleGateway: { title?: string } | undefined;
  LiveEventCreateGateway: { title?: string } | undefined;
  /**
   * Page OS — the canonical public page surface. One route for every page
   * type (artist, business, organization, …); the tab set is server-decided.
   * `handle` or `pageId` identifies the page; both accepted so deep links
   * (`/pulse/pages/@handle`) and in-app navigation share one destination.
   */
  Page: { handle?: string; pageId?: number; title?: string } | undefined;
  /**
   * Page creation flow: identity → details → review. `flavor` presets the
   * wizard for the Presence entry points (artist vs business categories);
   * without it the generic 16-type grid is offered.
   */
  PageCreate: { flavor?: "artist" | "business" } | undefined;
  /** My pages + role-gated management, linking into existing canonical systems. */
  PagesHub: { focusPageId?: number } | undefined;
  /**
   * Presence Home — the Profile OS entry to the member's professional
   * identities (artist / business), all backed by the canonical Page OS.
   */
  Presence: undefined;
  ProfileDetail: {
    profileKey?: string;
    userId?: number;
    profileId?: string;
    publicPlayerId?: string;
    username?: string;
    source?: string;
    title?: string;
  } | undefined;
  ProfileEdit: undefined;
  /**
   * `undefined` is load-bearing: the deep link `/pulse/premium`, premium-lock
   * notifications and the dashboard all push this route with no params at all,
   * and `resolveRouteProfileContext` reads absent params as the *owner*. The
   * Profile OS tile passes `ProfileOsParams` so the same screen can refuse on a
   * visitor route.
   */
  Premium: ({ title?: string } & ProfileOsParams) | undefined;
  CreatorStudio: undefined;
  CreatorStudioAlias: undefined;
  ContentPlanner: { mode?: "planner" | "scheduler" | "drafts"; title?: string } | undefined;
  ContentPlannerWeb: { mode?: "planner" | "scheduler" | "drafts"; title?: string } | undefined;
  ContentPlannerPulseAlias: { title?: string } | undefined;
  PostScheduler: { title?: string } | undefined;
  PostSchedulerPulseAlias: { title?: string } | undefined;
  DraftStudio: { title?: string } | undefined;
  DraftStudioPulseAlias: { title?: string } | undefined;
  Courses: { category?: string; title?: string } | undefined;
  CourseDetail: { courseId?: number; lessonSlug?: string; title?: string } | undefined;
  LearningLessonDetail: { lessonSlug: string; title?: string };
  TeacherProfileGateway: { teacherId?: string; title?: string } | undefined;
  TeacherDashboardGateway: { title?: string } | undefined;
  GrowthCenter: ({ contentType?: string; contentId?: number | string; title?: string } & ProfileOsParams) | undefined;
  /**
   * Founding Member Challenge and retention missions.
   *
   * Carries `ProfileOsParams` because it is reached from the Profile OS grid,
   * but deliberately declares no subject parameter of its own: the screen always
   * shows the signed-in member's own progress, and the server ignores a target
   * user even if a caller invented one. `section` only chooses which layer of
   * the Progress Center opens first; `ref` is the opaque, viewer-bound referral
   * token, never a user id.
   */
  ProgressCenter: ({
    section?: ProgressCenterSection;
    ref?: string;
    title?: string;
  } & ProfileOsParams) | undefined;
  IntelligenceCenter: ({ alertId?: number; subsystem?: string; title?: string } & ProfileOsParams) | undefined;
  UndxActionCenter: { orgId?: string; actor?: string; productArea?: string; title?: string } | undefined;
  UndxCapabilities: { title?: string } | undefined;
  // `presetSymbol` seeds the create form when the user arrives from an asset.
  // It selects an asset; it does not create anything.
  AlertManagement: { alertId?: number; presetSymbol?: string; title?: string } | undefined;
  CryptoAlertManagement:
    | { alertId?: number; alert_id?: number; id?: number; presetSymbol?: string; title?: string }
    | undefined;
  CryptoAlertCenter: { presetSymbol?: string; title?: string } | undefined;
  CryptoAlertHistory: { alertId?: number; title?: string } | undefined;
  CryptoPortfolio: { title?: string } | undefined;
  Watchlists: { title?: string } | undefined;
  AssetDetail: { symbol: string; name?: string; title?: string };
  AccountCenter: { section?: "account" | "security" | "privacy" | "devices"; title?: string } | undefined;
  AccountSettings: { title?: string } | undefined;
  AccountSecurity: { title?: string } | undefined;
  AccountWebSettings: { title?: string } | undefined;
  AccountWebSecurity: { title?: string } | undefined;
  AccountPrivacy: { title?: string } | undefined;
  AccountDevices: { title?: string } | undefined;
  AccountHealth: { title?: string } | undefined;
  AccountHealthWeb: { title?: string } | undefined;
  SafetyHub:
    | ({
        title?: string;
        section?: "overview" | "blocks" | "mutes" | "reports";
        reportTarget?: string;
        reportType?: string;
        blockTarget?: string;
        muteTarget?: string;
      } & ProfileOsParams)
    | undefined;
  SafetyWebHub:
    | ({
        title?: string;
        section?: "overview" | "blocks" | "mutes" | "reports";
        reportTarget?: string;
        reportType?: string;
        blockTarget?: string;
        muteTarget?: string;
      } & ProfileOsParams)
    | undefined;
  TrustSafety: ({ title?: string; mode?: "support" | "security" | "scam" | "trust" } & ProfileOsParams) | undefined;
  TrustSafetySupport: ({ title?: string } & ProfileOsParams) | undefined;
  TrustSafetyHelp: ({ title?: string } & ProfileOsParams) | undefined;
  TrustCenter: ({ title?: string } & ProfileOsParams) | undefined;
  SecurityReport: ({ title?: string } & ProfileOsParams) | undefined;
  ScamShield: ({ title?: string } & ProfileOsParams) | undefined;
  VerificationCenter: { title?: string; track?: "identity" | "blue_check" | "business" | "government_id" } | undefined;
  VerificationWebCenter: { title?: string; track?: "identity" | "blue_check" | "business" | "government_id" } | undefined;
  ActivityInbox: ({
    category?: "all" | "messages" | "calls" | "social" | "safety" | "verification" | "marketplace" | "creator_growth" | "intelligence_alerts";
    title?: string;
  } & ProfileOsParams) | undefined;
  ActivityInboxLegacyInbox: { title?: string } | undefined;
  ActivityInboxWebActivity: { title?: string } | undefined;
  ActivityInboxWebInbox: { title?: string } | undefined;
  NotificationCenter: { notificationId?: number } | undefined;
  NotificationPreferences: undefined;
  RegionTime: undefined;

  /**
   * Settings platform. Each of these is a first-class native screen — there is
   * no WebView fallback anywhere in this group. `params.highlight` optionally
   * carries a preference key so a deep link or search result can scroll to and
   * flash the specific row the user was looking for.
   */
  NotificationSettings: { highlight?: string } | undefined;
  AppearanceSettings: { highlight?: string } | undefined;
  AccessibilitySettings: { highlight?: string } | undefined;
  LanguageSettings: { highlight?: string } | undefined;
  StorageSettings: { highlight?: string } | undefined;
  PermissionsSettings: { highlight?: string } | undefined;
  PrivacySettings: { highlight?: string } | undefined;
  SecuritySettings: { highlight?: string } | undefined;
  SessionsDevices: { highlight?: string } | undefined;
  BlockedUsers: undefined;
  MutedUsers: undefined;
  DataPrivacySettings: { highlight?: string } | undefined;
  HelpSettings: undefined;
  AboutSettings: undefined;
  LegalSettings: { document?: "terms" | "privacy" | "guidelines" | "cookies" | "licenses" } | undefined;
  DeveloperSettings: undefined;
  /**
   * Full-screen True-to-Publish preview. `token` keys into the in-memory
   * preview handoff store (draft + live publish callback); params are kept
   * serialization-safe (no functions) per navigation best practice.
   */
  ContentPreview: { token: string; title?: string };
};
