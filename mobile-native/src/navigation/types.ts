import { NavigatorScreenParams } from "@react-navigation/native";

export type AuthStackParamList = {
  Login: undefined;
  Signup: undefined;
};

export type AppTabParamList = {
  Home: undefined;
  Search: { query?: string } | undefined;
  Saved: undefined;
  Groups: undefined;
  Live: undefined;
  Reels: undefined;
  Status: undefined;
  Messenger: undefined;
  Notifications: undefined;
  PulseAI: undefined;
  Profile: undefined;
  Marketplace: undefined;
  Settings: undefined;
};

export type RootStackParamList = {
  Tabs: NavigatorScreenParams<AppTabParamList> | undefined;
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
  NotificationCenter: undefined;
  NotificationPreferences: undefined;
};
