import { NativeStackNavigationProp } from "@react-navigation/native-stack";
import AsyncStorage from "@react-native-async-storage/async-storage";
import * as Battery from "expo-battery";
import { RouteProp, useIsFocused, useNavigation, useRoute } from "@react-navigation/native";
import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AccessibilityInfo, ActivityIndicator, Animated, AppState, Easing, FlatList, Image, NativeScrollEvent, NativeSyntheticEvent, Pressable, RefreshControl, ScrollView, StyleSheet, Text, View, ViewToken, useWindowDimensions } from "react-native";
import {
  addPostComment,
  deletePost,
  getPostDetail,
  hidePost,
  listFeed,
  loadCachedFeed,
  mutePostAuthor,
  PulsePost,
  pulsePostUrl,
  reactToPost,
  repostPost,
  savablePostId,
  toggleFollowAuthor
} from "../api/feed";
import { isContentOwner } from "../api/contentOwnership";
import { describeDeleteError } from "../api/deleteErrors";
import { profileTargetFromPost } from "../api/profile";
import { profileNavigationParams } from "../api/profileTarget";
import { listStatuses, loadCachedStatuses, PulseStatus, statusPosterUrl } from "../api/status";
import { HomePulseComposer } from "../components/HomePulseComposer";
import { GalacticAtmosphere } from "../components/GalacticAtmosphere";
import { LogiNexusBadge, LogiNexusEmptyState, LogiNexusPanel } from "../components/LogiNexus";
import { MasterNavigationDrawer } from "../components/MasterNavigationDrawer";
import { PostCard } from "../components/PostCard";
import { peekSaveState } from "../social/savedStore";
import { setSaved } from "../social/useSaveAction";
import { SponsoredAdCard } from "../components/SponsoredAdCard";
import { fetchSponsoredAds, SponsoredAd } from "../api/ads";
import { injectAds } from "../feed/injectAds";
import { HomeRow, injectDiscoveryRows } from "../discovery/discoveryRows";
import { DiscoveryRowView } from "../discovery/DiscoveryRowView";
import { useHomeDiscovery } from "../discovery/useHomeDiscovery";
import { invalidateNativeSync, registerSyncInvalidation } from "../core/eventSync";
import { getPulseRadioState, PulseRadioState, subscribePulseRadio, togglePulseRadio } from "../core/pulseRadio";
import { useBottomNavContentPadding, useBottomNavScrollVisibility } from "../navigation/BottomNavVisibility";
import { GlobalNavigationBadges, GlobalNavigationIdentity, LogiNexusGlobalHeader } from "../navigation/GlobalNavigation";
import { openDashboardRoute } from "../navigation/dashboardRouting";
import { registerRefreshDestination } from "../navigation/refreshCoordinator";
import { openNativeRoute } from "../navigation/nativeRouteActions";
import { AppTabParamList, RootStackParamList } from "../navigation/types";
import { actionKey, useSocialActionGuard } from "../social/actionGuard";
import { useAuth } from "../session/auth";
import { colors } from "../theme/colors";
import { logiNexus } from "../theme/logiNexus";
import { sharePulseObject } from "../sharing/nativeShare";
import { createThemedStyles } from "../theme/themedStyles";
import { spatialHomeFeedEnabled } from "../spatial/flags";
import { SpatialPager } from "../spatial/SpatialPager";

type HomeNavigation = NativeStackNavigationProp<RootStackParamList>;

/**
 * Posts, ads and — once the discovery flags are on — suggestion rows.
 *
 * `HomeRow` is `FeedRow` plus one `discovery` member, so with every flag off
 * this alias describes exactly the same set of rows it described before: the
 * union widens, but `injectDiscoveryRows` returns its input unchanged and no
 * value of the new shape is ever constructed.
 */
type HomeFeedRow = HomeRow<PulsePost>;

type HomeScreenProps = {
  badges?: GlobalNavigationBadges;
  identity?: GlobalNavigationIdentity;
};

type FeedTab = {
  key: string;
  label: string;
  description: string;
};

const FEED_TABS: FeedTab[] = [
  { key: "for_you", label: "For You", description: "Ranked PulseSoc feed" },
  { key: "following", label: "Following", description: "Accounts you follow" },
  { key: "friends", label: "Friends", description: "Friend graph updates" },
  { key: "communities", label: "Communities", description: "Groups and rooms" },
  { key: "trending", label: "Trending", description: "Active public signals" },
  { key: "crypto", label: "Crypto", description: "Market and crypto signals" },
  { key: "scam_alerts", label: "Scam Alerts", description: "Safety and scam signals" },
  { key: "arena_highlights", label: "Arena Highlights", description: "Arena clips and moments" },
  { key: "roast_clips", label: "Roast Clips", description: "Creator clips and comedy" },
  { key: "questions", label: "Questions", description: "Questions and answers" },
  { key: "my_posts", label: "My Posts", description: "Your published posts" }
];

const FEED_SELECTION_KEY = "pulsesoc.native.home.feed.selection.v1";

/** Stable empty array: the outer list carries no rows in spatial mode. */
const EMPTY_FEED_ROWS: HomeFeedRow[] = [];

const HOME_COMMAND_ITEMS = [
  { label: "Home", route: "/pulse", icon: "⌂", active: true },
  { label: "Dashboard", route: "/pulse/dashboard", icon: "▦" },
  { label: "Reels", route: "/pulse/reels", icon: "▶" },
  { label: "Videos", route: "/dashboard/media/video-library", icon: "▣" },
  { label: "Premium", route: "/pulse/premium", icon: "◆" },
  { label: "Saved", route: "/pulse/saved", icon: "★" }
];

function useHomeAmbientMotionEnabled() {
  const focused = useIsFocused();
  const lowPowerMode = Battery.useLowPowerMode();
  const [appActive, setAppActive] = useState(AppState.currentState === "active");
  const [reduceMotion, setReduceMotion] = useState(false);

  useEffect(() => {
    AccessibilityInfo.isReduceMotionEnabled().then(setReduceMotion).catch(() => undefined);
    const motion = AccessibilityInfo.addEventListener("reduceMotionChanged", setReduceMotion);
    const appState = AppState.addEventListener("change", (next) => setAppActive(next === "active"));
    return () => {
      motion.remove();
      appState.remove();
    };
  }, []);

  return focused && appActive && !reduceMotion && !lowPowerMode;
}

