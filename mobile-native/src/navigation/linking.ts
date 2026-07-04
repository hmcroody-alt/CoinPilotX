import { LinkingOptions } from "@react-navigation/native";
import { RootStackParamList } from "./types";

export const linking: LinkingOptions<RootStackParamList> = {
  prefixes: ["pulsesoc://", "https://pulsesoc.com"],
  config: {
    screens: {
      Tabs: {
        screens: {
          Home: "pulse",
          Reels: "pulse/reels",
          Messenger: "pulse/messages",
          Notifications: "pulse/notifications",
          Profile: "pulse/profile"
        }
      },
      Chat: {
        path: "pulse/messages/:conversationId",
        parse: {
          conversationId: Number
        }
      },
      PostDetail: {
        path: "pulse/post/:postId",
        parse: {
          postId: Number
        }
      },
      Reels: {
        path: "pulse/reels"
      },
      ReelDetail: {
        path: "pulse/reels/:reelId",
        parse: {
          reelId: Number
        }
      },
      ProfileEdit: {
        path: "pulse/profile/edit"
      },
      ProfileDetail: {
        path: "pulse/profile/:profileKey"
      },
      NotificationCenter: {
        path: "notifications"
      },
      NotificationPreferences: {
        path: "pulse/settings/notifications"
      }
    }
  }
};
