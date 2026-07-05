import { NavigatorScreenParams } from "@react-navigation/native";

export type AuthStackParamList = {
  Login: undefined;
  Signup: undefined;
};

export type AppTabParamList = {
  Home: { openComposer?: boolean } | undefined;
  Search: { query?: string } | undefined;
  Saved: undefined;
  Groups: undefined;
  Live: undefined;
  Reels: undefined;
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
  Chat: { conversationId: number; title?: string };
  PostDetail: { postId: number; title?: string };
  Reels: { reelId?: number; title?: string } | undefined;
  ReelDetail: { reelId: number; title?: string };
  StatusDetail: { statusId: number; title?: string };
  MarketplaceDetail: { listingId?: number; title?: string } | undefined;
  Search: { query?: string; title?: string } | undefined;
  Saved: undefined;
  GroupDetail: { groupSlug: string; title?: string };
  LiveDetail: { liveId: number; title?: string };
  ProfileDetail: { profileKey?: string; title?: string } | undefined;
  ProfileEdit: undefined;
  Premium: undefined;
  CreatorStudio: undefined;
  GrowthCenter: { contentType?: string; contentId?: number | string; title?: string } | undefined;
  IntelligenceCenter: { alertId?: number; subsystem?: string; title?: string } | undefined;
  AlertManagement: { alertId?: number; title?: string } | undefined;
  CryptoAlertManagement: { alertId?: number; alert_id?: number; id?: number; title?: string } | undefined;
  NotificationCenter: undefined;
  NotificationPreferences: undefined;
};
