import { getStateFromPath, LinkingOptions } from "@react-navigation/native";
import { profileNavigationParams, profileTargetFromUrl } from "../api/profileTarget";
import { findSettingsEntry } from "../settings/registry";
import { canonicalNativeRoute, nativeObjectDestination } from "./nativeRouteActions";
import { RootStackParamList } from "./types";

/**
 * `pulsesoc://settings/<id>` — resolved from the registry, not from a table.
 *
 * This is handled here rather than in `config.screens` below for two reasons.
 * First, a screen may only carry one path in the static config, and several
 * settings destinations already own one (`ProfileEdit` is `pulse/profile/edit`,
 * `SafetyHub` is `pulse/safety/:section?`) — declaring them again would be a
 * conflict, not an alias. Second, resolving through `findSettingsEntry` means
 * the deep link and the index row can never disagree about where an id goes:
 * both read the same entry, including its `params`.
 *
 * The `settings/` prefix, not `pulse/settings/`, is deliberate. The latter is
 * already claimed by `AccountCenter`'s `pulse/settings/:section` catch-all, and
 * layering these underneath it would make every new id a ranking question.
 *
 * Returns null for anything that isn't a settings link, so the caller falls
 * through to its normal resolution.
 */
function settingsDeepLink(path: string) {
  const match = /^\/?settings\/([a-z0-9-]+)\/?$/i.exec(path.split("?")[0]);
  if (!match) return null;
  const entry = findSettingsEntry(match[1].toLowerCase());
  // An unknown id lands on the settings index rather than nowhere: a link from
  // an older or newer build should still open Settings, not fail silently.
  if (!entry) return { routes: [{ name: "Tabs" as const, params: { screen: "Settings" } }] };
  return { routes: [{ name: entry.route, params: entry.params }] };
}

export const linking: LinkingOptions<RootStackParamList> = {
  prefixes: ["pulsesoc://", "https://pulsesoc.com"],
  getStateFromPath(path, options) {
    const canonical = canonicalNativeRoute(path);
    const normalizedPath = canonical.relative;
    const settingsRoute = settingsDeepLink(canonical.path) || settingsDeepLink(normalizedPath);
    if (settingsRoute) return settingsRoute;
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
      // Page OS. Order matters: the static create/hub paths must be declared
      // before the ":handle" catch-all so "create" never resolves as a handle.
      Presence: "pulse/presence",
      PageCreate: "pulse/pages/create",
      PagesHub: "pulse/pages",
      Page: {
        path: "pulse/pages/:handle"
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
      UndxActionCenter: {
        path: "pulse/undx/actions",
        parse: {
          orgId: String,
          actor: String,
          productArea: String
        }
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
      // The screen has existed in AppNavigator since the portfolio was made
      // native, and nativeRouteActions.ts routes to it in-app, but it was never
      // declared here — so it was reachable by tapping and not by link. UNDX
      // grounds a holdings answer with this path as the place to see it, and a
      // deep link that resolves to nothing is worse than no link at all.
      Portfolio: "pulse/portfolio",
      AccountCenter: {
        path: "pulse/settings/:section",
        parse: {
          section: String
        }
      },
      AccountSettings: "dashboard/account/settings",
      AccountSecurity: "dashboard/account/security",
      AccountWebSettings: "account/settings",
      AccountWebSecurity: "account/security",
      AccountPrivacy: "privacy-center",
      AccountDevices: "pulse/settings/devices",
      AccountHealth: "pulse/account-health",
      AccountHealthWeb: "dashboard/account/health",
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
