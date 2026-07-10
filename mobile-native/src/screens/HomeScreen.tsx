import { NativeStackNavigationProp } from "@react-navigation/native-stack";
import { RouteProp, useNavigation, useRoute } from "@react-navigation/native";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ActivityIndicator, FlatList, Image, Pressable, RefreshControl, ScrollView, Share, StyleSheet, Text, View } from "react-native";
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
import { LogiNexusBadge, LogiNexusButton, LogiNexusEmptyState, LogiNexusPanel, LogiNexusSignalIndicator } from "../components/LogiNexus";
import { MasterNavigationDrawer } from "../components/MasterNavigationDrawer";
import { PostCard } from "../components/PostCard";
import { invalidateNativeSync, registerSyncInvalidation } from "../core/eventSync";
import { LogiNexusGlobalHeader } from "../navigation/GlobalNavigation";
import { openDashboardRoute } from "../navigation/dashboardRouting";
import { openNativeRoute } from "../navigation/nativeRouteActions";
import { AppTabParamList, RootStackParamList } from "../navigation/types";
import { colors } from "../theme/colors";
import { logiNexus } from "../theme/logiNexus";

type HomeNavigation = NativeStackNavigationProp<RootStackParamList>;

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

export function HomeScreen() {
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
      <FlatList
        ref={listRef}
        style={styles.list}
        contentContainerStyle={styles.content}
        data={posts}
        keyExtractor={(item) => String(item.id)}
        refreshControl={<RefreshControl refreshing={refreshing} tintColor={colors.accent} onRefresh={() => refreshHome()} />}
        ListHeaderComponent={
          <HomeHeader
            activeTab={activeTab}
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
            onRefresh={refreshHome}
            onSelectFeed={selectFeed}
            onOpenUndx={() => navigation.navigate("Tabs", { screen: "PulseAI" })}
            onOpenPulseRadio={() => openDashboardRoute(navigation, "/pulse/music#pulse-radio")}
            onOpenLive={() => navigation.navigate("Tabs", { screen: "Live" })}
            onOpenSafety={() => navigation.navigate("SafetyHub", { title: "Safety Hub" })}
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
  activeTab,
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
  onRefresh,
  onSelectFeed,
  onOpenUndx,
  onOpenPulseRadio,
  onOpenLive,
  onOpenSafety,
  onAddStatus,
  onOpenStatus,
  onOpenCamera,
  onCreated,
  onOpenMusic
}: {
  activeTab: FeedTab;
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
  onRefresh: () => void;
  onSelectFeed: (feedKey: string) => void;
  onOpenUndx: () => void;
  onOpenPulseRadio: () => void;
  onOpenLive: () => void;
  onOpenSafety: () => void;
  onAddStatus: () => void;
  onOpenStatus: (status: PulseStatus) => void;
  onOpenCamera: (mode: "photo" | "video" | "reel") => void;
  onCreated: (post?: PulsePost) => void;
  onOpenMusic: () => void;
}) {
  return (
    <View style={styles.header}>
      <HomeTopBar onOpenDrawer={onOpenDrawer} onOpenSearch={onOpenSearch} onOpenActivity={onOpenActivity} onOpenProfile={onOpenProfile} />
      <PulseNetworkHero posts={posts} statuses={statusItems} offline={offline || statusOffline} onRefresh={onRefresh} onOpenUndx={onOpenUndx} onOpenPulseRadio={onOpenPulseRadio} onOpenLive={onOpenLive} onOpenSafety={onOpenSafety} />
      <StatusRail
        items={statusItems}
        loading={statusLoading}
        offline={statusOffline}
        error={statusError}
        onAddStatus={onAddStatus}
        onOpenStatus={onOpenStatus}
      />
      <HomePulseComposer onCreated={onCreated} onOpenCamera={onOpenCamera} onOpenLive={onOpenLive} onOpenMusic={onOpenMusic} />
      <View style={styles.feedTabsWrap}>
        <Text style={styles.feedTabsTitle}>{activeTab.label}</Text>
        <Text style={styles.feedTabsSubtitle}>{activeTab.description}</Text>
        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.feedTabs}>
          {feedTabs.map((tab) => (
            <Pressable key={tab.key} style={[styles.feedTab, selectedFeed === tab.key && styles.feedTabActive]} onPress={() => onSelectFeed(tab.key)}>
              <Text style={[styles.feedTabText, selectedFeed === tab.key && styles.feedTabTextActive]}>{tab.label}</Text>
            </Pressable>
          ))}
        </ScrollView>
      </View>
    </View>
  );
}

