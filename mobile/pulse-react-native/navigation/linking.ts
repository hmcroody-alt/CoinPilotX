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
      MainTabs: {
        path: "pulse",
        screens: {
          HomeFeed: "",
          Reels: "reels",
          Videos: "videos",
          Messages: "messages-v2",
          Notifications: "notifications",
          ProfileStack: {
            path: "profile",
            screens: {
              Profile: "",
              Settings: "settings",
              Premium: "premium",
              UNDX: "undx"
            }
          }
        }
      },
      HomeFeed: "pulse",
      CreatePulse: "create",
      PostDetail: {
        path: "post/:postId",
        parse: { postId: Number }
      },
      ProfileDetail: {
        path: "profile/:username",
        parse: { username: String }
      },
      Reels: "pulse/reels",
      Videos: "pulse/videos",
      Messages: "pulse/messages-v2",
      Notifications: "pulse/notifications"
    }
  }
};