export function HomeScreen({ badges, identity }: HomeScreenProps = {}) {
  const navigation = useNavigation<HomeNavigation>();
  const route = useRoute<RouteProp<AppTabParamList, "Home">>();
  const { authState } = useAuth();
  const isFocused = useIsFocused();
  const currentUserId = Number(authState.user?.user_id || 0);
  const bottomContentPadding = useBottomNavContentPadding();
  const listRef = useRef<FlatList<HomeFeedRow>>(null);
  const offsetRef = useRef(0);
  const hasMoreRef = useRef(false);
  const loadingMoreRef = useRef(false);
  const refreshingRef = useRef(false);
  const [posts, setPosts] = useState<PulsePost[]>([]);
  const bottomNavScroll = useBottomNavScrollVisibility({ enabled: posts.length > 0 });
  const [selectedFeed, setSelectedFeed] = useState(FEED_TABS[0].key);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [offline, setOffline] = useState(false);
  const [error, setError] = useState("");
  // Replaces a `busyPostId` scalar. A scalar cannot represent two cards acting at
  // once, and every social handler here only ever *wrote* it — nothing read it —
  // so it prevented no duplicate request. The guard locks per action+id in a ref,
  // which is what makes a second tap a no-op rather than a second write.
  const guard = useSocialActionGuard();
  const [statusItems, setStatusItems] = useState<PulseStatus[]>([]);
  /**
   * Bumped on pull-to-refresh only. It re-fetches the discovery modules and
   * doubles as the rotation offset, so a refresh both refreshes the suggestions
   * and moves which kind sits at which scroll depth — §3's rotation without the
   * re-shuffling that a random offset would cause on unrelated re-renders.
   */
  const [discoveryRefreshToken, setDiscoveryRefreshToken] = useState(0);
  const [statusLoading, setStatusLoading] = useState(true);
  const [statusOffline, setStatusOffline] = useState(false);
  const [statusError, setStatusError] = useState("");
  const [drawerOpen, setDrawerOpen] = useState(false);
  const ambientMotionEnabled = useHomeAmbientMotionEnabled();
  const [activePostId, setActivePostId] = useState<number | null>(null);
  const [ads, setAds] = useState<SponsoredAd[]>([]);
  const [hiddenAdKeys, setHiddenAdKeys] = useState<Set<string>>(() => new Set());
  // Keyed rows the vertical feed currently considers visible. Sponsored ads and
  // discovery carousels both need this, and they need it to mean the same thing:
  // a discovery row only autoplays a preview when its key is in here, which is
  // what keeps a carousel three screens down from decoding video.
  const [viewableRowKeys, setViewableRowKeys] = useState<Set<string>>(() => new Set());
  const feedViewabilityConfig = useRef({ itemVisiblePercentThreshold: 72 }).current;
  const onFeedViewableItemsChanged = useRef(({ viewableItems }: { viewableItems: ViewToken[] }) => {
    let nextActivePostId: number | null = null;
    const nextViewableRowKeys = new Set<string>();
    for (const token of viewableItems) {
      if (!token.isViewable) continue;
      const row = token.item as HomeFeedRow | undefined;
      if (!row) continue;
      if (row.type === "post" && nextActivePostId == null) nextActivePostId = row.post.id;
      if (row.type === "ad" || row.type === "discovery") nextViewableRowKeys.add(row.key);
    }
    setActivePostId(nextActivePostId);
    setViewableRowKeys((current) => {
      if (current.size === nextViewableRowKeys.size && [...current].every((key) => nextViewableRowKeys.has(key))) {
        return current;
      }
      return nextViewableRowKeys;
    });
  }).current;
  const selectionRestoredRef = useRef(false);
  // ---- Dormant spatial feed (flag-gated; OFF is the product decision) ------
  // Spatial paging shipped on Home to testers and was withdrawn: Reels is the
  // only motion-enabled browsing destination now. The pager below stays behind
  // `spatialHomeFeedEnabled` — which is OFF and stays OFF — purely so the
  // implementation remains recoverable, and it is a TOUCH-ONLY pager. Home has
  // no tilt hook, no immersive navigator and no reveal strip: motion is absent
  // from this screen by construction rather than by a flag reading false.
  const spatialFeed = spatialHomeFeedEnabled();
  const { width: windowWidth, height: windowHeight } = useWindowDimensions();
  const [spatialIndexByFeed, setSpatialIndexByFeed] = useState<Record<string, number>>({});
  const [spatialResetNonce, setSpatialResetNonce] = useState(0);
  const [newSignalsAvailable, setNewSignalsAvailable] = useState(false);
  const spatialIndex = spatialIndexByFeed[selectedFeed] || 0;
  const spatialIndexRef = useRef(0);
  spatialIndexRef.current = spatialIndex;
  const spatialPageHeight = Math.max(420, Math.round(windowHeight * 0.72));
  // -------------------------------------------------------------------------
  const activeTab = useMemo(() => FEED_TABS.find((tab) => tab.key === selectedFeed) || FEED_TABS[0], [selectedFeed]);
  const activeLivePost = useMemo(
    () => posts.find((post) => post.id === activePostId && Number(post.live?.live_session_id || 0) > 0),
    [activePostId, posts]
  );

  // The visible Live card alone polls canonical server state. This both keeps
  // ended/processing/replay transitions truthful and avoids room or API work
  // for off-screen Live posts.
  useEffect(() => {
    if (!isFocused || !activeLivePost) return undefined;
    let cancelled = false;
    const refresh = async () => {
      const detail = await getPostDetail(activeLivePost.id).catch(() => null);
      if (!cancelled && detail?.post) updatePost(activeLivePost.id, detail.post);
    };
    const timer = setInterval(() => { refresh().catch(() => undefined); }, 6_000);
    return () => { cancelled = true; clearInterval(timer); };
  }, [activeLivePost?.id, isFocused]);

  const isAuthenticated = currentUserId > 0;

  const loadAds = useCallback(
    async (feedKey = selectedFeed) => {
      if (!isAuthenticated) return;
      const served = await fetchSponsoredAds({ context: "home", feedContext: feedKey, limit: 3 });
      setAds(served);
    },
    [isAuthenticated, selectedFeed]
  );

  const availableAds = useMemo(
    () => ads.filter((ad) => !hiddenAdKeys.has(`${ad.campaignId}:${ad.creativeId}`)),
    [ads, hiddenAdKeys]
  );

  /**
   * Status ids already on Home's rail. §3: a suggestion the user can see two rows
   * up is noise, so the rail's contents are subtracted from the Statuses module.
   * Reels have no equivalent set — Home renders no reels — so none is passed.
   */
  const railStatusIds = useMemo(
    () => new Set(statusItems.map((status) => Number(status.id)).filter((id) => id > 0)),
    [statusItems]
  );

  const discovery = useHomeDiscovery({
    navigation,
    excludeStatusIds: railStatusIds,
    // §13: no suggestions for a signed-out viewer.
    enabled: isAuthenticated,
    refreshToken: discoveryRefreshToken
  });

  /**
   * Ads first, then suggestions threaded through the result.
   *
   * The order matters and is not interchangeable. `injectAds` owns the sponsored
   * cadence Advertising specified; running it first and composing over its output
   * means discovery can see where the ads landed and keep each ad with the post
   * that earned it, while an ad slot is never displaced by a carousel. With the
   * discovery flags off, `discovery.modules` is empty and `injectDiscoveryRows`
   * returns the ad-injected array itself — so this line produces byte-identical
   * rows to the previous `injectAds(...)` call, which is the §15 rollback path.
   */
  const feedRows = useMemo<HomeFeedRow[]>(
    () =>
      injectDiscoveryRows(injectAds(posts, availableAds, { interval: 5, leadIn: 3 }), discovery.modules, {
        dismissed: discovery.dismissed,
        rotationOffset: discovery.rotationOffset
      }),
    [posts, availableAds, discovery.modules, discovery.dismissed, discovery.rotationOffset]
  );

  const handleHideAd = useCallback((ad: SponsoredAd) => {
    setHiddenAdKeys((current) => {
      const next = new Set(current);
      next.add(`${ad.campaignId}:${ad.creativeId}`);
      return next;
    });
  }, []);

  const loadStatuses = useCallback(async () => {
    setStatusLoading(true);
    setStatusOffline(false);
    setStatusError("");
    try {
      const data = await listStatuses({ lane: "for_you" });
      const rail = data.rail_items?.length ? data.rail_items : data.items || [];
      setStatusItems(rail);
    } catch (statusLoadError) {
      const cached = await loadCachedStatuses("for_you");
      const rail = cached.rail_items?.length ? cached.rail_items : cached.items || [];
      if (rail.length) {
        setStatusItems(rail);
        setStatusOffline(true);
      } else {
        setStatusError(statusLoadError instanceof Error ? statusLoadError.message : "Status rail unavailable.");
      }
    } finally {
      setStatusLoading(false);
    }
  }, []);

  const load = useCallback(
    async (mode: "initial" | "refresh" | "more" = "initial", feedKey = selectedFeed) => {
      if (mode === "more" && (!hasMoreRef.current || loadingMoreRef.current)) return;
      if (mode === "refresh" && refreshingRef.current) return;
      const nextOffset = mode === "more" ? offsetRef.current : 0;
      setError("");
      setOffline(false);
      if (mode === "initial") setLoading(true);
      if (mode === "refresh") {
        refreshingRef.current = true;
        setRefreshing(true);
      }
      if (mode === "more") {
        loadingMoreRef.current = true;
        setLoadingMore(true);
      }
      try {
        const data = await listFeed({ feed: feedKey, tab: feedKey, offset: nextOffset, limit: 20 });
        setPosts((current) => (mode === "more" ? mergePosts(current, data.posts || []) : data.posts || []));
        offsetRef.current = Number(data.next_offset || nextOffset + (data.posts?.length || 0));
        hasMoreRef.current = Boolean(data.has_more);
      } catch (feedError) {
        const cached = await loadCachedFeed(feedKey);
        if (cached.length && mode !== "more") {
          setPosts(cached);
          setOffline(true);
        } else {
          setError(feedError instanceof Error ? feedError.message : "PulseSoc feed is unavailable.");
        }
      } finally {
        setLoading(false);
        setRefreshing(false);
        if (mode === "refresh") refreshingRef.current = false;
        loadingMoreRef.current = false;
        setLoadingMore(false);
      }
    },
    [selectedFeed]
  );

  useEffect(() => {
    AsyncStorage.getItem(FEED_SELECTION_KEY)
      .then((saved) => {
        if (saved && FEED_TABS.some((tab) => tab.key === saved)) setSelectedFeed(saved);
      })
      .catch(() => undefined)
      .finally(() => {
        selectionRestoredRef.current = true;
      });
  }, []);

  useEffect(() => {
    if (!selectionRestoredRef.current) return;
    AsyncStorage.setItem(FEED_SELECTION_KEY, selectedFeed).catch(() => undefined);
  }, [selectedFeed]);

  useEffect(() => {
    load("initial", selectedFeed).catch(() => undefined);
    loadAds(selectedFeed).catch(() => undefined);
  }, [load, loadAds, selectedFeed]);

  useEffect(() => {
    loadStatuses().catch(() => undefined);
  }, [loadStatuses]);

  useEffect(() => {
    if (route.params?.openComposer) {
      requestAnimationFrame(() => listRef.current?.scrollToOffset({ offset: 430, animated: true }));
    }
  }, [route.params?.openComposer]);

  /**
   * Background feed invalidations. In the spatial feed, a background update
   * must never yank the pager out from under the user: if they have swiped
   * away from the first post, we surface a restrained "New Signals" action
   * and apply only on their tap. At rest on page 0 (or in the legacy feed)
   * the refresh applies as before.
   */
  const backgroundRefresh = useCallback(() => {
    if (spatialFeed && spatialIndexRef.current > 0) {
      setNewSignalsAvailable(true);
      return;
    }
    load("refresh").catch(() => undefined);
  }, [load, spatialFeed]);

  useEffect(() => {
    const stopActivity = registerSyncInvalidation("activity", backgroundRefresh);
    const stopNotifications = registerSyncInvalidation("notifications", backgroundRefresh);
    const stopMarketplace = registerSyncInvalidation("marketplace", backgroundRefresh);
    return () => {
      stopActivity();
      stopNotifications();
      stopMarketplace();
    };
  }, [backgroundRefresh]);

  /** Media lifecycle: in the spatial feed only the settled page is active. */
  useEffect(() => {
    if (!spatialFeed) return;
    const row = feedRows[Math.min(spatialIndex, Math.max(0, feedRows.length - 1))];
    setActivePostId(row && row.type === "post" ? row.post.id : null);
    setViewableRowKeys(
      row && (row.type === "ad" || row.type === "discovery") ? new Set([row.key]) : new Set()
    );
  }, [spatialFeed, feedRows, spatialIndex]);

  const resetSpatialPosition = useCallback(() => {
    if (!spatialFeed) return;
    setSpatialIndexByFeed((current) => (current[selectedFeed] ? { ...current, [selectedFeed]: 0 } : current));
    setSpatialResetNonce((nonce) => nonce + 1);
    setNewSignalsAvailable(false);
  }, [selectedFeed, spatialFeed]);

  useEffect(() => registerRefreshDestination("home", {
    scrollToTop: () => {
      listRef.current?.scrollToOffset({ offset: 0, animated: true });
      // Home re-tap is explicit user intent: snap the spatial feed back to the
      // first signal without blanking what is on screen.
      resetSpatialPosition();
    },
    refresh: async () => {
      loadStatuses().catch(() => undefined);
      resetSpatialPosition();
      // Same bump as pull-to-refresh: a refresh is a refresh whichever gesture
      // asked for it, and leaving this out would make the tab re-tap the one
      // path that reloads the feed but leaves stale suggestions behind it.
      setDiscoveryRefreshToken((token) => token + 1);
      await load("refresh");
      AccessibilityInfo.announceForAccessibility?.("Home refreshed");
    },
    isRefreshing: () => refreshingRef.current,
    canRefresh: () => isAuthenticated
  }), [isAuthenticated, load, loadStatuses, resetSpatialPosition]);

  const updatePost = useCallback((postId: number, next: Partial<PulsePost>) => {
    setPosts((current) => current.map((post) => (post.id === postId ? { ...post, ...next } : post)));
  }, []);

  async function handleReact(post: PulsePost, reactionType: string) {
    const previous = post.viewer_reaction || "";
    const previousCounts = post.reaction_counts || {};
    const removing = previous === reactionType;
    const counts = { ...previousCounts };
    if (previous) counts[previous] = Math.max(0, Number(counts[previous] || 0) - 1);
    if (!removing) counts[reactionType] = Number(counts[reactionType] || 0) + 1;
    await guard.run(actionKey("post_react", post.id), () => reactToPost(post.id, reactionType), {
      // Switching reaction mid-flight is legitimate, so the second tap runs and
      // the sequence guard makes the LATER one win. Dropping it would leave the
      // button showing the reaction the user just abandoned.
      supersede: true,
      optimistic: () => updatePost(post.id, { viewer_reaction: removing ? "" : reactionType, reaction_counts: counts }),
      onResult: (result) => updatePost(post.id, {
        viewer_reaction: result.removed ? "" : result.viewer_reaction || result.reaction_type || reactionType,
        reaction_counts: result.reaction_counts || counts
      }),
      onRollback: () => updatePost(post.id, { viewer_reaction: previous, reaction_counts: previousCounts }),
      // Previously an empty `catch {}`: the count snapped back with no
      // explanation, which is indistinguishable from the tap not registering.
      onError: setError
    });
  }

  /**
   * Saving is the one social action that is not local to this list.
   *
   * The optimistic-guard pattern used for reactions and reposts updates this
   * screen's copy of the post, which is right for a count nobody else is
   * showing. A saved state is shown by the profile behind this feed, by the
   * detail screen pushed on top of it, and by the Saved collection — all
   * holding their own copies. Routing through the shared store updates every
   * one of them from a single tap, and keeps the in-flight lock global so two
   * screens showing the same post cannot both mutate it at once.
   */
  async function handleSave(post: PulsePost) {
    const savableId = savablePostId(post);
    // What to ask for is decided from the store, not from this screen's copy of
    // the post: the copy can be several taps out of date if the user saved the
    // same content from the detail screen pushed on top of this one.
    const previousSaved = peekSaveState("post", savableId)?.saved ?? Boolean(post.saved ?? post.is_saved);
    const outcome = await setSaved({ type: "post", id: savableId }, !previousSaved);
    updatePost(post.id, { saved: outcome.saved, is_saved: outcome.saved });
    if (!outcome.ok && outcome.message) setError(outcome.message);
  }

  async function handleRepost(post: PulsePost) {
    const previousReposted = Boolean(post.reposted);
    const previousCount = Number(post.repost_count || 0);
    const undo = previousReposted;
    // A real toggle now. This was one-way while the route was create-only and
    // returned neither a `reposted` flag nor a count — an un-repost would have
    // claimed something the server never performed. DELETE soft-deletes every
    // live repost row the viewer holds, so undo is honest even for the duplicate
    // rows the old create-only route left behind.
    await guard.run(actionKey("post_repost", post.id), () => repostPost(post.id, { undo }), {
      optimistic: () => updatePost(post.id, {
        reposted: !undo,
        repost_count: undo ? Math.max(0, previousCount - 1) : previousCount + 1
      }),
      // The server's count is authoritative because it also reflects everyone
      // else's reposts, which this screen cannot see. Falling back to the
      // optimistic value keeps the card correct if an older build omits it.
      onResult: (result) => updatePost(post.id, {
        reposted: Boolean(result.reposted ?? result.is_reposted ?? !undo),
        repost_count: typeof result.repost_count === "number"
          ? result.repost_count
          : undo ? Math.max(0, previousCount - 1) : previousCount + 1
      }),
      onRollback: () => updatePost(post.id, { reposted: previousReposted, repost_count: previousCount }),
      onError: setError
    });
  }

  async function handleShare(post: PulsePost) {
    const author = post.author || post.user || {};
    await sharePulseObject({
      kind: "post",
      url: pulsePostUrl(post.id),
      title: post.title || "PulseSoc post",
      description: post.body || post.text || post.content,
      author: author.display_name || author.name || author.username || post.author_name,
      previewImageUrl: post.thumbnail_url || post.image_url
    }).catch(() => undefined);
  }

  async function handleInlineComment(post: PulsePost, body: string) {
    const previousCount = Number(post.comment_count || post.comments_count || 0);
    const previousPreview = post.preview_comments || [];
    await guard.run(actionKey("post_comment", post.id), () => addPostComment(post.id, body), {
      // Optimistic on the counter only. The comment body itself is not shown
      // until the server returns a row, because a preview comment with no id
      // cannot be replied to, reacted to or deleted.
      optimistic: () => updatePost(post.id, { comment_count: previousCount + 1, comments_count: previousCount + 1 }),
      onResult: (result) => {
        const nextComment = result.comment;
        if (nextComment) {
          updatePost(post.id, {
            comment_count: previousCount + 1,
            comments_count: previousCount + 1,
            preview_comments: [nextComment, ...previousPreview].slice(0, 2)
          });
        } else if (result.comments?.length) {
          updatePost(post.id, {
            comment_count: result.comments.length,
            comments_count: result.comments.length,
            preview_comments: result.comments.slice(0, 2)
          });
        } else {
          load("refresh").catch(() => undefined);
        }
        // A comment does generate a notification for the author, so the activity
        // and notification caches are genuinely stale now.
        invalidateNativeSync(["activity", "notifications"], "home_comment", [
          {
            event_type: "comment_created",
            entity_type: "post",
            entity_id: post.id,
            invalidates: ["activity", "notifications"],
            metadata: { source: "native_home_feed_inline_comment" }
          }
        ]).catch(() => undefined);
      },
      // Previously there was a `finally` but no `catch`, so a failed inline
      // comment rejected out of the handler and left the count raised.
      onRollback: () => updatePost(post.id, {
        comment_count: previousCount,
        comments_count: previousCount,
        preview_comments: previousPreview
      }),
      onError: setError
    });
  }

  async function handleFollow(post: PulsePost) {
    const publicId = post.author?.public_player_id || post.author_public_player_id || "";
    const previousFollows = Boolean(post.viewer_follows_author);
    const applyFollowing = (following: boolean) => setPosts((current) =>
      current.map((item) => {
        const itemPublicId = item.author?.public_player_id || item.author_public_player_id || "";
        if (publicId && itemPublicId === publicId) return { ...item, viewer_follows_author: following };
        return item.id === post.id ? { ...item, viewer_follows_author: following } : item;
      })
    );
    await guard.run(actionKey("post_follow_author", post.id), () => toggleFollowAuthor(post), {
      optimistic: () => applyFollowing(!previousFollows),
      onResult: (result) => {
        const following = Boolean(result.following);
        applyFollowing(following);
        invalidateNativeSync(["activity", "notifications"], "home_follow", [
          {
            event_type: following ? "follow" : "unfollow",
            entity_type: "profile",
            entity_id: publicId || post.author?.user_id || post.author?.id || "unknown",
            invalidates: ["activity", "notifications"],
            metadata: { source: "native_home_feed" }
          }
        ]).catch(() => undefined);
      },
      // Previously a `finally` with no `catch`: a failed follow rejected out of
      // the handler and the button was left claiming the follow had happened.
      onRollback: () => applyFollowing(previousFollows),
      onError: setError
    });
  }

  async function handleHide(post: PulsePost) {
    await guard.run(actionKey("post_hide", post.id), () => hidePost(post.id), {
      // Removal is applied on confirmation, not optimistically: putting a hidden
      // post back after a failure would look like the feed resurrecting it.
      onResult: () => {
        setPosts((current) => current.filter((item) => item.id !== post.id));
        invalidateNativeSync(["activity", "notifications"], "home_hide", [
          {
            event_type: "pulse_post_hidden",
            entity_type: "post",
            entity_id: post.id,
            invalidates: ["activity", "notifications"],
            metadata: { source: "native_home_feed" }
          }
        ]).catch(() => undefined);
      },
      onError: (message) => setError(message || "Post could not be hidden.")
    });
  }

  async function handleMute(post: PulsePost) {
    const authorId = Number(post.author?.user_id || post.author?.id || 0);
    const publicId = post.author?.public_player_id || post.author_public_player_id || "";
    await guard.run(actionKey("post_mute_author", post.id), () => mutePostAuthor(post), {
      onResult: (result) => {
        const mutedId = Number(result.muted_user_id || authorId || 0);
        setPosts((current) =>
          current.filter((item) => {
            const itemAuthorId = Number(item.author?.user_id || item.author?.id || 0);
            const itemPublicId = item.author?.public_player_id || item.author_public_player_id || "";
            if (mutedId && itemAuthorId === mutedId) return false;
            if (publicId && itemPublicId === publicId) return false;
            return item.id !== post.id;
          })
        );
        invalidateNativeSync(["activity", "notifications"], "home_mute", [
          {
            event_type: "pulse_user_muted",
            entity_type: "user",
            entity_id: mutedId || publicId || "unknown",
            invalidates: ["activity", "notifications"],
            metadata: { source: "native_home_feed" }
          }
        ]).catch(() => undefined);
      },
      onError: (message) => setError(message || "User could not be muted.")
    });
  }

  async function handleDelete(post: PulsePost) {
    await guard.run(actionKey("post_delete", post.id), () => deletePost(post.id), {
      onResult: () => {
        setPosts((current) => current.filter((item) => item.id !== post.id));
        if (activePostId === post.id) setActivePostId(null);
        invalidateNativeSync(["activity", "notifications"], "home_delete", [
          {
            event_type: "pulse_post_deleted",
            entity_type: "post",
            entity_id: post.id,
            invalidates: ["activity", "notifications"],
            metadata: { source: "native_home_feed" }
          }
        ]).catch(() => undefined);
      },
      // describeDeleteError, not the guard's generic copy: delete has its own
      // permission and already-deleted wording that users act on.
      onError: (_message, err) => setError(describeDeleteError(err, "Post"))
    });
  }

  function selectFeed(feedKey: string) {
    if (feedKey === selectedFeed) return;
    setSelectedFeed(feedKey);
    setPosts([]);
    offsetRef.current = 0;
    hasMoreRef.current = false;
    loadingMoreRef.current = false;
    setLoading(true);
    // Each category keeps its own spatial position; snap the pager to the
    // incoming category's remembered index.
    if (spatialFeed) {
      setSpatialResetNonce((nonce) => nonce + 1);
      setNewSignalsAvailable(false);
    }
  }

  function refreshHome() {
    listRef.current?.scrollToOffset({ offset: 0, animated: true });
    resetSpatialPosition();
    loadStatuses().catch(() => undefined);
    load("refresh").catch(() => undefined);
    loadAds().catch(() => undefined);
    setDiscoveryRefreshToken((token) => token + 1);
  }

  function openHomeRoute(routePath: string) {
    setDrawerOpen(false);
    openNativeRoute(navigation, routePath);
  }

  /**
   * Renders one feed row (post, ad, or suggestion). Shared verbatim between the
   * legacy vertical list and the spatial pager so every post type, action and ad
   * behavior is identical in both modes.
   */
  const renderFeedRow = (row: HomeFeedRow) => {
    if (row.type === "discovery") {
      return (
        <DiscoveryRowView
          module={row.module}
          slot={row.slot}
          {...discovery.actions}
          onSeeAll={discovery.seeAllFor(row.module.kind)}
          pendingFriendKeys={discovery.pendingFriendKeys}
          joinedGroupSlugs={discovery.joinedGroupSlugs}
          // Both conditions, because they fail in different ways: an unfocused
          // Home is still "visible" to FlatList, so focus is what stops a preview
          // when the user navigates away, and row viewability is what stops the
          // eleven carousels that are mounted but off screen.
          isRowVisible={isFocused && viewableRowKeys.has(row.key)}
        />
      );
    }
    if (row.type === "ad") {
      return (
        <SponsoredAdCard
          ad={row.ad}
          isViewable={viewableRowKeys.has(row.key)}
          edgeInset={12}
          navigation={navigation}
          onHide={handleHideAd}
        />
      );
    }
    const item = row.post;
    return (
      <PostCard
        post={item}
        busy={guard.isItemBusy(item.id)}
        active={activePostId === item.id}
        motionEnabled={ambientMotionEnabled}
        onOpen={(post) => navigation.navigate("PostDetail", { postId: post.id, title: "Post" })}
        onOpenLive={(post) => {
          const liveId = Number(post.live?.live_session_id || 0);
          if (liveId > 0) navigation.navigate("LiveDetail", { liveId, title: post.title || "PulseSoc Live" });
        }}
        onReact={handleReact}
        onSave={handleSave}
        onRepost={handleRepost}
        onPromote={(post) => navigation.navigate("GrowthCenter", { contentType: "post", contentId: post.id, title: "Promote Post" })}
        onShare={handleShare}
        onComment={(post) => navigation.navigate("PostDetail", { postId: post.id, title: "Comments" })}
        onSubmitComment={handleInlineComment}
        onReport={(post) =>
          navigation.navigate("SafetyHub", {
            title: "Report",
            section: "reports",
            reportType: "post",
            reportTarget: String(post.id)
          })
        }
        onHide={handleHide}
        onBlock={(post) =>
          navigation.navigate("SafetyHub", {
            title: "Blocked Users",
            section: "blocks",
            blockTarget: post.author?.public_player_id || post.author_public_player_id || post.author?.username || post.author_username || ""
          })
        }
        onMute={handleMute}
        onFollow={handleFollow}
        onDelete={isContentOwner(item, currentUserId) ? handleDelete : undefined}
        onAuthorPress={(post) => {
          const params = profileNavigationParams(profileTargetFromPost(post), post.author?.display_name || "Profile");
          if (params) navigation.navigate("ProfileDetail", params);
        }}
      />
    );
  };

  return (
    <View style={styles.root}>
      {/*
        The feed used to draw its own `GalacticAtmosphere` here. It now defers to
        the single `PulseBackground` at the app root: this was a plain full-screen
        backdrop, and its opaque gradient hid the shared layer on the app's
        most-visited surface. The clipped hero panel further down keeps its own
        atmosphere — that one is a design element, not a backdrop.
      */}
      {/*
        `error` used to be reachable only through ListEmptyComponent, so a failed
        like, comment, follow or delete set a message that nothing rendered as
        long as the feed had a single post in it — which is the normal case. The
        handlers reported, and the report went nowhere. The banner sits outside
        the FlatList so it is visible whether or not the list is empty and cannot
        be scrolled away from, and it is dismissible so a stale failure does not
        sit over the feed forever.
      */}
      {error ? (
        <Pressable
          accessibilityRole="button"
          accessibilityLabel={`${error}. Tap to dismiss.`}
          accessibilityLiveRegion="polite"
          style={styles.actionErrorBanner}
          onPress={() => setError("")}
        >
          <Text style={styles.actionErrorText}>{error}</Text>
          <Text style={styles.actionErrorDismiss}>Dismiss</Text>
        </Pressable>
      ) : null}
      <FlatList
        testID="native-home-feed"
        ref={listRef}
        style={styles.list}
        contentContainerStyle={[styles.content, { paddingBottom: bottomContentPadding }]}
        data={spatialFeed ? EMPTY_FEED_ROWS : feedRows}
        keyExtractor={(row) => row.key}
        refreshControl={<RefreshControl refreshing={refreshing} tintColor={colors.accent} onRefresh={() => refreshHome()} />}
        ListHeaderComponent={
          <HomeHeader
            feedTabs={FEED_TABS}
            selectedFeed={selectedFeed}
            statusItems={statusItems}
            statusLoading={statusLoading}
            statusOffline={statusOffline}
            statusError={statusError}
            posts={posts}
            offline={offline}
            onOpenDrawer={() => setDrawerOpen(true)}
            onOpenSearch={() => navigation.navigate("Tabs", { screen: "Search" })}
            onOpenActivity={() => navigation.navigate("ActivityInbox", { title: "Activity Inbox" })}
            onOpenProfile={() => navigation.navigate("Tabs", { screen: "Profile" })}
            badges={badges}
            identity={identity}
            initiallyExpandComposer={Boolean(route.params?.openComposer)}
            initialComposerMode={route.params?.composerMode || "post"}
            captureReturnNonce={route.params?.composerReturnNonce || ""}
            shareHandoffNonce={route.params?.shareHandoffNonce || ""}
            onRefresh={refreshHome}
            onSelectFeed={selectFeed}
            onOpenUndx={() => navigation.navigate("Tabs", { screen: "PulseAI" })}
            ambientMotionEnabled={ambientMotionEnabled}
            onOpenRadioLibrary={() => openDashboardRoute(navigation, "/pulse/music#pulse-radio")}
            onOpenLive={() => navigation.navigate("Tabs", { screen: "Live" })}
            onOpenSafety={() => navigation.navigate("SafetyHub", { title: "Safety Hub" })}
            onOpenRoute={openHomeRoute}
            onAddStatus={() => navigation.navigate("Tabs", { screen: "Status", params: { openCreator: true } })}
            onViewStatuses={() => navigation.navigate("Tabs", { screen: "Status" })}
            onOpenStatus={(status) => navigation.navigate("StatusDetail", { statusId: status.id, title: status.author?.display_name || "Status" })}
            onOpenCamera={(cameraMode, composerMode) => {
              const target = composerMode === "status" ? "status" : composerMode === "reel" ? "reel" : "feed";
              const routeMode = composerMode === "status" ? "status" : composerMode === "reel" ? "reel" : cameraMode === "video" ? "video" : "photo";
              navigation.navigate("CameraStudio", {
                target,
                mode: routeMode,
                captureMode: cameraMode === "reel" ? "video" : cameraMode,
                returnToComposer: true,
                composerMode,
                title: composerMode === "reel" ? "Reel Camera" : composerMode === "status" ? "Status Camera" : cameraMode === "video" ? "Video Camera" : "Camera"
              });
            }}
            onCreated={(post) => {
              if (post) setPosts((current) => [post, ...current.filter((item) => item.id !== post.id)]);
              invalidateNativeSync(["activity", "notifications"], "home_publish", [
                {
                  event_type: "post_published",
                  entity_type: "post",
                  entity_id: post?.id || post?.post_id || "pending",
                  invalidates: ["activity", "notifications"],
                  metadata: { source: "native_home_composer" }
                }
              ]).catch(() => undefined);
              load("refresh").catch(() => undefined);
              loadStatuses().catch(() => undefined);
            }}
            onOpenMusic={() => navigation.navigate("Music", { title: "PulseSoc Music" })}
            onOpenPreview={(token) => navigation.navigate("ContentPreview", { token })}
          />
        }
        ListFooterComponent={
          spatialFeed ? (
            <>
              {newSignalsAvailable ? (
                <Pressable
                  accessibilityRole="button"
                  accessibilityLabel="New Signals available. Tap to load."
                  style={styles.newSignalsPill}
                  onPress={refreshHome}
                >
                  <Text style={styles.newSignalsText}>New Signals available</Text>
                </Pressable>
              ) : null}
              {feedRows.length > 0 ? (
                <SpatialPager
                  testID="spatial-home-feed"
                  accessibilityLabel={`${activeTab.label} feed, swipe horizontally between signals`}
                  data={feedRows}
                  keyExtractor={(row) => row.key}
                  pageWidth={windowWidth}
                  pageHeight={spatialPageHeight}
                  index={spatialIndex}
                  resetNonce={spatialResetNonce}
                  onIndexSettled={(nextIndex) => {
                    setSpatialIndexByFeed((current) =>
                      current[selectedFeed] === nextIndex ? current : { ...current, [selectedFeed]: nextIndex }
                    );
                  }}
                  onEndReached={() => load("more").catch(() => undefined)}
                  renderPage={(row) => (
                    <ScrollView
                      style={styles.spatialPageScroll}
                      showsVerticalScrollIndicator={false}
                      nestedScrollEnabled
                    >
                      {renderFeedRow(row)}
                    </ScrollView>
                  )}
                />
              ) : null}
            </>
          ) : loadingMore ? (
            <ActivityIndicator style={styles.footer} color={colors.accent} />
          ) : null
        }
        ListEmptyComponent={
          spatialFeed && feedRows.length > 0 ? null : (
            <LogiNexusEmptyState
              title={loading ? "Opening the PulseSoc network" : error ? "Connection interrupted" : `${activeTab.label} is quiet`}
              body={loading ? "Loading your canonical feed…" : error || `No signals matched ${activeTab.label}. Pull to refresh or switch filters.`}
              tone={error ? "warning" : "default"}
            />
          )
        }
        viewabilityConfig={feedViewabilityConfig}
        onViewableItemsChanged={onFeedViewableItemsChanged}
        renderItem={({ item: row }) => renderFeedRow(row)}
        onEndReached={() => load("more").catch(() => undefined)}
        onEndReachedThreshold={0.35}
        onScroll={bottomNavScroll.onScroll}
        onScrollBeginDrag={bottomNavScroll.onScrollBeginDrag}
        scrollEventThrottle={bottomNavScroll.scrollEventThrottle}
      />
      <MasterNavigationDrawer visible={drawerOpen} onClose={() => setDrawerOpen(false)} onOpenRoute={openHomeRoute} />
    </View>
  );
}

