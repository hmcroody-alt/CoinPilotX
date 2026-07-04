import { LinkingOptions } from "@react-navigation/native";
import { RootStackParamList } from "./types";

export const linking: LinkingOptions<RootStackParamList> = {
  prefixes: ["pulsesoc://", "https://pulsesoc.com"],
  config: {
    screens: {
      Tabs: {
        screens: {
          Home: "pulse",
          Search: "pulse/search",
          Saved: "pulse/saved",
          Groups: "pulse/groups",
          Live: "pulse/live",
          Reels: "pulse/reels",
          Status: "pulse/status",
          Messenger: "pulse/messages",
          Notifications: "pulse/notifications",
          PulseAI: "pulse/ai",
          Profile: "pulse/profile",
          Marketplace: "pulse/marketplace",
          Settings: "pulse/settings"
        }
      },
      CameraStudio: {
        path: "pulse/camera/:mode?",
        parse: {
          mode: String,
          target: String,
          captureMode: String,
          conversationId: Number
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
      ReelDetail: {
        path: "pulse/reels/:reelId",
        parse: {
          reelId: Number
        }
      },
      StatusDetail: {
        path: "pulse/status/:statusId",
        parse: {
          statusId: Number
        }
      },
      MarketplaceDetail: {
        path: "pulse/marketplace/:listingId",
        parse: {
          listingId: Number
        }
      },
      Search: {
        path: "search",
        parse: {
          query: String
        }
      },
      Saved: {
        path: "saved"
      },
      GroupDetail: {
        path: "pulse/groups/:groupSlug"
      },
      LiveDetail: {
        path: "pulse/live/:liveId",
        parse: {
          liveId: Number
        }
      },
      ProfileEdit: {
        path: "pulse/profile/edit"
      },
      ProfileDetail: {
        path: "pulse/profile/:profileKey"
      },
      Premium: {
        path: "pulse/premium"
      },
      CreatorStudio: {
        path: "pulse/creator-studio"
      },
      GrowthCenter: {
        path: "pulse/growth"
      },
      IntelligenceCenter: {
        path: "dashboard/intelligence/:subsystem?"
      },
      AlertManagement: {
        path: "pulse/alerts/:alertId?",
        parse: {
          alertId: Number
        }
      },
      CryptoAlertManagement: {
        path: "dashboard/crypto/alerts",
        parse: {
          alertId: Number,
          alert_id: Number,
          id: Number
        }
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
