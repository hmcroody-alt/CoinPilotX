export type AuthStackParamList = {
  Splash: undefined;
  Login: undefined;
  Signup: undefined;
  ForgotPassword: undefined;
  EmailConfirmationPending: { email?: string; token?: string };
  ResetPassword: { token?: string };
};

export type MainStackParamList = {
  MainTabs: undefined;
  HomeFeed: undefined;
  CreatePulse: undefined;
  PostDetail: { postId: number };
  ProfileDetail: { username: string; displayName?: string; avatarUrl?: string };
};

export type MainTabParamList = {
  HomeFeed: undefined;
  Reels: undefined;
  Videos: undefined;
  Messages: undefined;
  Notifications: undefined;
  ProfileStack: undefined;
};

export type ProfileStackParamList = {
  Profile: undefined;
  Settings: undefined;
  Premium: undefined;
  UNDX: undefined;
};
