import { LinkingOptions } from "@react-navigation/native";
import { PULSE_LINK_PREFIXES } from "../services/config";
import { AuthStackParamList, MainStackParamList, MainTabParamList, ProfileStackParamList } from "./types";

type RootParamList = AuthStackParamList & MainStackParamList & MainTabParamList & ProfileStackParamList;

export const supportedLinkPrefixes = ["pulse://", ...PULSE_LINK_PREFIXES.filter(prefix => prefix !== "pulse://")];

export const linking: LinkingOptions<RootParamList> = {
  prefixes: supportedLinkPrefixes,
  config: {
    screens: {
      Splash: "splash",
      Login: "login",
      Signup: "signup",
      ForgotPassword: "forgot-password",
      EmailConfirmationPending: {
        path: "verify-email/:token",
        parse: {
          token: String
        }
      },
      ResetPassword: {
        path: "reset-password/:token",
        parse: {
          token: String
        }
      },
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
