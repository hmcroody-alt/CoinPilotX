import { LinkingOptions } from "@react-navigation/native";
import { PULSE_LINK_PREFIXES } from "../services/config";
import { AuthStackParamList, MainTabParamList, ProfileStackParamList } from "./types";

type RootParamList = AuthStackParamList & MainTabParamList & ProfileStackParamList;

export const supportedLinkPrefixes = ["pulse://", ...PULSE_LINK_PREFIXES.filter(prefix => prefix !== "pulse://")];

export const linking: LinkingOptions<RootParamList> = {
  prefixes: supportedLinkPrefixes,
  config: {
    screens: {
      Splash: "splash",
      Login: "login",
      Signup: "signup",
      ForgotPassword: "forgot-password",
      HomeFeed: "pulse",
      Reels: "pulse/reels",
      Messages: "pulse/messages-v2",
      Notifications: "pulse/notifications",
      Marketplace: "pulse/marketplace",
      ProfileStack: {
        path: "pulse/profile",
        screens: {
          Profile: "",
          Settings: "settings",
          Premium: "premium",
          UNDX: "undx"
        }
      }
    }
  }
};