function HomeHeader({
  feedTabs,
  selectedFeed,
  statusItems,
  statusLoading,
  statusOffline,
  statusError,
  posts,
  offline,
  onOpenDrawer,
  onOpenSearch,
  onOpenActivity,
  onOpenProfile,
  badges,
  identity,
  ambientMotionEnabled,
  initiallyExpandComposer,
  initialComposerMode,
  captureReturnNonce,
  shareHandoffNonce,
  onRefresh,
  onSelectFeed,
  onOpenUndx,
  onOpenRadioLibrary,
  onOpenLive,
  onOpenSafety,
  onOpenRoute,
  onAddStatus,
  onViewStatuses,
  onOpenStatus,
  onOpenCamera,
  onCreated,
  onOpenMusic,
  onOpenPreview
}: {
  feedTabs: FeedTab[];
  selectedFeed: string;
  statusItems: PulseStatus[];
  statusLoading: boolean;
  statusOffline: boolean;
  statusError: string;
  posts: PulsePost[];
  offline: boolean;
  onOpenDrawer: () => void;
  onOpenSearch: () => void;
  onOpenActivity: () => void;
  onOpenProfile: () => void;
  badges?: GlobalNavigationBadges;
  identity?: GlobalNavigationIdentity;
  ambientMotionEnabled: boolean;
  initiallyExpandComposer: boolean;
  initialComposerMode: "post" | "status" | "reel";
  captureReturnNonce: string;
  shareHandoffNonce: string;
  onRefresh: () => void;
  onSelectFeed: (feedKey: string) => void;
  onOpenUndx: () => void;
  onOpenRadioLibrary: () => void;
  onOpenLive: () => void;
  onOpenSafety: () => void;
  onOpenRoute: (routePath: string) => void;
  onAddStatus: () => void;
  onViewStatuses: () => void;
  onOpenStatus: (status: PulseStatus) => void;
  onOpenCamera: (mode: "photo" | "video" | "reel", composerMode: "post" | "status" | "reel") => void;
  onCreated: (post?: PulsePost) => void;
  onOpenMusic: (composerMode: "post" | "status" | "reel") => void;
  onOpenPreview: (token: string) => void;
}) {
  const { width } = useWindowDimensions();
  const compactHero = width < 360;
  const wideCanvas = width >= 900;
  return (
    <View style={styles.header}>
      <HomeTopBar onOpenDrawer={onOpenDrawer} onOpenSearch={onOpenSearch} onOpenActivity={onOpenActivity} onOpenProfile={onOpenProfile} badges={badges} identity={identity} />
      <View style={[styles.homeCanvas, wideCanvas && styles.homeCanvasWide]}>
        {wideCanvas ? <HomeCommandRail onOpenRoute={onOpenRoute} onOpenPulseRadio={onOpenRadioLibrary} /> : null}
        <View style={styles.homePrimaryColumn}>
          <PulseNetworkHero posts={posts} statuses={statusItems} offline={offline || statusOffline} compact={compactHero} ambientMotionEnabled={ambientMotionEnabled} onRefresh={onRefresh} onOpenUndx={onOpenUndx} onOpenRadioLibrary={onOpenRadioLibrary} onOpenLive={onOpenLive} onOpenSafety={onOpenSafety} />
          <StatusRail
            items={statusItems}
            loading={statusLoading}
            offline={statusOffline}
            error={statusError}
            onAddStatus={onAddStatus}
            onOpenStatus={onOpenStatus}
            onViewAll={onViewStatuses}
          />
          <HomePulseComposer
            initiallyExpanded={initiallyExpandComposer}
            initialMode={initialComposerMode}
            captureReturnNonce={captureReturnNonce}
            shareHandoffNonce={shareHandoffNonce}
            identity={identity}
            onCreated={onCreated}
            onOpenCamera={onOpenCamera}
            onOpenMusic={onOpenMusic}
            onOpenRoute={onOpenRoute}
            onOpenPreview={onOpenPreview}
          />
          <View style={styles.feedTabsWrap}>
            <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.feedTabs}>
              {feedTabs.map((tab) => (
                <Pressable accessibilityRole="button" key={tab.key} style={[styles.feedTab, selectedFeed === tab.key && styles.feedTabActive]} onPress={() => onSelectFeed(tab.key)}>
                  <Text style={[styles.feedTabText, selectedFeed === tab.key && styles.feedTabTextActive]}>{tab.label}</Text>
                </Pressable>
              ))}
            </ScrollView>
          </View>
        </View>
        {wideCanvas ? (
          <HomeWebSideRail
            posts={posts}
            statuses={statusItems}
            offline={offline || statusOffline}
            onOpenUndx={onOpenUndx}
            onOpenSafety={onOpenSafety}
          />
        ) : null}
      </View>
    </View>
  );
}

