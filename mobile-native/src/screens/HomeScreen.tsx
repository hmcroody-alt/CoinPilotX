import { NativeStackNavigationProp } from "@react-navigation/native-stack";
import { RouteProp, useNavigation, useRoute } from "@react-navigation/native";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ActivityIndicator, FlatList, Image, Pressable, RefreshControl, ScrollView, Share, StyleSheet, Text, View, useWindowDimensions } from "react-native";
import {
  hidePost,
  listFeed,
  loadCachedFeed,
  mutePostAuthor,
  PulsePost,
  pulsePostUrl,
  reactToPost,
  repostPost,
  savePost,
  toggleFollowAuthor
} from "../api/feed";
import { listStatuses, loadCachedStatuses, PulseStatus } from "../api/status";
import { HomePulseComposer } from "../components/HomePulseComposer";
import { LogiNexusBadge, LogiNexusEmptyState, LogiNexusPanel } from "../components/LogiNexus";
import { MasterNavigationDrawer } from "../components/MasterNavigationDrawer";
import { PostCard } from "../components/PostCard";
import { invalidateNativeSync, registerSyncInvalidation } from "../core/eventSync";
import { GlobalNavigationBadges, GlobalNavigationIdentity, LogiNexusGlobalHeader } from "../navigation/GlobalNavigation";
import { openDashboardRoute } from "../navigation/dashboardRouting";
import { openNativeRoute } from "../navigation/nativeRouteActions";
import { AppTabParamList, RootStackParamList } from "../navigation/types";
import { colors } from "../theme/colors";
import { logiNexus } from "../theme/logiNexus";

type HomeNavigation = NativeStackNavigationProp<RootStackParamList>;

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

const HOME_COMMAND_ITEMS = [
  { label: "Home", route: "/pulse", icon: "⌂", active: true },
  { label: "Dashboard", route: "/pulse/dashboard", icon: "▦" },
  { label: "Reels", route: "/pulse/reels", icon: "▶" },
  { label: "Videos", route: "/dashboard/media/video-library", icon: "▣" },
  { label: "Premium", route: "/pulse/premium", icon: "◆" },
  { label: "Saved", route: "/pulse/saved", icon: "★" }
];