function HomeTopBar({
  onOpenDrawer,
  onOpenSearch,
  onOpenActivity,
  onOpenProfile
}: {
  onOpenDrawer: () => void;
  onOpenSearch: () => void;
  onOpenActivity: () => void;
  onOpenProfile: () => void;
}) {
  return (
    <LogiNexusGlobalHeader
      title="PulseSoc"
      subtitle="Powered by LogiNexus Intelligence"
      mode="home"
      showDrawer
      onOpenDrawer={onOpenDrawer}
      onOpenSearch={onOpenSearch}
      onOpenActivity={onOpenActivity}
      onOpenProfile={onOpenProfile}
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
  onOpenSafety
}: {
  posts: PulsePost[];
  statuses: PulseStatus[];
  offline: boolean;
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
  return (
    <LogiNexusPanel style={styles.hero} tone="default">
      <View style={styles.heroAtmosphere}>
        <View style={[styles.heroGlow, styles.heroGlowPrimary]} />
        <View style={[styles.heroGlow, styles.heroGlowSecondary]} />
      </View>
      <View style={styles.heroNetworkRow}>
        <View style={styles.heroLeft}>
          <LogiNexusBadge label="Pulse Network" />
          <Text style={styles.heroMetric}>{posts.length ? `${posts.length}` : offline ? "Cached" : "Live"}</Text>
          <Text style={styles.heroMetricLabel}>{posts.length ? "Signals active now" : offline ? "Cached signals" : "Network ready"}</Text>
          <View style={styles.heroDivider} />
          <Text style={styles.heroLiveMetric}>{liveCount}</Text>
          <Text style={styles.heroLiveLabel}>Live broadcasts</Text>
        </View>
        <View style={styles.heroOrb}>
          <View style={styles.heroRingOuter} />
          <View style={styles.heroRingInner} />
          <View style={styles.heroNodeBig} />
          <View style={[styles.heroNode, styles.heroNodeOne]} />
          <View style={[styles.heroNode, styles.heroNodeTwo]} />
          <View style={[styles.heroNode, styles.heroNodeThree]} />
          <View style={[styles.heroNode, styles.heroNodeFour]} />
          <Text style={styles.heroOrbText}>LN</Text>
        </View>
      </View>
      <Text style={styles.heroSubtitle}>
        {offline
          ? "Connection interrupted. Cached signals remain available while PulseSoc resyncs."
          : `${creatorCount} creators and ${alertCount} UNDX alerts are represented from server-authoritative Home safety and discovery data.`}
      </Text>
      <View style={styles.heroTileGrid}>
        <HeroTile label="UNDX" value={String(alertCount)} body="Important updates" tone="intelligence" icon="◇" onPress={onOpenUndx} />
        <HeroTile label="Pulse Radio" value="Now playing" body="Open Pulse Radio" tone="creator" icon="≋" onPress={onOpenPulseRadio} />
        <HeroTile label="Safety Shield" value={String(alertCount)} body="Scan trust signals" tone="safety" icon="⌾" onPress={onOpenSafety} />
      </View>
      <View style={styles.heroActions}>
        <LogiNexusButton label="Live" onPress={onOpenLive} tone="danger" variant="outline" />
        <LogiNexusButton label="Refresh" onPress={onRefresh} variant="outline" />
      </View>
    </LogiNexusPanel>
  );
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
        <Text style={styles.heroTileLabel}>{label}</Text>
        <Text style={[styles.heroTileValue, { color: accent }]}>{value}</Text>
        <Text style={styles.heroTileBody}>{body}</Text>
      </View>
      <Text style={styles.heroTileArrow}>→</Text>
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
        <Text style={styles.statusHeaderKicker}>› Your Orbit</Text>
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
            <Text style={styles.statusEmptyTitle}>No active status signals.</Text>
            <Text style={styles.statusEmptyText}>{error || "Transmit your first update."}</Text>
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
    backgroundColor: logiNexus.colors.home.surfaceGlass,
    borderColor: logiNexus.colors.home.borderSubtle,
    borderRadius: logiNexus.radius.large,
    borderWidth: 1,
    justifyContent: "center",
    minHeight: 128,
    padding: 14,
    width: 112
  },
  addStatusIcon: {
    color: colors.background,
    backgroundColor: colors.accent,
    borderRadius: 24,
    fontSize: 28,
    fontWeight: "900",
    height: 48,
    lineHeight: 48,
    overflow: "hidden",
    textAlign: "center",
    width: 48
  },
  addStatusText: {
    color: colors.text,
    fontSize: 15,
    fontWeight: "900",
    marginTop: 12,
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
    padding: 14,
    paddingBottom: 32
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
    minHeight: 44,
    justifyContent: "center",
    paddingHorizontal: 14
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
    gap: 10,
    paddingTop: 10
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
    backgroundColor: "rgba(7, 16, 29, 0.88)",
    borderColor: logiNexus.colors.home.borderSubtle,
    borderRadius: logiNexus.radius.panel,
    borderWidth: 1,
    marginBottom: 14,
    padding: 12
  },
  footer: {
    padding: 18
  },
  header: {
    marginBottom: 2
  },
  hero: {
    backgroundColor: logiNexus.colors.home.surfaceGlassStrong,
    marginBottom: 14,
    padding: 18
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
    height: 180,
    right: -70,
    top: -80,
    width: 180
  },
  heroGlowSecondary: {
    backgroundColor: colors.intelligence,
    bottom: -90,
    height: 210,
    left: -80,
    width: 210
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
    marginTop: 14
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
  heroLeft: {
    flex: 0.82,
    minWidth: 116
  },
  heroDivider: {
    backgroundColor: logiNexus.colors.home.borderSubtle,
    height: 1,
    marginVertical: 12,
    width: "74%"
  },
  heroLiveLabel: {
    color: colors.text,
    ...logiNexus.typography.home.cardMetadata
  },
  heroLiveMetric: {
    color: colors.text,
    fontSize: 24,
    fontWeight: "900",
    lineHeight: 29
  },
  heroMetric: {
    color: colors.text,
    ...logiNexus.typography.home.heroMetric,
    marginTop: 14
  },
  heroMetricLabel: {
    color: colors.text,
    ...logiNexus.typography.home.heroSupporting
  },
  heroNetworkRow: {
    alignItems: "center",
    flexDirection: "row",
    gap: 14,
    justifyContent: "space-between"
  },
  heroOrb: {
    alignItems: "center",
    alignSelf: "center",
    backgroundColor: "rgba(97, 216, 255, 0.045)",
    borderColor: "rgba(97, 216, 255, 0.44)",
    borderRadius: 78,
    borderWidth: 1,
    height: 156,
    justifyContent: "center",
    overflow: "hidden",
    width: 156
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
    fontSize: 24,
    fontWeight: "900"
  },
  heroRingInner: {
    borderColor: "rgba(50, 230, 179, 0.28)",
    borderRadius: 48,
    borderWidth: 1,
    height: 96,
    position: "absolute",
    width: 96
  },
  heroRingOuter: {
    borderColor: "rgba(159, 124, 255, 0.28)",
    borderRadius: 66,
    borderWidth: 1,
    height: 132,
    position: "absolute",
    width: 132
  },
  heroSubtitle: {
    color: colors.muted,
    ...logiNexus.typography.home.heroSupporting,
    marginTop: 14
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
    gap: 10,
    justifyContent: "space-between"
  },
  heroTile: {
    alignItems: "center",
    backgroundColor: "rgba(3, 7, 18, 0.68)",
    borderRadius: logiNexus.radius.large,
    borderWidth: 1,
    flexDirection: "row",
    gap: 10,
    minHeight: 74,
    padding: 12,
    width: "100%"
  },
  heroTileArrow: {
    color: colors.muted,
    fontSize: 18,
    fontWeight: "900"
  },
  heroTileBody: {
    color: colors.muted,
    fontSize: 12,
    fontWeight: "700",
    marginTop: 1
  },
  heroTileCopy: {
    flex: 1
  },
  heroTileGrid: {
    gap: 10,
    marginTop: 14
  },
  heroTileIcon: {
    alignItems: "center",
    borderRadius: 18,
    borderWidth: 1,
    height: 42,
    justifyContent: "center",
    width: 42
  },
  heroTileIconText: {
    fontSize: 20,
    fontWeight: "900"
  },
  heroTileLabel: {
    color: colors.text,
    fontSize: 12,
    fontWeight: "900",
    letterSpacing: 0.8,
    textTransform: "uppercase"
  },
  heroTileValue: {
    fontSize: 13,
    fontWeight: "900",
    marginTop: 2
  },
  list: {
    backgroundColor: logiNexus.colors.home.backgroundDeepSpace,
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
  statusAvatar: {
    alignItems: "center",
    backgroundColor: "rgba(37, 208, 167, 0.12)",
    borderColor: colors.accent,
    borderRadius: 33,
    borderWidth: 2,
    height: 66,
    justifyContent: "center",
    width: 66
  },
  statusAvatarImage: {
    borderRadius: 28,
    height: 56,
    width: 56
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
    gap: 6,
    minHeight: 116,
    padding: 8,
    width: 106
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
    minHeight: 136,
    padding: 16,
    width: 188
  },
  statusEmptyText: {
    color: colors.muted,
    marginTop: 4,
    textAlign: "center"
  },
  statusEmptyTitle: {
    color: colors.text,
    fontSize: 15,
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
    fontSize: 13,
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
    fontSize: 12,
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
    gap: 12
  },
  statusSection: {
    marginBottom: 14
  },
  statusOnlineDot: {
    backgroundColor: colors.accent,
    borderColor: logiNexus.colors.home.backgroundDeepSpace,
    borderRadius: 7,
    borderWidth: 2,
    bottom: 5,
    height: 14,
    position: "absolute",
    right: 5,
    width: 14
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
