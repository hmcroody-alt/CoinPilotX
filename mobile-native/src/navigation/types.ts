import { NavigatorScreenParams } from "@react-navigation/native";

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
  Reels: undefined;
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
  Music: { trackId?: string; track?: string; artistId?: number; artist?: number; openUpload?: boolean; title?: string; surface?: "post" | "status" | "reel" } | undefined;
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
  Reels: { reelId?: number; title?: string } | undefined;
  ReelDetail: { reelId: number; title?: string };
  StatusDetail: { statusId: number; title?: string };
  MarketplaceDetail: { listingId?: number; title?: string } | undefined;
  BusinessOs: { title?: string } | undefined;
  BusinessProfile: { title?: string } | undefined;
  /**
   * The seller-side Marketplace manager (Business "Sections" card #3). Distinct
   * from `Marketplace`/`MarketplaceDetail`, which are the consumer browse
   * surface, and from `MarketplaceCreateGateway`, which is the composer.
   */
  MarketplaceManager: { title?: string } | undefined;
  /**
   * One route, two screens. Default is the rebuilt two-sided ads manager;
   * `mode: "classic"` renders the previous screen, which still owns the
   * ad-account and campaign creation forms the manager routes into.
   */
  BusinessOsAdvertising: { title?: string; accountId?: number; mode?: "manager" | "classic" } | undefined;
  BusinessOsInsights: { title?: string } | undefined;
  BusinessOsPayments: { title?: string; accountId?: number } | undefined;
  SellerStore: { title?: string; mode?: "overview" | "apply" | "dashboard" | "profile" | "create" | "payouts" | "orders"; sellerId?: string } | undefined;
  BuyerOrders: { orderId?: number; source?: string; title?: string } | undefined;
  BuyerOrderDetail: { orderId: number; source?: string; title?: string };
  BuyerPurchases: { title?: string } | undefined;
  BuyerOrdersDashboard: { orderId?: number; order_id?: number; id?: number; source?: string; title?: string } | undefined;
  MerchantApply: { title?: string } | undefined;
  MerchantDashboard: { title?: string } | undefined;
  MerchantProfile: { title?: string; sellerId?: string } | undefined;
  MarketplaceCreateGateway: { title?: string } | undefined;
  Search: { query?: string; title?: string } | undefined;
  Saved: undefined;
  GroupDetail: { groupSlug: string; title?: string };
  LiveDetail: { liveId: number; title?: string };
  LiveStudio: { title?: string } | undefined;
  NativeLiveHost: { liveId: number; room?: string; tokenUrl?: string; title?: string };
  ReplayViewer: { liveId?: number; replayUrl?: string; poster?: string; title?: string; creator?: string };
  Events: { eventId?: number; mode?: "events" | "schedule" | "create"; title?: string } | undefined;
  EventDetail: { eventId: number; title?: string };
  LiveScheduleGateway: { title?: string } | undefined;
  LiveEventCreateGateway: { title?: string } | undefined;
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
  Premium: undefined;
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
  GrowthCenter: { contentType?: string; contentId?: number | string; title?: string } | undefined;
  IntelligenceCenter: { alertId?: number; subsystem?: string; title?: string } | undefined;
  UndxActionCenter: { orgId?: string; actor?: string; productArea?: string; title?: string } | undefined;
  AlertManagement: { alertId?: number; title?: string } | undefined;
  CryptoAlertManagement: { alertId?: number; alert_id?: number; id?: number; title?: string } | undefined;
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
    | {
        title?: string;
        section?: "overview" | "blocks" | "mutes" | "reports";
        reportTarget?: string;
        reportType?: string;
        blockTarget?: string;
        muteTarget?: string;
      }
    | undefined;
  SafetyWebHub:
    | {
        title?: string;
        section?: "overview" | "blocks" | "mutes" | "reports";
        reportTarget?: string;
        reportType?: string;
        blockTarget?: string;
        muteTarget?: string;
      }
    | undefined;
  TrustSafety: { title?: string; mode?: "support" | "security" | "scam" | "trust" } | undefined;
  TrustSafetySupport: { title?: string } | undefined;
  TrustSafetyHelp: { title?: string } | undefined;
  TrustCenter: { title?: string } | undefined;
  SecurityReport: { title?: string } | undefined;
  ScamShield: { title?: string } | undefined;
  VerificationCenter: { title?: string; track?: "identity" | "blue_check" | "business" | "government_id" } | undefined;
  VerificationWebCenter: { title?: string; track?: "identity" | "blue_check" | "business" | "government_id" } | undefined;
  ActivityInbox: {
    category?: "all" | "messages" | "calls" | "social" | "safety" | "verification" | "marketplace" | "creator_growth" | "intelligence_alerts";
    title?: string;
  } | undefined;
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
