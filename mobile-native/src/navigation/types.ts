export type AuthStackParamList = {
  Login: undefined;
  Signup: undefined;
};

export type AppTabParamList = {
  Home: undefined;
  Messenger: undefined;
  PulseAI: undefined;
  Profile: undefined;
  Settings: undefined;
};

export type RootStackParamList = {
  Tabs: undefined;
  Chat: { conversationId: number; title?: string };
};
