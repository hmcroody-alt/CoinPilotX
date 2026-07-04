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
          Profile: "pulse/profile",
          Marketplace: "pulse/marketplace"
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
      NotificationCenter: {
        path: "notifications"
      },
      NotificationPreferences: {
        path: "pulse/settings/notifications"
      }
    }
  }
};
