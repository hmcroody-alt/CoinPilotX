import { LinkingOptions } from "@react-navigation/native";
import { PULSE_DEEP_LINK_PREFIXES } from "../config";

export type PulseRootParamList = {
  Auth: undefined;
  Feed: undefined;
  Reels: undefined;
  Videos: undefined;
  Messages: undefined;
  Notifications: undefined;
  Profile: undefined;
  Settings: undefined;
  Premium: undefined;
};

export const linking: LinkingOptions<PulseRootParamList> = {
  prefixes: PULSE_DEEP_LINK_PREFIXES,
  config: {
    screens: {
      Auth: "login",
      Feed: "pulse",
      Reels: "pulse/reels",
      Videos: "pulse/videos",
      Messages: "pulse/messages-v2",
      Notifications: "pulse/notifications",
      Profile: "pulse/profile",
      Settings: "pulse/settings",
      Premium: "pulse/premium"
    }
  }
};
