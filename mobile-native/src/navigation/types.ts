import { NavigatorScreenParams } from "@react-navigation/native";

export type AuthStackParamList = {
  Login: undefined;
  Signup: undefined;
  AccountRecovery: undefined;
};

export type AppTabParamList = {
  Dashboard: undefined;
  Home: { openComposer?: boolean } | undefined;
  Search: { query?: string } | undefined;
  Saved: undefined;
  Groups: undefined;
  Live: undefined;
  Reels: undefined;
  Create: undefined;
  Status: { openCreator?: boolean; statusId?: number } | undefined;
  Messenger: undefined;
  Notifications: undefined;
  PulseAI: undefined;
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
  DashboardLegacyModule: { legacyGroup?: string; legacyModule?: string; legacySubmodule?: string; title?: string } | undefined;
  DashboardModuleDetail: { groupKey: string; moduleKey: string; title?: string };
  CameraStudio: {
    target?: "feed" | "post" | "status" | "reel" | "message" | "avatar" | "cover" | "creator" | "marketplace";
    mode?: "photo" | "video" | "status" | "reel";
    captureMode?: "photo" | "video";
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
  Chat: { conversationId: number; title?: string; openControlCenter?: boolean };
  PostDetail: { postId: number; title?: string };
  Reels: { reelId?: number; title?: string } | undefined;
  ReelDetail: { reelId: number; title?: string };
  StatusDetail: { statusId: number; title?: string };
  MarketplaceDetail: { listingId?: number; title?: string } | undefined;
  SellerStore: { title?: string; mode?: "overview" | "apply" | "dashboard" | "profile" | "create" | "payouts"; sellerId?: string } | undefined;
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
  Events: { eventId?: number; mode?: "events" | "schedule" | "create"; title?: string } | undefined;
  EventDetail: { eventId: number; title?: string };
  LiveScheduleGateway: { title?: string } | undefined;
  LiveEventCreateGateway: { title?: string } | undefined;
  ProfileDetail: { profileKey?: string; title?: string } | undefined;
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
  NotificationCenter: undefined;
  NotificationPreferences: undefined;
};
