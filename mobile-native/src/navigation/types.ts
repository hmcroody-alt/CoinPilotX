import { NavigatorScreenParams } from "@react-navigation/native";

export type AuthStackParamList = {
  Login: undefined;
  Signup: undefined;
};

export type AppTabParamList = {
  Home: undefined;
  Messenger: undefined;
  Notifications: undefined;
  PulseAI: undefined;
  Profile: undefined;
  Settings: undefined;
};

export type RootStackParamList = {
  Tabs: NavigatorScreenParams<AppTabParamList> | undefined;
  Chat: { conversationId: number; title?: string };
  PostDetail: { postId: number; title?: string };
  ProfileDetail: { profileKey?: string; title?: string } | undefined;
  ProfileEdit: undefined;
  NotificationCenter: undefined;
  NotificationPreferences: undefined;
};
