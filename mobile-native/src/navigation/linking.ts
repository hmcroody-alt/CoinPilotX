import { getStateFromPath, LinkingOptions } from "@react-navigation/native";
import { profileNavigationParams, profileTargetFromUrl } from "../api/profileTarget";
import { canonicalNativeRoute, nativeObjectDestination } from "./nativeRouteActions";
import { RootStackParamList } from "./types";

export const linking: LinkingOptions<RootStackParamList> = {
  prefixes: ["pulsesoc://", "https://pulsesoc.com"],
  getStateFromPath(path, options) {
    const canonical = canonicalNativeRoute(path);
    const normalizedPath = canonical.relative;
    if (canonical.path === "/pulse/profile/edit") {
      return { routes: [{ name: "ProfileEdit" }] };
    }
    const profileParams = profileNavigationParams(profileTargetFromUrl(normalizedPath), "Profile");
    if (profileParams) {
      return { routes: [{ name: "ProfileDetail", params: profileParams }] };
    }
    const objectDestination = nativeObjectDestination(normalizedPath);
    if (objectDestination) {
      return {
        routes: [{
          name: objectDestination.screen as keyof RootStackParamList,
          params: objectDestination.params
        }]
      };
    }
    return getStateFromPath(path, options);
  },
  config: {
    screens: {
      Tabs: {
        screens: {
          Dashboard: "pulse/dashboard",
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
      DashboardLegacyModule: {
        path: "dashboard/:legacyGroup/:legacyModule/:legacySubmodule?",
        parse: {
          legacyGroup: String,
          legacyModule: String,
          legacySubmodule: String
        }
      },
      UserDashboard: "dashboard",
      UserDashboardWeb: "dashboard/home",
      DashboardComposeAlias: "pulse/compose",
      DashboardMusicAlias: "pulse/music-alias",
      Music: {
        path: "pulse/music",
        parse: {
          trackId: String,
          track: String,
          artistId: Number,
          artist: Number,
          openUpload: Boolean
        }
      },
      DashboardModuleDetail: {
        path: "pulse/dashboard/module/:groupKey/:moduleKey",
        parse: {
          groupKey: String,
          moduleKey: String
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
      Call: {
        path: "pulse/calls/:callId?",
        parse: {
          callId: String,
          conversationId: Number,
          callType: String,
          direction: String
        }
      },
      Chat: {
        path: "pulse/messages/:conversationId",
        parse: {
          conversationId: Number
        }
      },
      NewChat: {
        path: "pulse/messages/new",
        parse: {
          initialQuery: String,
          targetUserId: Number
        }
      },
      PostDetail: {
        path: "pulse/post/:postId",
        parse: {
          postId: Number
        }
      },
      MarketplaceCreateGateway: "pulse/marketplace/create",
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
      SellerStore: {
        path: "pulse/seller-store",
        parse: {
          mode: String,
          sellerId: String
        }
      },
      BuyerOrders: {
        path: "pulse/orders",
        parse: {
          orderId: Number,
          source: String
        }
      },
      BuyerOrderDetail: {
        path: "pulse/orders/:orderId",
        parse: {
          orderId: Number,
          source: String
        }
      },
      BuyerPurchases: "pulse/purchases",
      BuyerOrdersDashboard: {
        path: "dashboard/orders",
        parse: {
          orderId: Number,
          order_id: Number,
          id: Number,
          source: String
        }
      },
      MerchantApply: "pulse/merchant/apply",
      MerchantDashboard: "pulse/merchant/dashboard",
      MerchantProfile: {
        path: "pulse/merchant/:sellerId",
        parse: {
          sellerId: String
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
      Events: {
        path: "pulse/events",
        parse: {
          eventId: Number,
          mode: String
        }
      },
      EventDetail: {
        path: "pulse/events/:eventId",
        parse: {
          eventId: Number
        }
      },
      LiveScheduleGateway: "pulse/live/schedule",
      LiveEventCreateGateway: "pulse/live/events/create",
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
      CreatorStudioAlias: "pulse/creator",
      ContentPlanner: {
        path: "pulse/content-planner",
        parse: {
          mode: String
        }
      },
      ContentPlannerWeb: {
        path: "pulse/dashboard/content-planner-web",
        parse: {
          mode: String
        }
      },
      ContentPlannerPulseAlias: "pulse/dashboard/content-planner",
      PostScheduler: "pulse/dashboard/post-scheduler-web",
      PostSchedulerPulseAlias: "pulse/dashboard/post-scheduler",
      DraftStudio: "pulse/dashboard/draft-studio-web",
      DraftStudioPulseAlias: "pulse/dashboard/draft-studio",
      Courses: {
        path: "pulse/courses",
        parse: {
          category: String
        }
      },
      CourseDetail: {
        path: "pulse/courses/:courseId",
        parse: {
          courseId: Number
        }
      },
      LearningLessonDetail: {
        path: "education/lesson/:lessonSlug"
      },
      TeacherProfileGateway: {
        path: "pulse/teachers/:teacherId?"
      },
      TeacherDashboardGateway: "pulse/teacher-dashboard",
      GrowthCenter: {
        path: "pulse/growth"
      },
      IntelligenceCenter: {
        path: "pulse/intelligence/:subsystem?"
      },
      AlertManagement: {
        path: "pulse/alerts/:alertId?",
        parse: {
          alertId: Number
        }
      },
      CryptoAlertManagement: {
        path: "pulse/crypto/alerts",
        parse: {
          alertId: Number,
          alert_id: Number,
          id: Number
        }
      },
      AccountCenter: {
        path: "pulse/settings/:section",
        parse: {
          section: String
        }
      },
      AccountSettings: "pulse/dashboard/account-settings",
      AccountSecurity: "pulse/dashboard/account-security",
      AccountWebSettings: "account/settings",
      AccountWebSecurity: "account/security",
      AccountPrivacy: "privacy-center",
      AccountDevices: "pulse/settings/devices",
      AccountHealth: "pulse/account-health",
      AccountHealthWeb: "pulse/dashboard/account-health",
      SafetyHub: {
        path: "pulse/safety/:section?",
        parse: {
          section: String
        }
      },
      SafetyWebHub: {
        path: "pulse/dashboard/network-safety/:section?",
        parse: {
          section: String
        }
      },
      TrustSafety: {
        path: "pulse/help",
        parse: {
          mode: String
        }
      },
      TrustSafetySupport: "pulse/support",
      TrustSafetyHelp: "help",
      TrustCenter: "trust-center",
      SecurityReport: "security",
      ScamShield: "scam-shield/:mode?",
      VerificationCenter: {
        path: "pulse/verification/:track?",
        parse: {
          track: String
        }
      },
      VerificationWebCenter: {
        path: "pulse/dashboard/account-verification",
        parse: {
          track: String
        }
      },
      ActivityInbox: {
        path: "pulse/activity/:category?",
        parse: {
          category: String
        }
      },
      ActivityInboxLegacyInbox: "pulse/inbox",
      ActivityInboxWebActivity: "dashboard/activity",
      ActivityInboxWebInbox: "dashboard/inbox",
      NotificationCenter: {
        path: "notifications"
      },
      NotificationPreferences: {
        path: "pulse/settings/notifications"
      }
    }
  }
};
