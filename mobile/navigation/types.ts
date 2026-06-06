export type AuthStackParamList = {
  Splash: undefined;
  Login: undefined;
  Signup: undefined;
  ForgotPassword: undefined;
  EmailConfirmationPending: { email?: string; token?: string };
  ResetPassword: { token?: string };
};

export type MainStackParamList = {
  HomeFeed: undefined;
};

export type MainTabParamList = {
  HomeFeed: undefined;
  Reels: undefined;
  Messages: undefined;
  Notifications: undefined;
  Marketplace: undefined;
  ProfileStack: undefined;
};

export type ProfileStackParamList = {
  Profile: undefined;
  Settings: undefined;
  Premium: undefined;
  UNDX: undefined;
};