function HomeCommandRail({
  onOpenRoute,
  onOpenPulseRadio
}: {
  onOpenRoute: (routePath: string) => void;
  onOpenPulseRadio: () => void;
}) {
  return (
    <View style={styles.commandRail}>
      <LogiNexusPanel style={styles.commandIdentityCard} tone="default">
        <View style={styles.commandIdentityRow}>
          <Text style={styles.commandIdentityIcon}>RC</Text>
          <View style={styles.commandIdentityCopy}>
            <Text style={styles.commandIdentityTitle} numberOfLines={1}>Your PulseSoc OS</Text>
            <Text style={styles.commandIdentityMeta} numberOfLines={1}>Navigate, create, learn</Text>
          </View>
        </View>
      </LogiNexusPanel>
      <View style={styles.commandShortcutGroup}>
        <Text style={styles.commandSectionTitle}>Today</Text>
        <Pressable accessibilityRole="button" style={styles.commandShortcutCard} onPress={() => onOpenRoute("/pulse/dashboard")}>
          <Text style={styles.commandShortcutTitle}>Dashboard</Text>
          <Text style={styles.commandShortcutMeta}>Account command center</Text>
        </Pressable>
        <Pressable accessibilityRole="button" style={styles.commandShortcutCard} onPress={() => onOpenRoute("/pulse/growth")}>
          <Text style={styles.commandShortcutTitle}>Promote</Text>
          <Text style={styles.commandShortcutMeta}>Owner tools</Text>
        </Pressable>
      </View>
      <View style={styles.commandNavGroup}>
        {HOME_COMMAND_ITEMS.map((item) => (
          <Pressable
            key={item.route}
            accessibilityRole="button"
            accessibilityLabel={`Open ${item.label}`}
            style={[styles.commandNavItem, item.active && styles.commandNavItemActive]}
            onPress={() => onOpenRoute(item.route)}
          >
            <Text style={styles.commandNavIcon}>{item.icon}</Text>
            <Text style={styles.commandNavLabel} numberOfLines={1}>{item.label}</Text>
          </Pressable>
        ))}
      </View>
      <LogiNexusPanel style={styles.commandRadioPanel} tone="creator">
        <Text style={styles.commandRadioTitle}>Pulse Radio</Text>
        <View style={styles.commandRadioTabs}>
          <Text style={[styles.commandRadioTab, styles.commandRadioTabActive]}>Radio</Text>
          <Text style={styles.commandRadioTab}>Podcasts</Text>
          <Text style={styles.commandRadioTab}>Discover</Text>
        </View>
        <Pressable accessibilityRole="button" style={styles.commandRadioNow} onPress={onOpenPulseRadio}>
          <View style={styles.commandRadioDot} />
          <View style={styles.commandRadioCopy}>
            <Text style={styles.commandRadioNowTitle} numberOfLines={1}>Pulse Radio</Text>
            <Text style={styles.commandRadioNowMeta} numberOfLines={1}>Approved PulseSoc streams</Text>
          </View>
        </Pressable>
        <View style={styles.commandRadioActions}>
          <Pressable accessibilityRole="button" style={styles.commandRadioPrimary} onPress={onOpenPulseRadio}>
            <Text style={styles.commandRadioPrimaryText}>Play / Pause</Text>
          </Pressable>
          <Pressable accessibilityRole="button" style={styles.commandRadioSecondary} onPress={onOpenPulseRadio}>
            <Text style={styles.commandRadioSecondaryText}>Next</Text>
          </Pressable>
        </View>
        <Text style={styles.commandRadioBody}>Radio loads from approved creator-safe tracks. If no track is available, controls report that state instead of faking playback.</Text>
      </LogiNexusPanel>
    </View>
  );
}

function HomeTopBar({
  onOpenDrawer,
  onOpenSearch,
  onOpenActivity,
  onOpenProfile,
  badges,
  identity
}: {
  onOpenDrawer: () => void;
  onOpenSearch: () => void;
  onOpenActivity: () => void;
  onOpenProfile: () => void;
  badges?: GlobalNavigationBadges;
  identity?: GlobalNavigationIdentity;
}) {
  return (
    <LogiNexusGlobalHeader
      title="PulseSoc"
      mode="home"
      showDrawer
      onOpenDrawer={onOpenDrawer}
      onOpenSearch={onOpenSearch}
      onOpenActivity={onOpenActivity}
      onOpenProfile={onOpenProfile}
      badges={badges}
      identity={identity}
      testID="home-global-command-strip"
    />
  );
}

function PulseNetworkHero({
  posts,
  statuses,
  offline,
  ambientMotionEnabled,
  onRefresh,
  onOpenUndx,
  onOpenRadioLibrary,
  onOpenLive,
  onOpenSafety,
  compact
}: {
  posts: PulsePost[];
  statuses: PulseStatus[];
  offline: boolean;
  compact: boolean;
  ambientMotionEnabled: boolean;
  onRefresh: () => void;
  onOpenUndx: () => void;
  onOpenRadioLibrary: () => void;
  onOpenLive: () => void;
  onOpenSafety: () => void;
}) {
  const creatorCount = new Set(
    [
      ...posts.map((post) => post.author?.id || post.author?.user_id || post.author_username || post.author_name),
      ...statuses.map((status) => status.author?.id || status.author?.user_id || status.author_name)
    ].filter(Boolean)
  ).size;
  const liveCount = statuses.filter((status) => status.author_live || status.status_type === "live").length;
  const alertCount = posts.filter((post) => /scam|alert|warning|security|safety/i.test(`${post.title || ""} ${post.body || ""}`)).length;
  const signalMetric = posts.length ? formatHeroMetric(posts.length) : offline ? "Cached" : "—";
  const mood = posts.length ? "Curious" : offline ? "Cached" : "Curious";
  const summary = posts.length
    ? `${posts.length} public posts summarized. Aggregate activity only.`
    : offline
      ? "Cached network signals remain available."
      : "Signals are loading quietly so the feed stays fast.";
  return (
    <LogiNexusPanel style={[styles.hero, compact && styles.heroCompact]} tone="default">
      <View pointerEvents="none" style={styles.heroAtmosphere}>
        <GalacticAtmosphere variant="feed" testID="pulse-network-galactic-atmosphere" />
      </View>
      <View style={styles.heroTopLine}>
        <LogiNexusBadge label="Pulse Network" />
        <Pressable accessibilityRole="button" accessibilityLabel="Refresh Pulse Network" style={styles.heroHealthPill} onPress={onRefresh}>
          <View style={styles.heroHealthDot} />
          <Text style={styles.heroHealthText}>{offline ? "Cached" : "Connected"}</Text>
        </Pressable>
      </View>
      <View style={styles.heroMoodRow}>
        <View style={styles.heroMoodCopy}>
          <Text style={styles.heroMoodTitle} numberOfLines={1}>{mood}</Text>
          <Text style={styles.heroMoodSummary} numberOfLines={2}>{summary}</Text>
        </View>
        <PulseRadioHeroControl />
      </View>
      <View style={styles.heroCompactMetricRow}>
        <HeroMetricBlock value={signalMetric} label={posts.length ? "Signals" : offline ? "Cached" : "Ready"} tone="default" />
        <HeroMetricBlock value={creatorCount} label="creators" tone="intelligence" />
        <HeroMetricBlock value={liveCount} label="live" tone="danger" onPress={onOpenLive} />
      </View>
      <View style={styles.heroQuickRow}>
        <HeroTile label="UNDX" value={String(alertCount)} body="UNDX alerts" tone="intelligence" icon="◇" onPress={onOpenUndx} />
        <HeroTile label="Pulse Radio" value="Radio" body="Open library" tone="creator" icon="≋" onPress={onOpenRadioLibrary} />
        <HeroTile label="Safety Shield" value={String(alertCount)} body="Scan ready" tone="safety" icon="⌾" onPress={onOpenSafety} />
      </View>
    </LogiNexusPanel>
  );
}