export function HomeScreen({ badges, identity }: HomeScreenProps = {}) {
  const navigation = useNavigation<HomeNavigation>();
  const route = useRoute<RouteProp<AppTabParamList, "Home">>();
  const listRef = useRef<FlatList<PulsePost>>(null);
  const offsetRef = useRef(0);
  const hasMoreRef = useRef(false);
  const loadingMoreRef = useRef(false);
  const [posts, setPosts] = useState<PulsePost[]>([]);
  const [selectedFeed, setSelectedFeed] = useState(FEED_TABS[0].key);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [offline, setOffline] = useState(false);
  const [error, setError] = useState("");
  const [busyPostId, setBusyPostId] = useState<number | null>(null);
  const [statusItems, setStatusItems] = useState<PulseStatus[]>([]);
  const [statusLoading, setStatusLoading] = useState(true);
  const [statusOffline, setStatusOffline] = useState(false);
  const [statusError, setStatusError] = useState("");
  const [drawerOpen, setDrawerOpen] = useState(false);
  const activeTab = useMemo(() => FEED_TABS.find((tab) => tab.key === selectedFeed) || FEED_TABS[0], [selectedFeed]);

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
      const nextOffset = mode === "more" ? offsetRef.current : 0;
      setError("");
      setOffline(false);
      if (mode === "initial") setLoading(true);
      if (mode === "refresh") setRefreshing(true);
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
        loadingMoreRef.current = false;
        setLoadingMore(false);
      }
    },
    [selectedFeed]
  );

  useEffect(() => {
    load("initial", selectedFeed).catch(() => undefined);
  }, [load, selectedFeed]);

  useEffect(() => {
    loadStatuses().catch(() => undefined);
  }, [loadStatuses]);

  useEffect(() => {
    if (route.params?.openComposer) {
      requestAnimationFrame(() => listRef.current?.scrollToOffset({ offset: 430, animated: true }));
    }
  }, [route.params?.openComposer]);

  useEffect(() => {
    const stopActivity = registerSyncInvalidation("activity", () => load("refresh").catch(() => undefined));
    const stopNotifications = registerSyncInvalidation("notifications", () => load("refresh").catch(() => undefined));
    const stopMarketplace = registerSyncInvalidation("marketplace", () => load("refresh").catch(() => undefined));
    return () => {
      stopActivity();
      stopNotifications();
      stopMarketplace();
    };
  }, [load]);

  const updatePost = useCallback((postId: number, next: Partial<PulsePost>) => {
    setPosts((current) => current.map((post) => (post.id === postId ? { ...post, ...next } : post)));
  }, []);

  async function handleReact(post: PulsePost, reactionType: string) {
    setBusyPostId(post.id);
    const previous = post.viewer_reaction || "";
    const counts = { ...(post.reaction_counts || {}) };
    if (previous) counts[previous] = Math.max(0, Number(counts[previous] || 0) - 1);
    counts[reactionType] = Number(counts[reactionType] || 0) + 1;
    updatePost(post.id, { viewer_reaction: reactionType, reaction_counts: counts });
    try {
      const result = await reactToPost(post.id, reactionType);
      updatePost(post.id, {
        viewer_reaction: result.viewer_reaction || reactionType,
        reaction_counts: result.reaction_counts || counts
      });
    } catch {
      updatePost(post.id, { viewer_reaction: previous, reaction_counts: post.reaction_counts || {} });
    } finally {
      setBusyPostId(null);
    }
  }

  async function handleSave(post: PulsePost) {
    setBusyPostId(post.id);
    updatePost(post.id, { saved: !post.saved });
    try {
      const result = await savePost(post.id);
      updatePost(post.id, { saved: Boolean(result.saved ?? result.is_saved ?? !post.saved) });
    } catch {
      updatePost(post.id, { saved: post.saved });
    } finally {
      setBusyPostId(null);
    }
  }

  async function handleRepost(post: PulsePost) {
    setBusyPostId(post.id);
    updatePost(post.id, { reposted: true, repost_count: Number(post.repost_count || 0) + (post.reposted ? 0 : 1) });
    try {
      const result = await repostPost(post.id);
      updatePost(post.id, { reposted: Boolean(result.reposted ?? result.is_reposted ?? true) });
    } catch {
      updatePost(post.id, { reposted: post.reposted, repost_count: post.repost_count || 0 });
    } finally {
      setBusyPostId(null);
    }
  }

  async function handleShare(post: PulsePost) {
    await Share.share({ message: pulsePostUrl(post.id) }).catch(() => undefined);
  }

  async function handleFollow(post: PulsePost) {
    setBusyPostId(post.id);
    try {
      const result = await toggleFollowAuthor(post);
      const following = Boolean(result.following);
      const publicId = post.author?.public_player_id || post.author_public_player_id || "";
      setPosts((current) =>
        current.map((item) => {
          const itemPublicId = item.author?.public_player_id || item.author_public_player_id || "";
          if (publicId && itemPublicId === publicId) return { ...item, viewer_follows_author: following };
          return item.id === post.id ? { ...item, viewer_follows_author: following } : item;
        })
      );
      invalidateNativeSync(["activity", "notifications"], "home_follow", [
        {
          event_type: following ? "follow" : "unfollow",
          entity_type: "profile",
          entity_id: publicId || post.author?.user_id || post.author?.id || "unknown",
          invalidates: ["activity", "notifications"],
          metadata: { source: "native_home_feed" }
        }
      ]).catch(() => undefined);
    } finally {
      setBusyPostId(null);
    }
  }

  async function handleHide(post: PulsePost) {
    setBusyPostId(post.id);
    try {
      await hidePost(post.id);
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
    } catch (err) {
      setError(err instanceof Error ? err.message : "Post could not be hidden.");
    } finally {
      setBusyPostId(null);
    }
  }

  async function handleMute(post: PulsePost) {
    setBusyPostId(post.id);
    const authorId = Number(post.author?.user_id || post.author?.id || 0);
    const publicId = post.author?.public_player_id || post.author_public_player_id || "";
    try {
      const result = await mutePostAuthor(post);
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
    } catch (err) {
      setError(err instanceof Error ? err.message : "User could not be muted.");
    } finally {
      setBusyPostId(null);
    }
  }

  function selectFeed(feedKey: string) {
    if (feedKey === selectedFeed) return;
    setSelectedFeed(feedKey);
    setPosts([]);
    offsetRef.current = 0;
    hasMoreRef.current = false;
    loadingMoreRef.current = false;
    setLoading(true);
  }

  function refreshHome() {
    loadStatuses().catch(() => undefined);
    load("refresh").catch(() => undefined);
  }

  function openHomeRoute(routePath: string) {
    setDrawerOpen(false);
    openNativeRoute(navigation, routePath);
  }

  if (loading && !posts.length) {
    return (
      <View style={styles.center}>
        <ActivityIndicator color={colors.accent} />
        <Text style={styles.centerText}>Opening the PulseSoc network</Text>
      </View>
    );
  }

  return (
    <View style={styles.root}>
      <View pointerEvents="none" style={styles.homeAtmosphereRoot}>
        <View style={[styles.homeNebula, styles.homeNebulaLeft]} />
        <View style={[styles.homeNebula, styles.homeNebulaRight]} />
        <View style={styles.homeGridPlane} />
        <View style={[styles.homeStar, styles.homeStarOne]} />
        <View style={[styles.homeStar, styles.homeStarTwo]} />
        <View style={[styles.homeStar, styles.homeStarThree]} />
        <View style={[styles.homeSignalWave, styles.homeSignalWaveOne]} />
        <View style={[styles.homeSignalWave, styles.homeSignalWaveTwo]} />
      </View>
      <FlatList
        ref={listRef}
        style={styles.list}
        contentContainerStyle={styles.content}
        data={posts}
        keyExtractor={(item) => String(item.id)}
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
            onRefresh={refreshHome}
            onSelectFeed={selectFeed}
            onOpenUndx={() => navigation.navigate("Tabs", { screen: "PulseAI" })}
            onOpenPulseRadio={() => openDashboardRoute(navigation, "/pulse/music#pulse-radio")}
            onOpenLive={() => navigation.navigate("Tabs", { screen: "Live" })}
            onOpenSafety={() => navigation.navigate("SafetyHub", { title: "Safety Hub" })}
            onOpenRoute={openHomeRoute}
            onAddStatus={() => navigation.navigate("Tabs", { screen: "Status", params: { openCreator: true } })}
            onOpenStatus={(status) => navigation.navigate("StatusDetail", { statusId: status.id, title: status.author?.display_name || "Status" })}
            onOpenCamera={(mode) => {
              if (mode === "reel") navigation.navigate("CameraStudio", { target: "reel", mode: "reel", title: "Reel Camera" });
              else navigation.navigate("CameraStudio", { target: "feed", mode, title: mode === "video" ? "Video Camera" : "Camera" });
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
            }}
            onOpenMusic={() => openDashboardRoute(navigation, "/dashboard/media/music-library")}
          />
        }
        ListEmptyComponent={
          <LogiNexusEmptyState
            title={error ? "Connection interrupted" : `${activeTab.label} is quiet`}
            body={error || `No signals matched ${activeTab.label}. Pull to refresh or switch filters.`}
            tone={error ? "warning" : "default"}
          />
        }
        renderItem={({ item }) => (
          <PostCard
            post={item}
            busy={busyPostId === item.id}
            onOpen={(post) => navigation.navigate("PostDetail", { postId: post.id, title: "Post" })}
            onReact={handleReact}
            onSave={handleSave}
            onRepost={handleRepost}
            onPromote={(post) => navigation.navigate("GrowthCenter", { contentType: "post", contentId: post.id, title: "Promote Post" })}
            onShare={handleShare}
            onComment={(post) => navigation.navigate("PostDetail", { postId: post.id, title: "Comments" })}
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
            onAuthorPress={(post) => {
              const key = profileKeyForPost(post);
              if (key) navigation.navigate("ProfileDetail", { profileKey: key, title: post.author?.display_name || "Profile" });
            }}
          />
        )}
        onEndReached={() => load("more").catch(() => undefined)}
        onEndReachedThreshold={0.35}
        ListFooterComponent={loadingMore ? <ActivityIndicator style={styles.footer} color={colors.accent} /> : null}
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
  onRefresh,
  onSelectFeed,
  onOpenUndx,
  onOpenPulseRadio,
  onOpenLive,
  onOpenSafety,
  onOpenRoute,
  onAddStatus,
  onOpenStatus,
  onOpenCamera,
  onCreated,
  onOpenMusic
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
  onRefresh: () => void;
  onSelectFeed: (feedKey: string) => void;
  onOpenUndx: () => void;
  onOpenPulseRadio: () => void;
  onOpenLive: () => void;
  onOpenSafety: () => void;
  onOpenRoute: (routePath: string) => void;
  onAddStatus: () => void;
  onOpenStatus: (status: PulseStatus) => void;
  onOpenCamera: (mode: "photo" | "video" | "reel") => void;
  onCreated: (post?: PulsePost) => void;
  onOpenMusic: () => void;
}) {
  const { width } = useWindowDimensions();
  const compactHero = width < 430;
  const wideCanvas = width >= 900;
  return (
    <View style={styles.header}>
      <HomeTopBar onOpenDrawer={onOpenDrawer} onOpenSearch={onOpenSearch} onOpenActivity={onOpenActivity} onOpenProfile={onOpenProfile} badges={badges} identity={identity} />
      <View style={[styles.homeCanvas, wideCanvas && styles.homeCanvasWide]}>
        {wideCanvas ? <HomeCommandRail onOpenRoute={onOpenRoute} onOpenPulseRadio={onOpenPulseRadio} /> : null}
        <View style={styles.homePrimaryColumn}>
          <PulseNetworkHero posts={posts} statuses={statusItems} offline={offline || statusOffline} compact={compactHero} onRefresh={onRefresh} onOpenUndx={onOpenUndx} onOpenPulseRadio={onOpenPulseRadio} onOpenLive={onOpenLive} onOpenSafety={onOpenSafety} />
          <StatusRail
            items={statusItems}
            loading={statusLoading}
            offline={statusOffline}
            error={statusError}
            onAddStatus={onAddStatus}
            onOpenStatus={onOpenStatus}
          />
          <HomePulseComposer onCreated={onCreated} onOpenCamera={onOpenCamera} onOpenLive={onOpenLive} onOpenMusic={onOpenMusic} />
          <View style={styles.webRadioDock}>
            <Text style={styles.webRadioPause}>Ⅱ</Text>
            <View style={styles.webRadioCopy}>
              <Text style={styles.webRadioTitle} numberOfLines={1}>Beautiful Stranger</Text>
              <Text style={styles.webRadioMeta} numberOfLines={1}>PulseSoc Music · Pulse Radio</Text>
            </View>
            <Pressable style={styles.webRadioButton} onPress={onOpenPulseRadio}>
              <Text style={styles.webRadioButtonText}>Library</Text>
            </Pressable>
          </View>
          <View style={styles.feedTabsWrap}>
            <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.feedTabs}>
              {feedTabs.map((tab) => (
                <Pressable key={tab.key} style={[styles.feedTab, selectedFeed === tab.key && styles.feedTabActive]} onPress={() => onSelectFeed(tab.key)}>
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
        <Pressable style={styles.commandShortcutCard} onPress={() => onOpenRoute("/pulse/dashboard")}>
          <Text style={styles.commandShortcutTitle}>Dashboard</Text>
          <Text style={styles.commandShortcutMeta}>Account command center</Text>
        </Pressable>
        <Pressable style={styles.commandShortcutCard} onPress={() => onOpenRoute("/pulse/growth")}>
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
        <Pressable style={styles.commandRadioNow} onPress={onOpenPulseRadio}>
          <View style={styles.commandRadioDot} />
          <View style={styles.commandRadioCopy}>
            <Text style={styles.commandRadioNowTitle} numberOfLines={1}>Pulse Radio</Text>
            <Text style={styles.commandRadioNowMeta} numberOfLines={1}>Approved PulseSoc streams</Text>
          </View>
        </Pressable>
        <View style={styles.commandRadioActions}>
          <Pressable style={styles.commandRadioPrimary} onPress={onOpenPulseRadio}>
            <Text style={styles.commandRadioPrimaryText}>Play / Pause</Text>
          </Pressable>
          <Pressable style={styles.commandRadioSecondary} onPress={onOpenPulseRadio}>
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
  onRefresh,
  onOpenUndx,
  onOpenPulseRadio,
  onOpenLive,
  onOpenSafety,
  compact
}: {
  posts: PulsePost[];
  statuses: PulseStatus[];
  offline: boolean;
  compact: boolean;
  onRefresh: () => void;
  onOpenUndx: () => void;
  onOpenPulseRadio: () => void;
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
  const signalMetric = posts.length ? formatHeroMetric(posts.length) : offline ? "Cached" : "Live";
  const mood = posts.length ? "Curious" : offline ? "Cached" : "Curious";
  const summary = posts.length
    ? `${posts.length} public posts summarized. Aggregate activity only.`
    : offline
      ? "Cached network signals remain available."
      : "Signals are loading quietly so the feed stays fast.";
  return (
    <LogiNexusPanel style={styles.hero} tone="default">
      <View style={styles.heroAtmosphere}>
        <View style={[styles.heroGlow, styles.heroGlowPrimary]} />
        <View style={[styles.heroGlow, styles.heroGlowSecondary]} />
        <View style={[styles.heroSignalLine, styles.heroSignalLineOne]} />
        <View style={[styles.heroSignalLine, styles.heroSignalLineTwo]} />
        <View style={[styles.heroSignalLine, styles.heroSignalLineThree]} />
      </View>
      <View style={styles.heroTopLine}>
        <LogiNexusBadge label="Pulse Network" />
        <Pressable accessibilityRole="button" accessibilityLabel="Refresh Pulse Network" style={styles.heroHealthPill} onPress={onRefresh}>
          <View style={styles.heroHealthDot} />
          <Text style={styles.heroHealthText}>{offline ? "Resync" : "Optimal"}</Text>
        </Pressable>
      </View>
      <View style={styles.heroMoodRow}>
        <View style={styles.heroMoodCopy}>
          <Text style={styles.heroMoodTitle} numberOfLines={1}>{mood}</Text>
          <Text style={styles.heroMoodSummary} numberOfLines={2}>{summary}</Text>
        </View>
        <Pressable accessibilityRole="button" accessibilityLabel="Open Pulse Radio" style={styles.heroRadioPill} onPress={onOpenPulseRadio}>
          <Text style={styles.heroRadioIcon}>Ⅱ</Text>
          <View style={styles.heroRadioCopy}>
            <Text style={styles.heroRadioLabel}>Pulse Radio</Text>
            <Text style={styles.heroRadioMeta}>Now Playing</Text>
          </View>
        </Pressable>
      </View>
      {compact ? (
        <View style={styles.heroCompactMetricRow}>
          <HeroMetricBlock value={signalMetric} label={posts.length ? "Active signals" : offline ? "Cached" : "Ready"} tone="default" />
          <HeroMetricBlock value={creatorCount} label="creators" tone="intelligence" />
          <HeroMetricBlock value={liveCount} label="live" tone="danger" onPress={onOpenLive} />
        </View>
      ) : (
        <View style={styles.heroBlueprintRow}>
          <View style={styles.heroMetricStack}>
            <HeroMetricBlock value={signalMetric} label={posts.length ? "Active signals" : offline ? "Cached signals" : "Signals ready"} tone="default" />
            <HeroMetricBlock value={liveCount} label="Live broadcasts" tone="danger" onPress={onOpenLive} />
          </View>
          <View style={styles.heroMapPanel}>
            <Text style={styles.heroMapKicker}>Real-time overview</Text>
            <View style={styles.heroMapSignalLineOne} />
            <View style={styles.heroMapSignalLineTwo} />
            <View style={styles.heroMapSignalLineThree} />
            <View style={[styles.heroOrb, styles.heroOrbInMap]}>
              <View style={styles.heroRingOuter} />
              <View style={styles.heroRingInner} />
              <View style={styles.heroNodeBig} />
              <View style={[styles.heroNode, styles.heroNodeOne]} />
              <View style={[styles.heroNode, styles.heroNodeTwo]} />
              <View style={[styles.heroNode, styles.heroNodeThree]} />
              <View style={[styles.heroNode, styles.heroNodeFour]} />
              <Text style={styles.heroOrbText}>LN</Text>
            </View>
            <Text style={styles.heroMapCaption} numberOfLines={2}>
              {offline
                ? "Cached signals active."
                : `${creatorCount} creators online.`}
            </Text>
          </View>
        </View>
      )}
      {compact ? null : (
        <View style={[styles.heroOrb, styles.heroOrbCompact]}>
          <View style={styles.heroRingOuter} />
          <View style={styles.heroRingInner} />
          <View style={styles.heroNodeBig} />
          <View style={[styles.heroNode, styles.heroNodeOne]} />
          <View style={[styles.heroNode, styles.heroNodeTwo]} />
          <View style={[styles.heroNode, styles.heroNodeThree]} />
          <View style={[styles.heroNode, styles.heroNodeFour]} />
          <Text style={styles.heroOrbText}>LN</Text>
        </View>
      )}
      <View style={styles.heroQuickRow}>
        <HeroTile label="UNDX" value={String(alertCount)} body="UNDX alerts" tone="intelligence" icon="◇" onPress={onOpenUndx} />
        <HeroTile label="Pulse Radio" value="Radio" body="Open streams" tone="creator" icon="≋" onPress={onOpenPulseRadio} />
        <HeroTile label="Safety Shield" value={String(alertCount)} body="Scan ready" tone="safety" icon="⌾" onPress={onOpenSafety} />
      </View>
    </LogiNexusPanel>
  );
}

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
        <Text style={styles.sidePanelBody}>{statuses.length || posts.length ? "New posts, reactions, status, and replies hydrate through the native sync layer." : "Home is waiting for authenticated feed and status events."}</Text>
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
        <Text style={styles.heroTileLabel} numberOfLines={1}>{label}</Text>
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
  onOpenStatus
}: {
  items: PulseStatus[];
  loading: boolean;
  offline: boolean;
  error: string;
  onAddStatus: () => void;
  onOpenStatus: (status: PulseStatus) => void;
}) {
  return (
    <View style={styles.statusSection}>
      <View style={styles.statusHeader}>
        <Text style={styles.statusHeaderKicker}>Status</Text>
        <Text style={styles.statusHeaderAction}>View all →</Text>
      </View>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.statusRail}>
        <Pressable style={styles.addStatusCard} onPress={onAddStatus}>
          <Text style={styles.addStatusIcon}>+</Text>
          <Text style={styles.addStatusText}>Add Status</Text>
        </Pressable>
        {loading ? (
          <View style={styles.statusEmptyCard}>
            <ActivityIndicator color={colors.accent} />
            <Text style={styles.statusEmptyText}>Loading Status</Text>
          </View>
        ) : null}
        {!loading && !items.length ? (
          <View style={styles.statusEmptyCard}>
            <Text style={styles.statusEmptyTitle}>No Status yet.</Text>
            <Text style={styles.statusEmptyText}>{error || "Create one."}</Text>
          </View>
        ) : null}
        {items.map((status) => (
          <Pressable key={status.id} style={[styles.statusCard, !status.viewed && styles.statusCardUnseen]} onPress={() => onOpenStatus(status)}>
            <View style={styles.statusAvatar}>
              {status.author?.avatar_url ? (
                <Image source={{ uri: status.author.avatar_url }} style={styles.statusAvatarImage} />
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

function mergePosts(current: PulsePost[], incoming: PulsePost[]) {
  const seen = new Set(current.map((post) => post.id));
  return [...current, ...incoming.filter((post) => !seen.has(post.id))];
}

function profileKeyForPost(post: PulsePost) {
  const key =
    post.author?.public_player_id ||
    post.author?.username ||
    post.author_username ||
    post.author?.user_id ||
    post.author?.id ||
    post.author?.display_name ||
    post.author_name ||
    "";
  return String(key).trim();
}

const styles = StyleSheet.create({
  addStatusCard: {
    alignItems: "center",
    backgroundColor: "transparent",
    borderColor: "transparent",
    borderRadius: logiNexus.radius.large,
    borderWidth: 0,
    justifyContent: "center",
    minHeight: 64,
    padding: 3,
    width: 58
  },
  addStatusIcon: {
    color: colors.accent,
    backgroundColor: "rgba(3, 7, 18, 0.86)",
    borderColor: logiNexus.colors.home.borderActive,
    borderRadius: 20,
    borderWidth: 1,
    fontSize: 22,
    fontWeight: "900",
    height: 40,
    lineHeight: 38,
    overflow: "hidden",
    textAlign: "center",
    width: 40
  },
  addStatusText: {
    color: colors.text,
    fontSize: 9,
    fontWeight: "900",
    marginTop: 4,
    textAlign: "center"
  },
  center: {
    alignItems: "center",
    backgroundColor: colors.background,
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
    padding: 8,
    paddingBottom: 156,
    width: "100%"
  },
  commandIdentityCard: {
    backgroundColor: "rgba(7, 22, 35, 0.76)",
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
    backgroundColor: "rgba(4, 16, 27, 0.42)",
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
    backgroundColor: "rgba(3, 8, 18, 0.56)",
    borderColor: "rgba(50, 230, 179, 0.18)",
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
    backgroundColor: "rgba(6, 20, 32, 0.78)",
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
    gap: 10,
    width: 210
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
    borderColor: "transparent",
    borderRadius: logiNexus.radius.capsule,
    borderWidth: 1,
    minHeight: 34,
    justifyContent: "center",
    paddingHorizontal: 11
  },
  feedTabActive: {
    backgroundColor: "rgba(50, 230, 179, 0.13)",
    borderColor: logiNexus.colors.home.borderActive
  },
  feedTabText: {
    color: colors.muted,
    fontWeight: "900"
  },
  feedTabTextActive: {
    color: colors.accent
  },
  feedTabs: {
    gap: 8,
    paddingVertical: 3
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
    backgroundColor: "rgba(7, 16, 29, 0.9)",
    borderColor: logiNexus.colors.home.borderSubtle,
    borderRadius: 20,
    borderWidth: 1,
    marginBottom: 10,
    paddingHorizontal: 8,
    paddingVertical: 5
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
    gap: 14
  },
  homePrimaryColumn: {
    flex: 1,
    minWidth: 0
  },
  hero: {
    backgroundColor: "rgba(5, 13, 26, 0.94)",
    marginBottom: 6,
    overflow: "hidden",
    padding: 8
  },
  heroAtmosphere: {
    ...StyleSheet.absoluteFillObject,
    pointerEvents: "none"
  },
  heroGlow: {
    borderRadius: 180,
    opacity: 0.18,
    position: "absolute"
  },
  heroGlowPrimary: {
    backgroundColor: colors.accentStrong,
    height: 240,
    right: -90,
    top: -90,
    width: 240
  },
  heroGlowSecondary: {
    backgroundColor: colors.intelligence,
    bottom: -90,
    height: 240,
    left: -90,
    width: 240
  },
  heroTopLine: {
    alignItems: "center",
    flexDirection: "row",
    gap: 10,
    justifyContent: "space-between",
    zIndex: 2
  },
  heroHealthPill: {
    alignItems: "center",
    backgroundColor: "rgba(3, 7, 18, 0.62)",
    borderColor: logiNexus.colors.home.borderSubtle,
    borderRadius: logiNexus.radius.capsule,
    borderWidth: 1,
    flexDirection: "row",
    gap: 6,
    minHeight: 27,
    paddingHorizontal: 8
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
    fontSize: 10,
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
    backgroundColor: "rgba(3, 7, 18, 0.64)",
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
    gap: 5,
    marginTop: 5,
    zIndex: 2
  },
  heroRadioCopy: {
    minWidth: 0
  },
  heroRadioIcon: {
    backgroundColor: colors.accent,
    borderRadius: 16,
    color: colors.background,
    fontSize: 12,
    fontWeight: "900",
    height: 28,
    lineHeight: 28,
    overflow: "hidden",
    textAlign: "center",
    width: 28
  },
  heroRadioLabel: {
    color: colors.text,
    fontSize: 9,
    fontWeight: "900",
    letterSpacing: 0.6,
    textTransform: "uppercase"
  },
  heroRadioMeta: {
    color: colors.accent,
    fontSize: 8,
    fontWeight: "800",
    marginTop: 1
  },
  heroRadioPill: {
    alignItems: "center",
    backgroundColor: "rgba(10, 30, 43, 0.82)",
    borderColor: "rgba(50, 230, 179, 0.45)",
    borderRadius: 18,
    borderWidth: 1,
    flexDirection: "row",
    gap: 7,
    minHeight: 42,
    minWidth: 112,
    paddingHorizontal: 8,
    paddingVertical: 6
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
    gap: 6,
    marginTop: 6,
    minHeight: 90,
    zIndex: 2
  },
  heroCompactMetricRow: {
    flexDirection: "row",
    gap: 5,
    marginTop: 6,
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
    marginTop: 7,
    zIndex: 2
  },
  heroMoodSummary: {
    color: colors.muted,
    fontSize: 10,
    fontWeight: "800",
    lineHeight: 13,
    marginTop: 2
  },
  heroMoodTitle: {
    color: colors.text,
    fontSize: 28,
    fontWeight: "900",
    lineHeight: 32
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
    backgroundColor: "rgba(3, 7, 18, 0.56)",
    borderRadius: 12,
    borderWidth: 1,
    justifyContent: "center",
    minHeight: 38,
    paddingHorizontal: 6,
    paddingVertical: 3
  },
  heroMetricBlockLabel: {
    color: colors.muted,
    fontSize: 7,
    fontWeight: "800",
    lineHeight: 9,
    marginTop: 2
  },
  heroMetricBlockValue: {
    fontSize: 17,
    fontWeight: "900",
    lineHeight: 20
  },
  heroMetricStack: {
    gap: 5,
    width: 82,
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
    borderRadius: 15,
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
    backgroundColor: "rgba(3, 7, 18, 0.76)",
    borderRadius: 13,
    borderWidth: 1,
    flex: 1,
    flexDirection: "row",
    gap: 4,
    justifyContent: "center",
    minHeight: 34,
    minWidth: 0,
    paddingHorizontal: 5,
    paddingVertical: 4,
    zIndex: 2
  },
  heroTileArrow: {
    color: colors.muted,
    display: "none",
    fontSize: 10,
    fontWeight: "900",
    opacity: 0.55
  },
  heroTileBody: {
    color: colors.muted,
    display: "none",
    fontSize: 7,
    fontWeight: "700",
    marginTop: 1,
    textAlign: "center"
  },
  heroTileCopy: {
    alignItems: "flex-start",
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
    borderRadius: 9,
    borderWidth: 1,
    height: 18,
    justifyContent: "center",
    width: 18
  },
  heroTileIconText: {
    fontSize: 9,
    fontWeight: "900"
  },
  heroTileLabel: {
    color: colors.text,
    fontSize: 6.5,
    fontWeight: "900",
    letterSpacing: 0.2,
    textAlign: "left",
    textTransform: "uppercase"
  },
  heroTileValue: {
    fontSize: 7,
    fontWeight: "900",
    marginTop: 1,
    textAlign: "left"
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
  root: {
    backgroundColor: logiNexus.colors.home.backgroundDeepSpace,
    flex: 1
  },
  homeAtmosphereRoot: {
    ...StyleSheet.absoluteFillObject,
    overflow: "hidden"
  },
  homeGridPlane: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: "rgba(121, 210, 255, 0.025)",
    opacity: 0.5
  },
  homeNebula: {
    borderRadius: 260,
    height: 420,
    opacity: 0.22,
    position: "absolute",
    width: 420
  },
  homeNebulaLeft: {
    backgroundColor: "rgba(50, 230, 179, 0.18)",
    left: -180,
    top: 120
  },
  homeNebulaRight: {
    backgroundColor: "rgba(159, 124, 255, 0.2)",
    right: -160,
    top: 40
  },
  homeSignalWave: {
    backgroundColor: "rgba(50, 230, 179, 0.16)",
    borderRadius: 999,
    height: 2,
    position: "absolute",
    width: 42
  },
  homeSignalWaveOne: {
    right: "38%",
    top: "58%"
  },
  homeSignalWaveTwo: {
    left: "12%",
    top: "76%",
    width: 72
  },
  homeStar: {
    backgroundColor: "rgba(121, 210, 255, 0.45)",
    borderRadius: 2,
    height: 3,
    position: "absolute",
    width: 3
  },
  homeStarOne: {
    left: "18%",
    top: "14%"
  },
  homeStarTwo: {
    right: "21%",
    top: "33%"
  },
  homeStarThree: {
    left: "49%",
    top: "52%"
  },
  sideCard: {
    backgroundColor: "rgba(5, 13, 26, 0.92)",
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
    backgroundColor: "rgba(9, 20, 33, 0.66)",
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
    backgroundColor: "rgba(3, 7, 18, 0.62)",
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
    backgroundColor: "rgba(5, 13, 26, 0.9)",
    padding: 12
  },
  sidePanelBody: {
    color: colors.muted,
    fontSize: 12,
    fontWeight: "700",
    lineHeight: 18,
    marginTop: 10
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
    gap: 10,
    width: 290
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
    borderRadius: 22,
    borderWidth: 2,
    height: 44,
    justifyContent: "center",
    width: 44
  },
  statusAvatarImage: {
    borderRadius: 18,
    height: 36,
    width: 36
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
    gap: 4,
    minHeight: 64,
    padding: 3,
    width: 62
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
    padding: 6,
    width: 122
  },
  statusEmptyText: {
    color: colors.muted,
    fontSize: 9,
    marginTop: 2,
    textAlign: "center"
  },
  statusEmptyTitle: {
    color: colors.text,
    fontSize: 11,
    fontWeight: "900",
    textAlign: "center"
  },
  statusMeta: {
    color: colors.muted,
    fontSize: 9,
    fontWeight: "800"
  },
  statusName: {
    color: colors.text,
    fontSize: 10,
    fontWeight: "900",
    textAlign: "center"
  },
  statusHeader: {
    alignItems: "center",
    flexDirection: "row",
    justifyContent: "space-between",
    marginBottom: 3
  },
  statusHeaderAction: {
    color: colors.accentStrong,
    fontSize: 11,
    fontWeight: "900",
    textTransform: "uppercase"
  },
  statusHeaderKicker: {
    color: colors.accent,
    ...logiNexus.typography.home.sectionLabel,
    letterSpacing: 0.8,
    textTransform: "uppercase"
  },
  statusOffline: {
    color: colors.warning,
    fontSize: 12,
    marginTop: 8
  },
  statusRail: {
    gap: 6
  },
  statusSection: {
    marginBottom: 6
  },
  webRadioButton: {
    backgroundColor: "rgba(255,255,255,0.08)",
    borderColor: logiNexus.colors.home.borderSubtle,
    borderRadius: 12,
    borderWidth: 1,
    paddingHorizontal: 12,
    paddingVertical: 8
  },
  webRadioButtonText: {
    color: colors.text,
    fontSize: 11,
    fontWeight: "900"
  },
  webRadioCopy: {
    flex: 1,
    minWidth: 0
  },
  webRadioDock: {
    alignItems: "center",
    alignSelf: "center",
    backgroundColor: "rgba(5, 13, 26, 0.94)",
    borderColor: logiNexus.colors.home.borderSubtle,
    borderRadius: 20,
    borderWidth: 1,
    flexDirection: "row",
    gap: 10,
    marginBottom: -5,
    marginTop: -2,
    maxWidth: 520,
    paddingHorizontal: 10,
    paddingVertical: 8,
    width: "86%",
    zIndex: 4
  },
  webRadioMeta: {
    color: colors.muted,
    fontSize: 10,
    fontWeight: "800"
  },
  webRadioPause: {
    backgroundColor: colors.accent,
    borderRadius: 17,
    color: colors.background,
    fontSize: 15,
    fontWeight: "900",
    height: 34,
    lineHeight: 33,
    overflow: "hidden",
    textAlign: "center",
    width: 34
  },
  webRadioTitle: {
    color: colors.text,
    fontSize: 13,
    fontWeight: "900"
  },
  heroSignalLine: {
    backgroundColor: "rgba(121, 210, 255, 0.18)",
    height: 1,
    position: "absolute",
    transform: [{ rotate: "-18deg" }],
    width: 210
  },
  heroSignalLineOne: {
    left: 78,
    top: 88
  },
  heroSignalLineTwo: {
    left: 32,
    top: 150,
    transform: [{ rotate: "22deg" }]
  },
  heroSignalLineThree: {
    right: 110,
    top: 118,
    transform: [{ rotate: "8deg" }]
  },
  statusOnlineDot: {
    backgroundColor: colors.accent,
    borderColor: logiNexus.colors.home.backgroundDeepSpace,
    borderRadius: 6,
    borderWidth: 2,
    bottom: 3,
    height: 12,
    position: "absolute",
    right: 3,
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
});