const PulseRadioHeroControl = memo(function PulseRadioHeroControl() {
  const [radio, setRadio] = useState<PulseRadioState>(getPulseRadioState());
  useEffect(() => subscribePulseRadio(setRadio), []);
  const busy = radio.status === "connecting" || radio.status === "buffering";
  const playing = radio.status === "playing";
  const unavailable = radio.status === "error" || radio.status === "offline";
  const interrupted = radio.userWantsPlayback && Boolean(radio.interruptedBy);
  const stateLabel = playing ? "playing" : busy ? "connecting" : unavailable ? "unavailable" : interrupted ? "paused for active audio" : "paused";
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={`Pulse Radio, ${stateLabel}`}
      accessibilityHint={playing ? "Pauses Pulse Radio" : interrupted ? "Keeps Pulse Radio paused instead of resuming after active audio ends" : "Starts Pulse Radio"}
      accessibilityState={{ busy }}
      testID="home-pulse-radio-toggle"
      style={[styles.heroRadioPill, playing && styles.heroRadioPillPlaying]}
      onPress={() => togglePulseRadio().catch(() => undefined)}
    >
      <View style={[styles.heroRadioOrb, playing && styles.heroRadioOrbPlaying]}>
        <Text style={styles.heroRadioIcon}>{playing ? "Ⅱ" : busy ? "…" : "▶"}</Text>
      </View>
      <View style={styles.heroRadioCopy}>
        <Text style={styles.heroRadioLabel}>Pulse Radio</Text>
        <Text style={[styles.heroRadioMeta, unavailable && styles.heroRadioMetaError]} numberOfLines={1}>{radio.message}</Text>
      </View>
      <View pointerEvents="none" style={styles.heroRadioWave}>
        {[8, 15, 11, 20, 13, 17, 9].map((height, index) => (
          <View key={`${height}-${index}`} style={[styles.heroRadioWaveBar, { height: playing ? height : Math.min(8, height) }, playing && styles.heroRadioWaveBarPlaying]} />
        ))}
      </View>
    </Pressable>
  );
});

function HomeWebSideRail({
  posts,
  statuses,
  offline,
  onOpenUndx,
  onOpenSafety
}: {
  posts: PulsePost[];
  statuses: PulseStatus[];
  offline: boolean;
  onOpenUndx: () => void;
  onOpenSafety: () => void;
}) {
  const todayCount = posts.filter((post) => isToday(post.created_at)).length;
  const trendLabel = trendingLabel(posts);
  return (
    <View style={styles.sideRail}>
      <LogiNexusPanel style={styles.sidePanel} tone="intelligence">
        <Text style={styles.sidePanelTitle}>PulseSoc Intelligence</Text>
        <View style={styles.sideMetricGrid}>
          <View style={styles.sideMetricBox}>
            <Text style={styles.sideMetricValue}>{todayCount}</Text>
            <Text style={styles.sideMetricLabel}>posts today</Text>
          </View>
          <View style={styles.sideMetricBox}>
            <Text style={styles.sideMetricValue}>{offline ? "Cached" : "Curious"}</Text>
            <Text style={styles.sideMetricLabel}>community mood</Text>
          </View>
        </View>
        <View style={styles.sideProgressTrack}>
          <View style={[styles.sideProgressFill, { width: `${Math.min(92, Math.max(34, posts.length * 9))}%` }]} />
        </View>
        <Text style={styles.sidePanelBody}>Signals are loading quietly so the feed stays fast.</Text>
      </LogiNexusPanel>
      <Pressable accessibilityRole="button" accessibilityLabel="Open trending signals" style={styles.sideCard} onPress={onOpenUndx}>
        <Text style={styles.sideCardKicker}>Trending Signals</Text>
        <View style={styles.sideHashRow}>
          <Text style={styles.sideHashIcon}>#</Text>
          <View>
            <Text style={styles.sideHashTitle}>{trendLabel}</Text>
            <Text style={styles.sideHashMeta}>Safety intelligence</Text>
          </View>
        </View>
      </Pressable>
      <Pressable accessibilityRole="button" accessibilityLabel="Open Safety Shield" style={[styles.sideCard, styles.sideSponsoredCard]} onPress={onOpenSafety}>
        <View style={styles.sideSponsoredHeader}>
          <Text style={styles.sideSponsoredPill}>Sponsored Signal</Text>
          <Text style={styles.sideClose}>×</Text>
        </View>
        <Text style={styles.sideSponsoredTitle}>Premium signal loading</Text>
        <Text style={styles.sidePanelBody}>Approved sponsor projections appear here through privacy-safe frequency caps and review gates.</Text>
        <View style={styles.sideDotsRow}>
          <View style={[styles.sideDot, { backgroundColor: colors.accent }]} />
          <View style={[styles.sideDot, { backgroundColor: colors.accentStrong }]} />
          <View style={[styles.sideDot, { backgroundColor: colors.warning }]} />
          <View style={[styles.sideDot, { backgroundColor: colors.intelligence }]} />
        </View>
      </Pressable>
      <LogiNexusPanel style={styles.sidePanel} tone="default">
        <Text style={styles.sidePanelTitle}>Realtime layer ready</Text>
        <Text style={styles.sidePanelBody}>{statuses.length || posts.length ? "New posts, reactions, status, and replies update here as they arrive." : "Home is waiting for authenticated feed and status events."}</Text>
      </LogiNexusPanel>
    </View>
  );
}

function isToday(value?: string) {
  if (!value) return false;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return false;
  const now = new Date();
  return date.getFullYear() === now.getFullYear() && date.getMonth() === now.getMonth() && date.getDate() === now.getDate();
}

function trendingLabel(posts: PulsePost[]) {
  const source = posts.find((post) => /scam|security|safety/i.test(`${post.title || ""} ${post.body || ""}`));
  if (source) return "#scamshield";
  const crypto = posts.find((post) => /crypto|market|coin|token/i.test(`${post.title || ""} ${post.body || ""}`));
  if (crypto) return "#marketpulse";
  return "#scamshield";
}

function formatHeroMetric(value: number) {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(value >= 10_000_000 ? 0 : 1)}M`;
  if (value >= 1000) return `${(value / 1000).toFixed(value >= 10_000 ? 0 : 1)}K`;
  return String(value);
}

function HeroTile({
  label,
  value,
  body,
  icon,
  tone,
  onPress
}: {
  label: string;
  value: string;
  body: string;
  icon: string;
  tone: "intelligence" | "creator" | "safety";
  onPress: () => void;
}) {
  const borderColor =
    tone === "intelligence"
      ? logiNexus.colors.home.borderIntelligence
      : tone === "safety"
        ? logiNexus.colors.home.borderSafety
        : logiNexus.colors.home.borderCreator;
  const accent =
    tone === "intelligence"
      ? logiNexus.colors.home.accentUndx
      : tone === "safety"
        ? logiNexus.colors.home.accentSafety
        : logiNexus.colors.home.accentRadio;
  return (
    <Pressable accessibilityRole="button" accessibilityLabel={`Open ${label}`} style={[styles.heroTile, { borderColor }]} onPress={onPress}>
      <View style={[styles.heroTileIcon, { borderColor, backgroundColor: `${accent}18` }]}>
        <Text style={[styles.heroTileIconText, { color: accent }]}>{icon}</Text>
      </View>
      <View style={styles.heroTileCopy}>
        <Text style={styles.heroTileLabel} numberOfLines={2}>{label}</Text>
        <Text style={[styles.heroTileValue, { color: accent }]} numberOfLines={1}>{value}</Text>
        <Text style={styles.heroTileBody} numberOfLines={1}>{body}</Text>
      </View>
      <Text style={styles.heroTileArrow}>→</Text>
    </Pressable>
  );
}

function HeroMetricBlock({
  value,
  label,
  tone,
  onPress
}: {
  value: string | number;
  label: string;
  tone: "default" | "danger" | "intelligence";
  onPress?: () => void;
}) {
  const accent = tone === "danger" ? colors.danger : tone === "intelligence" ? colors.intelligence : colors.accent;
  return (
    <Pressable
      accessibilityRole={onPress ? "button" : undefined}
      accessibilityLabel={`${label}: ${value}`}
      disabled={!onPress}
      style={[styles.heroMetricBlock, { borderColor: `${accent}55` }]}
      onPress={onPress}
    >
      <Text style={[styles.heroMetricBlockValue, { color: accent }]} numberOfLines={1}>{value}</Text>
      <Text style={styles.heroMetricBlockLabel} numberOfLines={2}>{label}</Text>
    </Pressable>
  );
}

function StatusRail({
  items,
  loading,
  offline,
  error,
  onAddStatus,
  onOpenStatus,
  onViewAll
}: {
  items: PulseStatus[];
  loading: boolean;
  offline: boolean;
  error: string;
  onAddStatus: () => void;
  onOpenStatus: (status: PulseStatus) => void;
  onViewAll: () => void;
}) {
  return (
    <View style={styles.statusSection}>
      <View style={styles.statusHeader}>
        <Text style={styles.statusHeaderKicker}>Status</Text>
        <Pressable accessibilityRole="button" accessibilityLabel="View all Statuses" testID="home-status-view-all" onPress={onViewAll}>
          <Text style={styles.statusHeaderAction}>View all →</Text>
        </Pressable>
      </View>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.statusRail}>
        <Pressable accessibilityRole="button" style={styles.addStatusCard} onPress={onAddStatus}>
          <Text style={styles.addStatusIcon}>+</Text>
          <Text style={styles.addStatusText}>Add Status</Text>
        </Pressable>
        {loading ? (
          <View style={styles.statusEmptyCard}>
            <ActivityIndicator color={colors.accent} />
            <Text style={styles.statusEmptyText}>Loading Status</Text>
          </View>
        ) : null}
        {!loading && !items.length ? Array.from({ length: 5 }).map((_, index) => <StatusPlaceholder key={`status-placeholder-${index}`} message={error || "No status yet"} />) : null}
        {items.map((status) => (
          <Pressable accessibilityRole="button" key={status.id} style={[styles.statusCard, !status.viewed && styles.statusCardUnseen]} onPress={() => onOpenStatus(status)}>
            <View style={styles.statusAvatar}>
              {statusPosterUrl(status) || status.author?.avatar_url || status.author_avatar_url ? (
                <Image source={{ uri: statusPosterUrl(status) || status.author?.avatar_url || status.author_avatar_url }} style={styles.statusAvatarImage} />
              ) : (
                <Text style={styles.statusAvatarText}>{(status.author?.display_name || status.author_name || "PS").slice(0, 2).toUpperCase()}</Text>
              )}
              <View style={styles.statusOnlineDot} />
            </View>
            <Text style={styles.statusName} numberOfLines={1}>{status.author?.display_name || status.author_name || "PulseSoc"}</Text>
            <Text style={styles.statusMeta}>{status.viewed ? "Seen" : "Unseen"}</Text>
          </Pressable>
        ))}
      </ScrollView>
      {offline ? <Text style={styles.statusOffline}>Status rail is using cached metadata.</Text> : null}
    </View>
  );
}

function StatusPlaceholder({ message }: { message: string }) {
  return (
    <View style={styles.statusPlaceholderCard}>
      <View style={styles.statusPlaceholderOrb}>
        <View style={styles.statusPlaceholderInner} />
      </View>
      <Text style={styles.statusPlaceholderText} numberOfLines={2}>{message}</Text>
    </View>
  );
}

function mergePosts(current: PulsePost[], incoming: PulsePost[]) {
  const seen = new Set(current.map((post) => post.id));
  return [...current, ...incoming.filter((post) => !seen.has(post.id))];
}

const styles = createThemedStyles(() => ({
  actionErrorBanner: {
    alignItems: "center",
    backgroundColor: "rgba(74, 24, 24, 0.92)",
    borderColor: colors.danger,
    borderRadius: logiNexus.radius.medium,
    borderWidth: 1,
    flexDirection: "row",
    gap: 10,
    justifyContent: "space-between",
    marginHorizontal: 12,
    marginTop: 6,
    paddingHorizontal: 12,
    paddingVertical: 9
  },
  actionErrorDismiss: {
    color: colors.accent,
    fontSize: 11,
    fontWeight: "800",
    textTransform: "uppercase"
  },
  actionErrorText: {
    color: colors.text,
    flexShrink: 1,
    fontSize: 12,
    fontWeight: "700"
  },
  newSignalsPill: {
    alignItems: "center",
    alignSelf: "center",
    backgroundColor: colors.glassStrong,
    borderColor: colors.border,
    borderRadius: logiNexus.radius.large,
    borderWidth: 1,
    marginBottom: 8,
    marginTop: 2,
    paddingHorizontal: 16,
    paddingVertical: 8
  },
  newSignalsText: {
    color: colors.accent,
    fontSize: 12,
    fontWeight: "800",
    letterSpacing: 0.4
  },
  spatialPageScroll: {
    flex: 1
  },
  addStatusCard: {
    alignItems: "center",
    backgroundColor: "transparent",
    borderColor: "transparent",
    borderRadius: logiNexus.radius.large,
    borderWidth: 0,
    justifyContent: "center",
    minHeight: 78,
    padding: 3,
    width: 64
  },
  addStatusIcon: {
    color: colors.accent,
    backgroundColor: "rgba(11, 22, 51, 0.03)",
    borderColor: logiNexus.colors.home.borderActive,
    borderRadius: 24,
    borderWidth: 1,
    fontSize: 25,
    fontWeight: "900",
    height: 50,
    lineHeight: 47,
    overflow: "hidden",
    textAlign: "center",
    width: 50
  },
  addStatusText: {
    color: colors.text,
    fontSize: 10,
    fontWeight: "900",
    marginTop: 3,
    textAlign: "center"
  },
  center: {
    alignItems: "center",
    backgroundColor: "transparent",
    flex: 1,
    justifyContent: "center",
    padding: 24
  },
  centerText: {
    color: colors.muted,
    marginTop: 12
  },
  content: {
    alignSelf: "center",
    maxWidth: 1480,
    padding: 12,
    width: "100%"
  },
  commandIdentityCard: {
    backgroundColor: "rgba(7, 22, 35, 0.03)",
    borderColor: "rgba(50, 230, 179, 0.26)",
    padding: 10
  },
  commandIdentityCopy: {
    flex: 1,
    minWidth: 0
  },
  commandIdentityIcon: {
    backgroundColor: "rgba(121, 210, 255, 0.14)",
    borderColor: "rgba(50, 230, 179, 0.42)",
    borderRadius: 12,
    borderWidth: 1,
    color: colors.accentStrong,
    fontSize: 12,
    fontWeight: "900",
    height: 34,
    lineHeight: 32,
    overflow: "hidden",
    textAlign: "center",
    width: 34
  },
  commandIdentityMeta: {
    color: colors.muted,
    fontSize: 11,
    fontWeight: "800",
    marginTop: 2
  },
  commandIdentityRow: {
    alignItems: "center",
    flexDirection: "row",
    gap: 10
  },
  commandIdentityTitle: {
    color: colors.text,
    fontSize: 14,
    fontWeight: "900"
  },
  commandNavGroup: {
    backgroundColor: "rgba(4, 16, 27, 0.03)",
    borderColor: "rgba(121, 210, 255, 0.12)",
    borderRadius: 18,
    borderWidth: 1,
    gap: 5,
    padding: 7
  },
  commandNavIcon: {
    backgroundColor: "rgba(121, 210, 255, 0.1)",
    borderRadius: 10,
    color: colors.accentStrong,
    fontSize: 12,
    fontWeight: "900",
    height: 26,
    lineHeight: 25,
    overflow: "hidden",
    textAlign: "center",
    width: 26
  },
  commandNavItem: {
    alignItems: "center",
    borderColor: "transparent",
    borderRadius: 12,
    borderWidth: 1,
    flexDirection: "row",
    gap: 10,
    minHeight: 38,
    paddingHorizontal: 8
  },
  commandNavItemActive: {
    backgroundColor: "rgba(50, 230, 179, 0.12)",
    borderColor: "rgba(50, 230, 179, 0.2)"
  },
  commandNavLabel: {
    color: colors.text,
    flex: 1,
    fontSize: 13,
    fontWeight: "900"
  },
  commandRadioActions: {
    flexDirection: "row",
    gap: 7,
    marginTop: 8
  },
  commandRadioBody: {
    color: colors.muted,
    fontSize: 10,
    fontWeight: "700",
    lineHeight: 15,
    marginTop: 9
  },
  commandRadioCopy: {
    flex: 1,
    minWidth: 0
  },
  commandRadioDot: {
    backgroundColor: colors.accent,
    borderRadius: 5,
    height: 10,
    width: 10
  },
  commandRadioNow: {
    alignItems: "center",
    backgroundColor: "rgba(10, 18, 43, 0.03)",
    borderColor: logiNexus.colors.home.borderActive,
    borderRadius: 14,
    borderWidth: 1,
    flexDirection: "row",
    gap: 8,
    marginTop: 9,
    padding: 9
  },
  commandRadioNowMeta: {
    color: colors.muted,
    fontSize: 10,
    fontWeight: "800"
  },
  commandRadioNowTitle: {
    color: colors.text,
    fontSize: 13,
    fontWeight: "900"
  },
  commandRadioPanel: {
    backgroundColor: "rgba(6, 20, 32, 0.03)",
    padding: 11
  },
  commandRadioPrimary: {
    alignItems: "center",
    backgroundColor: colors.accent,
    borderRadius: 12,
    flex: 1,
    minHeight: 32,
    justifyContent: "center"
  },
  commandRadioPrimaryText: {
    color: colors.background,
    fontSize: 11,
    fontWeight: "900"
  },
  commandRadioSecondary: {
    alignItems: "center",
    backgroundColor: "rgba(255,255,255,0.055)",
    borderColor: "rgba(121, 210, 255, 0.16)",
    borderRadius: 12,
    borderWidth: 1,
    flex: 1,
    minHeight: 32,
    justifyContent: "center"
  },
  commandRadioSecondaryText: {
    color: colors.text,
    fontSize: 11,
    fontWeight: "900"
  },
  commandRadioTab: {
    backgroundColor: "rgba(255,255,255,0.055)",
    borderRadius: 10,
    color: colors.muted,
    flex: 1,
    fontSize: 10,
    fontWeight: "900",
    overflow: "hidden",
    paddingVertical: 7,
    textAlign: "center"
  },
  commandRadioTabActive: {
    backgroundColor: colors.accent,
    color: colors.background
  },
  commandRadioTabs: {
    flexDirection: "row",
    gap: 5,
    marginTop: 8
  },
  commandRadioTitle: {
    color: colors.text,
    fontSize: 13,
    fontWeight: "900"
  },
  commandRail: {
    gap: 9,
    width: 226
  },
  commandSectionTitle: {
    color: colors.muted,
    fontSize: 10,
    fontWeight: "900",
    letterSpacing: 1.2,
    textTransform: "uppercase"
  },
  commandShortcutCard: {
    backgroundColor: "rgba(255,255,255,0.045)",
    borderColor: "rgba(121, 210, 255, 0.16)",
    borderRadius: 14,
    borderWidth: 1,
    padding: 10
  },
  commandShortcutGroup: {
    gap: 7
  },
  commandShortcutMeta: {
    color: colors.muted,
    fontSize: 11,
    fontWeight: "800",
    marginTop: 2
  },
  commandShortcutTitle: {
    color: colors.text,
    fontSize: 15,
    fontWeight: "900"
  },
  empty: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    padding: 18
  },
  emptyText: {
    color: colors.muted,
    fontSize: 14,
    lineHeight: 21,
    marginTop: 6
  },
  emptyTitle: {
    color: colors.text,
    fontSize: 18,
    fontWeight: "900"
  },
  feedTab: {
    borderBottomColor: "transparent",
    borderBottomWidth: 3,
    minHeight: 42,
    justifyContent: "center",
    paddingHorizontal: 13
  },
  feedTabActive: {
    borderBottomColor: colors.accent
  },
  feedTabText: {
    color: colors.muted,
    fontSize: 15,
    fontWeight: "900"
  },
  feedTabTextActive: {
    color: colors.accent
  },
  feedTabs: {
    gap: 18,
    paddingVertical: 4
  },
  drawerClose: {
    alignItems: "center",
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    height: 40,
    justifyContent: "center",
    width: 40
  },
  drawerCloseText: {
    color: colors.text,
    fontSize: 24,
    fontWeight: "900"
  },
  drawerHeader: {
    alignItems: "center",
    borderBottomColor: colors.border,
    borderBottomWidth: 1,
    flexDirection: "row",
    justifyContent: "space-between",
    marginBottom: 12,
    paddingBottom: 12
  },
  drawerItem: {
    alignItems: "center",
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    flexDirection: "row",
    justifyContent: "space-between",
    marginTop: 8,
    minHeight: 44,
    paddingHorizontal: 12
  },
  drawerItemText: {
    color: colors.text,
    flex: 1,
    fontWeight: "900"
  },
  drawerKicker: {
    color: colors.accent,
    fontSize: 11,
    fontWeight: "900",
    letterSpacing: 1.2
  },
  drawerOverlay: {
    backgroundColor: "rgba(1, 6, 14, 0.68)",
    flex: 1,
    flexDirection: "row"
  },
  drawerPanel: {
    backgroundColor: colors.background,
    borderRightColor: colors.border,
    borderRightWidth: 1,
    maxWidth: 380,
    padding: 16,
    width: "86%"
  },
  drawerScrim: {
    ...StyleSheet.absoluteFillObject
  },
  drawerSection: {
    marginBottom: 16
  },
  drawerSectionTitle: {
    color: colors.muted,
    fontSize: 12,
    fontWeight: "900",
    letterSpacing: 1.4,
    textTransform: "uppercase"
  },
  drawerStatus: {
    color: colors.accent,
    fontSize: 10,
    fontWeight: "900",
    marginLeft: 10,
    textTransform: "uppercase"
  },
  drawerStatusFallback: {
    color: colors.warning
  },
  drawerStatusGated: {
    color: colors.danger
  },
  drawerSubtitle: {
    color: colors.muted,
    fontSize: 12,
    lineHeight: 17,
    marginTop: 3
  },
  drawerTitle: {
    color: colors.text,
    fontSize: 24,
    fontWeight: "900"
  },
  feedTabsSubtitle: {
    color: colors.muted,
    fontSize: 13,
    marginTop: 4
  },
  feedTabsTitle: {
    color: colors.text,
    fontSize: 18,
    fontWeight: "900"
  },
  feedTabsWrap: {
    backgroundColor: "transparent",
    // The rule under the feed tabs, in the same muted indigo as the post
    // separators below it. Kept at its own lighter alpha rather than switched to
    // `borderSubtle` wholesale: this line runs directly beneath the teal active
    // underline and reads heavier than a post separator at the same value.
    borderBottomColor: "rgba(138, 152, 232, 0.13)",
    borderBottomWidth: 1,
    marginBottom: 18,
    marginTop: 13,
    paddingHorizontal: 2,
    paddingVertical: 0
  },
  footer: {
    padding: 18
  },
  header: {
    marginBottom: 2
  },
  homeCanvas: {
    width: "100%"
  },
  homeCanvasWide: {
    alignItems: "flex-start",
    flexDirection: "row",
    gap: 16
  },
  homePrimaryColumn: {
    flex: 1,
    minWidth: 0
  },
  hero: {
    backgroundColor: "rgba(5, 15, 29, 0.03)",
    borderColor: "rgba(50, 230, 179, 0.32)",
    borderRadius: 28,
    marginBottom: 22,
    minHeight: 314,
    overflow: "hidden",
    padding: 13,
    shadowColor: colors.accentStrong,
    shadowOpacity: 0.18,
    shadowRadius: 24
  },
  heroCompact: {
    minHeight: 190
  },
  heroAtmosphere: {
    ...StyleSheet.absoluteFillObject,
    pointerEvents: "none"
  },
  heroPlanet: {
    backgroundColor: "rgba(24, 82, 118, 0.54)",
    borderColor: "rgba(91, 221, 255, 0.28)",
    borderRadius: 150,
    borderWidth: 1,
    height: 286,
    overflow: "hidden",
    position: "absolute",
    right: -86,
    top: 108,
    transform: [{ rotate: "-8deg" }],
    width: 286
  },
  heroPlanetLight: {
    backgroundColor: "rgba(45, 229, 183, 0.16)",
    borderRadius: 145,
    height: 290,
    left: -52,
    position: "absolute",
    top: -48,
    width: 290
  },
  heroPlanetShadow: {
    backgroundColor: "rgba(5, 10, 28, 0.03)",
    borderRadius: 150,
    bottom: -54,
    height: 300,
    position: "absolute",
    right: -70,
    width: 300
  },
  heroSkyline: {
    alignItems: "flex-end",
    bottom: 28,
    flexDirection: "row",
    gap: 4,
    opacity: 0.54,
    position: "absolute",
    right: 18
  },
  heroSkylineTower: {
    backgroundColor: "rgba(7, 15, 32, 0.03)",
    borderTopColor: "rgba(159, 124, 255, 0.46)",
    borderTopWidth: 1,
    justifyContent: "flex-start",
    width: 8
  },
  heroSkylineLight: {
    alignSelf: "center",
    backgroundColor: "rgba(50, 230, 179, 0.74)",
    height: 2,
    marginTop: 7,
    width: 2
  },
  heroGlow: {
    borderRadius: 180,
    opacity: 0.15,
    position: "absolute"
  },
  heroGlowPrimary: {
    backgroundColor: colors.accentStrong,
    height: 320,
    right: -112,
    top: -78,
    width: 320
  },
  heroGlowSecondary: {
    backgroundColor: colors.intelligence,
    bottom: -110,
    height: 280,
    left: -100,
    width: 280
  },
  heroTopLine: {
    alignItems: "center",
    flexDirection: "row",
    gap: 10,
    justifyContent: "space-between",
    marginBottom: 12,
    zIndex: 2
  },
  heroHealthPill: {
    alignItems: "center",
    backgroundColor: "rgba(10, 18, 43, 0.03)",
    borderColor: logiNexus.colors.home.borderSubtle,
    borderRadius: logiNexus.radius.capsule,
    borderWidth: 1,
    flexDirection: "row",
    gap: 8,
    minHeight: 32,
    paddingHorizontal: 13
  },
  heroHealthDot: {
    backgroundColor: colors.accent,
    borderRadius: 3,
    height: 6,
    shadowColor: colors.accent,
    shadowOpacity: 0.45,
    shadowRadius: 8,
    width: 6
  },
  heroHealthText: {
    color: colors.accent,
    fontSize: 13,
    fontWeight: "900"
  },
  heroHeadline: {
    color: colors.text,
    fontSize: 19,
    fontWeight: "900",
    lineHeight: 23,
    marginTop: 7,
    maxWidth: 250,
    zIndex: 2
  },
  heroMetricsRow: {
    flexDirection: "row",
    gap: 6,
    marginTop: 8,
    zIndex: 2
  },
  heroMetricCell: {
    backgroundColor: "rgba(10, 18, 43, 0.03)",
    borderRadius: 14,
    borderWidth: 1,
    flex: 1,
    justifyContent: "center",
    minHeight: 44,
    paddingHorizontal: 6,
    paddingVertical: 5
  },
  heroMetricValue: {
    fontSize: 16,
    fontWeight: "900",
    lineHeight: 20
  },
  heroMetricCellLabel: {
    color: colors.muted,
    fontSize: 8,
    fontWeight: "800",
    lineHeight: 10,
    marginTop: 3
  },
  heroQuickRow: {
    flexDirection: "row",
    gap: 10,
    marginTop: 16,
    zIndex: 2
  },
  heroRadioCopy: {
    flex: 1,
    minWidth: 0
  },
  heroRadioIcon: {
    color: colors.background,
    fontSize: 17,
    fontWeight: "900",
    lineHeight: 42,
    textAlign: "center"
  },
  heroRadioOrb: {
    alignItems: "center",
    backgroundColor: colors.accent,
    borderColor: "rgba(141, 247, 255, 0.82)",
    borderRadius: 21,
    borderWidth: 1,
    height: 42,
    justifyContent: "center",
    shadowColor: colors.accent,
    shadowOpacity: 0.32,
    shadowRadius: 14,
    width: 42
  },
  heroRadioOrbPlaying: {
    backgroundColor: colors.creator,
    borderColor: colors.focus
  },
  heroRadioLabel: {
    color: colors.text,
    fontSize: 10,
    fontWeight: "900",
    letterSpacing: 1.8,
    textTransform: "uppercase"
  },
  heroRadioMeta: {
    color: colors.accent,
    fontSize: 12,
    fontWeight: "800",
    marginTop: 3
  },
  heroRadioMetaError: {
    color: colors.danger
  },
  heroRadioPill: {
    alignItems: "center",
    backgroundColor: "rgba(18, 26, 61, 0.03)",
    borderColor: logiNexus.colors.home.borderSubtle,
    borderRadius: 26,
    borderWidth: 1,
    flexDirection: "row",
    flexShrink: 0,
    flexWrap: "wrap",
    gap: 8,
    minHeight: 82,
    minWidth: 0,
    paddingHorizontal: 11,
    paddingVertical: 11,
    width: 156
  },
  heroRadioPillPlaying: {
    backgroundColor: "rgba(24, 66, 74, 0.9)",
    borderColor: colors.accent
  },
  heroRadioWave: {
    alignItems: "center",
    flexBasis: "100%",
    flexDirection: "row",
    gap: 3,
    height: 17,
    justifyContent: "flex-end",
    paddingRight: 3
  },
  heroRadioWaveBar: {
    backgroundColor: "rgba(121, 210, 255, 0.42)",
    borderRadius: 2,
    width: 3
  },
  heroRadioWaveBarPlaying: {
    backgroundColor: colors.accent
  },
  heroAction: {
    alignItems: "center",
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    flex: 1,
    justifyContent: "center",
    minHeight: 42
  },
  heroActionText: {
    color: colors.text,
    fontWeight: "900"
  },
  heroActions: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 10,
    marginTop: 12
  },
  heroBlueprintRow: {
    flexDirection: "row",
    gap: 12,
    marginTop: 30,
    minHeight: 88,
    zIndex: 2
  },
  heroCompactMetricRow: {
    flexDirection: "row",
    gap: 6,
    marginTop: 16,
    zIndex: 2
  },
  heroMoodCopy: {
    flex: 1,
    minWidth: 0
  },
  heroMoodRow: {
    alignItems: "flex-start",
    flexDirection: "row",
    gap: 8,
    justifyContent: "space-between",
    marginTop: 2,
    minHeight: 92,
    zIndex: 2
  },
  heroMoodSummary: {
    color: colors.muted,
    fontSize: 14,
    fontWeight: "800",
    lineHeight: 20,
    marginTop: 9,
    maxWidth: 174
  },
  heroMoodTitle: {
    color: colors.text,
    fontSize: 38,
    fontWeight: "900",
    lineHeight: 44
  },
  heroKicker: {
    alignSelf: "flex-start",
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    color: colors.accent,
    fontSize: 12,
    fontWeight: "900",
    letterSpacing: 1.4,
    paddingHorizontal: 12,
    paddingVertical: 7
  },
  heroMain: {
    flex: 1
  },
  heroDivider: {
    backgroundColor: logiNexus.colors.home.borderSubtle,
    height: 1,
    marginVertical: 12,
    width: "74%"
  },
  heroLiveLabel: {
    color: colors.text,
    fontSize: 11,
    fontWeight: "800",
    lineHeight: 15,
    maxWidth: 112
  },
  heroLiveMetric: {
    color: colors.text,
    fontSize: 21,
    fontWeight: "900",
    lineHeight: 25,
    maxWidth: 112
  },
  heroMetric: {
    color: colors.text,
    fontSize: 31,
    fontWeight: "900",
    lineHeight: 36,
    marginTop: 12,
    maxWidth: 112
  },
  heroMetricLabel: {
    color: colors.text,
    fontSize: 14,
    fontWeight: "800",
    lineHeight: 19,
    maxWidth: 118
  },
  heroMetricBlock: {
    backgroundColor: "rgba(10, 18, 43, 0.03)",
    borderRadius: 18,
    borderWidth: 1,
    justifyContent: "center",
    flex: 1,
    minHeight: 54,
    paddingHorizontal: 11,
    paddingVertical: 8
  },
  heroMetricBlockLabel: {
    color: colors.muted,
    fontSize: 12,
    fontWeight: "800",
    lineHeight: 15,
    marginTop: 3
  },
  heroMetricBlockValue: {
    fontSize: 23,
    fontWeight: "900",
    lineHeight: 27
  },
  heroMetricStack: {
    gap: 8,
    width: 104,
    zIndex: 2
  },
  heroMapCaption: {
    bottom: 5,
    color: colors.muted,
    fontSize: 7,
    fontWeight: "800",
    left: 8,
    lineHeight: 9,
    maxWidth: 134,
    position: "absolute",
    zIndex: 3
  },
  heroMapKicker: {
    color: colors.accent,
    fontSize: 7,
    fontWeight: "900",
    left: 8,
    letterSpacing: 0.7,
    position: "absolute",
    textTransform: "uppercase",
    top: 6,
    zIndex: 3
  },
  heroMapPanel: {
    backgroundColor: "rgba(7, 18, 33, 0.5)",
    borderColor: "rgba(97, 216, 255, 0.16)",
    borderRadius: 22,
    borderWidth: 1,
    flex: 1,
    minWidth: 0,
    overflow: "hidden",
    position: "relative"
  },
  heroMapSignalLineOne: {
    backgroundColor: "rgba(121, 210, 255, 0.2)",
    height: 1,
    left: 16,
    position: "absolute",
    top: 47,
    transform: [{ rotate: "-18deg" }],
    width: 178
  },
  heroMapSignalLineThree: {
    backgroundColor: "rgba(159, 124, 255, 0.18)",
    height: 1,
    left: 42,
    position: "absolute",
    top: 70,
    transform: [{ rotate: "10deg" }],
    width: 164
  },
  heroMapSignalLineTwo: {
    backgroundColor: "rgba(50, 230, 179, 0.16)",
    height: 1,
    left: 4,
    position: "absolute",
    top: 74,
    transform: [{ rotate: "24deg" }],
    width: 154
  },
  heroContentRow: {
    alignItems: "stretch",
    flexDirection: "row",
    gap: 12,
    justifyContent: "space-between"
  },
  heroContentRowCompact: {
    flexDirection: "row",
    gap: 9
  },
  heroNetworkCluster: {
    flex: 1,
    minHeight: 210,
    minWidth: 0,
    overflow: "hidden",
    position: "relative"
  },
  heroNetworkClusterCompact: {
    flex: 0,
    minHeight: 214,
    width: 116
  },
  heroOrb: {
    alignItems: "center",
    alignSelf: "auto",
    backgroundColor: "rgba(97, 216, 255, 0.045)",
    borderColor: "rgba(97, 216, 255, 0.44)",
    borderRadius: 74,
    borderWidth: 1,
    height: 138,
    justifyContent: "center",
    overflow: "hidden",
    opacity: 0.72,
    position: "absolute",
    right: -8,
    top: 54,
    width: 138
  },
  heroOrbCompact: {
    height: 124,
    opacity: 0.24,
    right: -16,
    top: 56,
    width: 124,
    zIndex: 0
  },
  heroOrbInMap: {
    height: 88,
    opacity: 0.48,
    right: -10,
    top: 8,
    width: 88
  },
  heroNode: {
    backgroundColor: colors.accent,
    borderRadius: 5,
    height: 10,
    opacity: 0.78,
    position: "absolute",
    width: 10
  },
  heroNodeBig: {
    backgroundColor: "rgba(97, 216, 255, 0.12)",
    borderColor: colors.accentStrong,
    borderRadius: 42,
    borderWidth: 1,
    height: 84,
    position: "absolute",
    width: 84
  },
  heroNodeOne: {
    left: 26,
    top: 30
  },
  heroNodeThree: {
    bottom: 38,
    left: 42
  },
  heroNodeTwo: {
    right: 34,
    top: 44
  },
  heroNodeFour: {
    bottom: 44,
    right: 42
  },
  heroOrbText: {
    color: colors.accentStrong,
    fontSize: 21,
    fontWeight: "900"
  },
  heroRingInner: {
    borderColor: "rgba(50, 230, 179, 0.28)",
    borderRadius: 43,
    borderWidth: 1,
    height: 86,
    position: "absolute",
    width: 86
  },
  heroRingOuter: {
    borderColor: "rgba(159, 124, 255, 0.28)",
    borderRadius: 60,
    borderWidth: 1,
    height: 120,
    position: "absolute",
    width: 120
  },
  heroSubtitle: {
    color: colors.muted,
    fontSize: 10,
    fontWeight: "800",
    lineHeight: 13,
    marginTop: 4,
    maxWidth: 250,
    zIndex: 2
  },
  heroTitle: {
    color: colors.text,
    ...logiNexus.typography.display,
    marginTop: 10
  },
  heroTopRow: {
    alignItems: "center",
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8,
    justifyContent: "space-between"
  },
  heroTile: {
    alignItems: "center",
    backgroundColor: "rgba(18, 26, 61, 0.03)",
    borderRadius: 22,
    borderWidth: 1,
    flex: 1,
    flexDirection: "column",
    gap: 7,
    justifyContent: "center",
    minHeight: 78,
    minWidth: 0,
    paddingHorizontal: 8,
    paddingVertical: 9,
    zIndex: 2
  },
  heroTileArrow: {
    color: colors.muted,
    display: "none",
    fontSize: 9,
    fontWeight: "900",
    opacity: 0.55
  },
  heroTileBody: {
    color: colors.muted,
    fontSize: 10,
    fontWeight: "700",
    marginTop: 1,
    display: "none",
    textAlign: "center"
  },
  heroTileCopy: {
    alignItems: "center",
    flex: 1,
    justifyContent: "center",
    minWidth: 0
  },
  heroTileColumn: {
    flex: 0.86,
    gap: 8,
    justifyContent: "center",
    minWidth: 150
  },
  heroTileColumnCompact: {
    flex: 1,
    minWidth: 0,
    zIndex: 2
  },
  heroTileIcon: {
    alignItems: "center",
    borderRadius: 16,
    borderWidth: 1,
    height: 34,
    justifyContent: "center",
    width: 34
  },
  heroTileIconText: {
    fontSize: 18,
    fontWeight: "900"
  },
  heroTileLabel: {
    color: colors.text,
    fontSize: 9,
    fontWeight: "900",
    letterSpacing: 0.9,
    textAlign: "center",
    textTransform: "uppercase"
  },
  heroTileValue: {
    fontSize: 14,
    fontWeight: "900",
    marginTop: 1,
    textAlign: "center"
  },
  list: {
    backgroundColor: "transparent",
    flex: 1
  },
  metric: {
    alignItems: "center",
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    flex: 1,
    minHeight: 64,
    justifyContent: "center"
  },
  metricLabel: {
    color: colors.muted,
    fontSize: 12,
    marginTop: 2
  },
  metricRow: {
    flexDirection: "row",
    gap: 10,
    marginTop: 12
  },
  metricValue: {
    color: colors.text,
    fontSize: 24,
    fontWeight: "900"
  },
  offlinePill: {
    color: colors.warning,
    fontSize: 12,
    fontWeight: "900",
    marginTop: 8
  },
  radioButton: {
    alignItems: "center",
    borderColor: colors.border,
    borderRadius: 999,
    borderWidth: 1,
    flexDirection: "row",
    gap: 8,
    paddingHorizontal: 14,
    paddingVertical: 9
  },
  radioPlay: {
    color: colors.background,
    backgroundColor: colors.accent,
    borderRadius: 16,
    fontSize: 12,
    height: 32,
    lineHeight: 32,
    overflow: "hidden",
    textAlign: "center",
    width: 32
  },
  radioText: {
    color: colors.text,
    fontWeight: "900"
  },
  /**
   * Transparent, so the app-root `PulseBackground` is what the feed sits on.
   *
   * This used to paint `home.backgroundDeepSpace` (`#030712`) — a flat, fully
   * opaque near-black. When the screen's own `GalacticAtmosphere` backdrop was
   * removed in favour of the single shared layer, the fill it had been drawn
   * over was left behind, and an opaque fill is indistinguishable from a
   * missing background: `PulseBackground` was mounting, animating and
   * rendering perfectly, one view underneath this one.
   *
   * That made Home the one screen in the app with no ambient environment,
   * which is the opposite of what it should be — it is the most-visited
   * surface in the product. Nothing else here paints: `list` and `content` are
   * already transparent, the navigator's `sceneContainerStyle` and
   * `contentStyle` are transparent, and `PostCard` has no fill at all, so this
   * single declaration is what the whole feed's appearance turns on. It is
   * pinned in `navigation/__tests__/backgroundSurfaces.test.ts` for that
   * reason.
   */
  root: {
    backgroundColor: "transparent",
    flex: 1
  },
  sideCard: {
    backgroundColor: logiNexus.colors.home.surfaceGlassStrong,
    borderColor: logiNexus.colors.home.borderSubtle,
    borderRadius: 16,
    borderWidth: 1,
    padding: 12
  },
  sideCardKicker: {
    color: colors.muted,
    fontSize: 10,
    fontWeight: "900",
    marginBottom: 8
  },
  sideClose: {
    color: colors.text,
    fontSize: 18,
    fontWeight: "900",
    opacity: 0.7
  },
  sideDot: {
    borderRadius: 4,
    height: 8,
    width: 8
  },
  sideDotsRow: {
    backgroundColor: "rgba(255,255,255,0.035)",
    borderColor: logiNexus.colors.home.borderSubtle,
    borderRadius: 12,
    borderWidth: 1,
    flexDirection: "row",
    gap: 22,
    justifyContent: "center",
    marginTop: 12,
    padding: 14
  },
  sideHashIcon: {
    backgroundColor: "rgba(121, 210, 255, 0.11)",
    borderColor: logiNexus.colors.home.borderSubtle,
    borderRadius: 16,
    borderWidth: 1,
    color: colors.text,
    fontSize: 16,
    fontWeight: "900",
    height: 32,
    lineHeight: 30,
    overflow: "hidden",
    textAlign: "center",
    width: 32
  },
  sideHashMeta: {
    color: colors.muted,
    fontSize: 10,
    fontWeight: "800",
    marginTop: 2
  },
  sideHashRow: {
    alignItems: "center",
    backgroundColor: "rgba(10, 18, 43, 0.03)",
    borderRadius: 12,
    flexDirection: "row",
    gap: 10,
    padding: 10
  },
  sideHashTitle: {
    color: colors.text,
    fontSize: 12,
    fontWeight: "900"
  },
  sideMetricBox: {
    backgroundColor: "rgba(10, 18, 43, 0.03)",
    borderColor: logiNexus.colors.home.borderSubtle,
    borderRadius: 12,
    borderWidth: 1,
    flex: 1,
    minHeight: 58,
    padding: 9
  },
  sideMetricGrid: {
    flexDirection: "row",
    gap: 8,
    marginTop: 9
  },
  sideMetricLabel: {
    color: colors.muted,
    fontSize: 10,
    fontWeight: "800",
    marginTop: 3
  },
  sideMetricValue: {
    color: colors.text,
    fontSize: 18,
    fontWeight: "900"
  },
  sidePanel: {
    backgroundColor: logiNexus.colors.home.surfaceGlass,
    padding: 13
  },
  sidePanelBody: {
    color: colors.muted,
    fontSize: 11,
    fontWeight: "700",
    lineHeight: 17,
    marginTop: 9
  },
  sidePanelTitle: {
    color: colors.text,
    fontSize: 13,
    fontWeight: "900"
  },
  sideProgressFill: {
    backgroundColor: colors.accent,
    borderRadius: 999,
    height: "100%"
  },
  sideProgressTrack: {
    backgroundColor: "rgba(255,255,255,0.08)",
    borderRadius: 999,
    height: 8,
    marginTop: 14,
    overflow: "hidden"
  },
  sideRail: {
    gap: 11,
    width: 314
  },
  sideSponsoredCard: {
    borderColor: "rgba(159, 124, 255, 0.28)"
  },
  sideSponsoredHeader: {
    alignItems: "center",
    flexDirection: "row",
    justifyContent: "space-between"
  },
  sideSponsoredPill: {
    alignSelf: "flex-start",
    backgroundColor: "rgba(50, 230, 179, 0.16)",
    borderColor: logiNexus.colors.home.borderActive,
    borderRadius: 999,
    borderWidth: 1,
    color: colors.accent,
    fontSize: 9,
    fontWeight: "900",
    paddingHorizontal: 8,
    paddingVertical: 5,
    textTransform: "uppercase"
  },
  sideSponsoredTitle: {
    color: colors.text,
    fontSize: 16,
    fontWeight: "900",
    marginTop: 10
  },
  statusAvatar: {
    alignItems: "center",
    backgroundColor: "rgba(37, 208, 167, 0.12)",
    borderColor: colors.accent,
    borderRadius: 34,
    borderWidth: 2,
    height: 68,
    justifyContent: "center",
    width: 68
  },
  statusAvatarImage: {
    borderRadius: 31,
    height: 62,
    width: 62
  },
  statusAvatarText: {
    color: colors.accent,
    fontWeight: "900"
  },
  statusCard: {
    alignItems: "center",
    backgroundColor: "transparent",
    borderColor: "transparent",
    borderRadius: logiNexus.radius.large,
    borderWidth: 1,
    gap: 7,
    minHeight: 108,
    padding: 4,
    width: 82
  },
  statusCardUnseen: {
    backgroundColor: "rgba(50, 230, 179, 0.065)",
    borderColor: logiNexus.colors.home.borderActive
  },
  statusEmptyCard: {
    alignItems: "center",
    backgroundColor: "rgba(255,255,255,0.035)",
    borderColor: colors.border,
    borderRadius: 8,
    borderStyle: "dashed",
    borderWidth: 1,
    justifyContent: "center",
    minHeight: 62,
    padding: 8,
    width: 122
  },
  statusPlaceholderCard: {
    alignItems: "center",
    gap: 9,
    minHeight: 108,
    paddingTop: 3,
    width: 82
  },
  statusPlaceholderInner: {
    backgroundColor: "rgba(255,255,255,0.025)",
    borderColor: "rgba(255,255,255,0.07)",
    borderRadius: 30,
    borderStyle: "dashed",
    borderWidth: 1,
    height: 60,
    width: 60
  },
  statusPlaceholderOrb: {
    alignItems: "center",
    backgroundColor: "rgba(255,255,255,0.04)",
    borderColor: "rgba(255,255,255,0.1)",
    borderRadius: 36,
    borderStyle: "dashed",
    borderWidth: 1,
    height: 72,
    justifyContent: "center",
    width: 72
  },
  statusPlaceholderText: {
    color: colors.muted,
    fontSize: 12,
    fontWeight: "800",
    lineHeight: 16,
    textAlign: "center"
  },
  statusEmptyText: {
    color: colors.muted,
    fontSize: 11,
    marginTop: 2,
    textAlign: "center"
  },
  statusEmptyTitle: {
    color: colors.text,
    fontSize: 13,
    fontWeight: "900",
    textAlign: "center"
  },
  statusMeta: {
    color: colors.muted,
    fontSize: 12,
    fontWeight: "800"
  },
  statusName: {
    color: colors.text,
    fontSize: 12,
    fontWeight: "900",
    textAlign: "center"
  },
  statusHeader: {
    alignItems: "center",
    flexDirection: "row",
    justifyContent: "space-between",
    marginBottom: 10
  },
  statusHeaderAction: {
    color: colors.accentStrong,
    fontSize: 14,
    fontWeight: "900",
    textTransform: "uppercase"
  },
  statusHeaderKicker: {
    color: colors.accent,
    ...logiNexus.typography.home.sectionLabel,
    fontSize: 18,
    letterSpacing: 4,
    textTransform: "uppercase"
  },
  statusOffline: {
    color: colors.warning,
    fontSize: 12,
    marginTop: 8
  },
  statusRail: {
    gap: 13
  },
  statusSection: {
    marginBottom: 20
  },
  heroSignalLine: {
    backgroundColor: "rgba(121, 210, 255, 0.18)",
    height: 1,
    position: "absolute",
    transform: [{ rotate: "-18deg" }],
    width: 250
  },
  heroSignalLineOne: {
    left: 112,
    top: 130
  },
  heroSignalLineTwo: {
    left: 66,
    top: 202,
    transform: [{ rotate: "22deg" }]
  },
  heroSignalLineThree: {
    right: 104,
    top: 166,
    transform: [{ rotate: "8deg" }]
  },
  heroNetworkLayer: {
    ...StyleSheet.absoluteFillObject
  },
  heroNetworkNode: {
    backgroundColor: colors.accentStrong,
    borderRadius: 3,
    height: 5,
    position: "absolute",
    shadowColor: colors.accentStrong,
    shadowOpacity: 0.55,
    shadowRadius: 7,
    width: 5
  },
  heroNetworkNodeOne: {
    left: 112,
    top: 128
  },
  heroNetworkNodeTwo: {
    right: 118,
    top: 164
  },
  heroNetworkNodeThree: {
    bottom: 72,
    left: 78
  },
  statusOnlineDot: {
    backgroundColor: colors.accent,
    borderColor: logiNexus.colors.home.backgroundDeepSpace,
    borderRadius: 6,
    borderWidth: 2,
    bottom: 4,
    height: 12,
    position: "absolute",
    right: 4,
    width: 12
  },
  topActions: {
    alignItems: "center",
    flexDirection: "row",
    gap: 8
  },
  topAvatarButton: {
    alignItems: "center",
    backgroundColor: "rgba(37, 208, 167, 0.12)",
    borderColor: colors.accent,
    borderRadius: 21,
    borderWidth: 1,
    height: 42,
    justifyContent: "center",
    width: 42
  },
  topAvatarText: {
    color: colors.accent,
    fontSize: 12,
    fontWeight: "900"
  },
  topBar: {
    alignItems: "center",
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    flexDirection: "row",
    gap: 12,
    justifyContent: "space-between",
    marginBottom: 14,
    padding: 12
  },
  topBrand: {
    alignItems: "center",
    flex: 1,
    flexDirection: "row",
    gap: 10
  },
  topBrandLogo: {
    backgroundColor: "rgba(37, 208, 167, 0.12)",
    borderColor: colors.border,
    borderRadius: 16,
    borderWidth: 1,
    color: colors.accent,
    fontSize: 12,
    fontWeight: "900",
    height: 32,
    lineHeight: 32,
    overflow: "hidden",
    textAlign: "center",
    width: 32
  },
  topBrandText: {
    color: colors.text,
    fontSize: 19,
    fontWeight: "900"
  },
  topBrandSubtext: {
    color: colors.muted,
    fontSize: 10,
    fontWeight: "800",
    marginTop: 1
  },
  topIconButton: {
    alignItems: "center",
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    height: 42,
    justifyContent: "center",
    minWidth: 42,
    paddingHorizontal: 8
  },
  topIconText: {
    color: colors.text,
    fontWeight: "900"
  }
}));
