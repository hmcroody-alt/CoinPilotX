import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { useEffect, useMemo, useRef, useState } from "react";
import { AccessibilityInfo, Animated, FlatList, Pressable, RefreshControl, StyleSheet, Text, View } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { deletePost, PulsePost, pulsePostUrl, reactToPost, repostPost, savablePostId } from "../api/feed";
import { describeDeleteError } from "../api/deleteErrors";
import { getMyProfile, getPublicProfile, listPublicProfilePosts, loadCachedProfile, profileErrorState, PulseProfile, toggleProfileFollow } from "../api/profile";
import { MessengerUserSearchResult, openDirectConversation } from "../api/messenger";
import { NativeProfileTarget, profileNavigationParams, profileTargetFromAuthor, resolveProfileTarget } from "../api/profileTarget";
import { peekSaveState } from "../social/savedStore";
import { setSaved } from "../social/useSaveAction";
import { ProfileHeader, ProfileModuleKey, ProfileStatKey } from "../components/ProfileHeader";
import { ContentCover, ContentCoverKind } from "../components/covers/ContentCover";
import { buildProfileContext, subjectName } from "../profile/profileContext";
import { profileOsDestination, tileNoun, visibleProfileOsTiles } from "../profile/profileOsTiles";
import { usePremiumTile } from "../profile/usePremiumTile";
import { trackPremium } from "../payments/premiumAnalytics";
import { useAuth } from "../session/auth";
import { GalacticAtmosphere } from "../components/GalacticAtmosphere";
import { LogiNexusScreenShell, LogiNexusStatePanel } from "../components/Screen";
import { invalidateNativeSync } from "../core/eventSync";
import { useBottomNavSurface } from "../navigation/BottomNavVisibility";
import { registerRefreshDestination } from "../navigation/refreshCoordinator";
import { RootStackParamList } from "../navigation/types";
import { actionKey, useSocialActionGuard } from "../social/actionGuard";
import { colors } from "../theme/colors";
import { profileNeon } from "../theme/profileNeon";
import { sharePulseObject } from "../sharing/nativeShare";
import { createThemedStyles } from "../theme/themedStyles";

type Props = Partial<NativeStackScreenProps<RootStackParamList, "ProfileDetail">>;
type TabKey = "posts" | "media" | "about";

export function ProfileScreen({ route, navigation }: Props) {
  const dock = useBottomNavSurface();
  const listRef = useRef<FlatList<PulsePost>>(null);
  const scrollY = useRef(new Animated.Value(0)).current;
  const onScroll = useMemo(
    () => Animated.event([{ nativeEvent: { contentOffset: { y: scrollY } } }], { useNativeDriver: true, listener: dock.handlers.onScroll }),
    [dock.handlers.onScroll, scrollY]
  );
  const profileTarget = useMemo<NativeProfileTarget | null>(() => resolveProfileTarget(route?.params || null), [
    route?.params?.profileKey,
    route?.params?.userId,
    route?.params?.publicPlayerId,
    route?.params?.username
  ]);
  const profileKey = profileTarget?.profileKey || "";
  const owner = !profileTarget;
  const { authState } = useAuth();
  const viewerUserId = Number(authState.user?.user_id || 0);
  const [profile, setProfile] = useState<PulseProfile | null>(null);
  const [posts, setPosts] = useState<PulsePost[]>([]);
  const [nextOffset, setNextOffset] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [tab, setTab] = useState<TabKey>("posts");
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [offline, setOffline] = useState(false);
  const [errorState, setErrorState] = useState<ReturnType<typeof profileErrorState> | null>(null);
  const [contentUnavailable, setContentUnavailable] = useState(false);
  const [actionMessage, setActionMessage] = useState("");
  const [followBusy, setFollowBusy] = useState(false);
  const refreshingRef = useRef(false);
  // Replaces a `busyPostId` scalar. A scalar can mark at most one card, so acting
  // on one post greyed out every other card's buttons, and the handlers guarded
  // themselves by reading state that React had not committed yet. The guard locks
  // per action+id in a ref, which is what actually stops the second tap.
  const guard = useSocialActionGuard();

  const visiblePosts = useMemo(() => (tab === "media" ? posts.filter((post) => post.media?.length) : posts), [posts, tab]);

  // The single source of truth for "whose profile is this". Every Profile OS
  // tile reads its subject from here, so no destination has to work it out from
  // the auth store — which is how tiles ended up loading the viewer's own data.
  // `profile.is_self` from the server settles ownership when it is present;
  // until the payload lands we fall back to the route target, which is already
  // the right subject.
  const profileContext = useMemo(
    () => buildProfileContext({ viewerUserId, profile, target: profileTarget }),
    [viewerUserId, profile, profileTarget]
  );
  const profileOsTiles = useMemo(() => visibleProfileOsTiles(profileContext), [profileContext]);
  // Owner-only. The Premium tile is not on a visitor's grid, so nothing is
  // fetched and no one else's screen pays for it.
  const premiumTile = usePremiumTile(profileContext.isOwnProfile);
  const moduleState = useMemo(() => (premiumTile ? { premium: premiumTile } : undefined), [premiumTile]);

  function postsLookupKey(nextProfile: PulseProfile | null, fallbackKey = profileKey) {
    if (!nextProfile) return fallbackKey;
    const canonicalUserId = Number(nextProfile.user_id || (owner ? viewerUserId : 0) || 0);
    return (canonicalUserId > 0 ? String(canonicalUserId) : "") || nextProfile.canonical_profile_key || nextProfile.public_player_id || nextProfile.username || fallbackKey || "";
  }

  function profilePostTarget(nextProfile: PulseProfile) {
    const canonicalUserId = Number(nextProfile.user_id || (owner ? viewerUserId : 0) || 0);
    const key = postsLookupKey(nextProfile);
    return {
      userId: canonicalUserId > 0 ? canonicalUserId : undefined,
      profileKey: key,
      publicPlayerId: nextProfile.public_player_id,
      username: nextProfile.username
    };
  }

  async function load(mode: "initial" | "refresh" = "initial") {
    setErrorState(null);
    setOffline(false);
    setContentUnavailable(false);
    if (mode === "initial") setLoading(true);
    if (mode === "refresh") {
      if (refreshingRef.current) return;
      refreshingRef.current = true;
      setRefreshing(true);
    }
    try {
      let canonicalProfile: PulseProfile;
      if (owner) {
        canonicalProfile = await getMyProfile();
      } else {
        canonicalProfile = await getPublicProfile(profileTarget);
      }
      setProfile(canonicalProfile);
      try {
        const target = profilePostTarget(canonicalProfile);
        const feed = target.profileKey ? await listPublicProfilePosts(target, { limit: 20, offset: 0 }) : { posts: [] as PulsePost[], next_offset: 0, has_more: false };
        setPosts(feed.posts || []);
        setNextOffset(Number(feed.next_offset || feed.posts?.length || 0));
        setHasMore(Boolean(feed.has_more));
      } catch {
        // Profile identity/About data is already canonical. Preserve it and
        // any previously loaded grid while reporting the narrower content
        // outage truthfully; an unavailable query is not an empty profile.
        setContentUnavailable(true);
      }
    } catch (loadError) {
      const mappedError = profileErrorState(loadError);
      const cached = await loadCachedProfile(owner ? "me" : profileTarget || profileKey);
      if (cached) {
        setProfile(cached);
        setOffline(Boolean(mappedError.offline || mappedError.retryable));
        setErrorState(mappedError.retryable ? mappedError : null);
        setContentUnavailable(true);
      } else if (!owner && posts.length) {
        setOffline(true);
      } else {
        setErrorState(mappedError);
      }
    } finally {
      setLoading(false);
      setRefreshing(false);
      if (mode === "refresh") refreshingRef.current = false;
    }
  }

  async function loadMorePosts() {
    if (loadingMore || !hasMore || !profile) return;
    const target = profilePostTarget(profile);
    if (!target.profileKey) return;
    setLoadingMore(true);
    try {
      const page = await listPublicProfilePosts(target, { limit: 20, offset: nextOffset });
      setPosts((current) => {
        const seen = new Set(current.map((item) => item.id));
        return current.concat((page.posts || []).filter((item) => !seen.has(item.id)));
      });
      setNextOffset(Number(page.next_offset || nextOffset + (page.posts || []).length));
      setHasMore(Boolean(page.has_more));
    } catch {
      setActionMessage("More posts could not load. Your current grid is preserved.");
    } finally {
      setLoadingMore(false);
    }
  }

  const profileLoadKey = owner ? `owner:${viewerUserId || "pending"}` : profileKey;

  useEffect(() => {
    load("initial").catch(() => undefined);
  }, [profileLoadKey]);

  useEffect(() => {
    if (!owner) return undefined;
    return registerRefreshDestination("profile", {
      scrollToTop: () => listRef.current?.scrollToOffset({ offset: 0, animated: true }),
      refresh: async () => {
        await load("refresh");
        AccessibilityInfo.announceForAccessibility?.("Profile refreshed");
      },
      isRefreshing: () => refreshingRef.current
    });
  }, [owner, profileLoadKey]);

  async function followProfile() {
    if (!profile || owner || followBusy) return;
    setFollowBusy(true);
    setActionMessage("");
    try {
      const result = await toggleProfileFollow(profile);
      const following = Boolean(result.following);
      setProfile((current) => current ? { ...current, viewer_follows: following, follower_count: Math.max(0, Number(current.follower_count || 0) + (following ? 1 : -1)) } : current);
      setActionMessage(following ? `Following ${profile.display_name}.` : `Unfollowed ${profile.display_name}.`);
    } catch (followError) {
      setActionMessage(followError instanceof Error ? followError.message : "Follow action failed.");
    } finally {
      setFollowBusy(false);
    }
  }

  async function messageProfile() {
    if (!profile || owner) return;
    setActionMessage("Opening secure conversation…");
    try {
      const target: MessengerUserSearchResult = {
        id: profile.user_id,
        user_id: profile.user_id,
        display_name: profile.display_name,
        public_player_id: profile.public_player_id || profile.username || profileTarget?.publicPlayerId || profileKey,
        avatar_url: profile.avatar_url || "",
        premium: Boolean(profile.premium_status),
        premium_mark: profile.verified_badge ? "verified" : ""
      };
      const result = await openDirectConversation(target);
      navigation?.navigate("Chat", { conversationId: result.conversation_id, title: profile.display_name });
    } catch (messageError) {
      setActionMessage(messageError instanceof Error ? messageError.message : "Conversation could not open.");
    }
  }

  async function startCall(callType: "audio" | "video") {
    if (!profile || owner) return;
    setActionMessage(callType === "video" ? "Starting secure video call…" : "Starting secure call…");
    try {
      const target: MessengerUserSearchResult = {
        id: profile.user_id,
        user_id: profile.user_id,
        display_name: profile.display_name,
        public_player_id: profile.public_player_id || profile.username || profileTarget?.publicPlayerId || profileKey,
        avatar_url: profile.avatar_url || "",
        premium: Boolean(profile.premium_status),
        premium_mark: profile.verified_badge ? "verified" : ""
      };
      const result = await openDirectConversation(target);
      navigation?.navigate("Call", { conversationId: result.conversation_id, callType, direction: "outgoing", title: profile.display_name });
    } catch (callError) {
      setActionMessage(callError instanceof Error ? callError.message : "Call could not start.");
    }
  }

  function handleStat(key: ProfileStatKey) {
    if (key === "posts") return setTab("posts");
    if (key === "media") return setTab("media");
    setActionMessage(key === "followers" ? `${profile?.follower_count || 0} followers.` : `${profile?.following_count || 0} following.`);
  }

  /**
   * Open a Profile OS tile against the profile that is currently on screen.
   *
   * The destination and its params come from `profileOsTiles`, which knows the
   * owner route and the visitor route for each tile separately. This function
   * deliberately holds no per-tile knowledge any more: the previous version was
   * a switch that sent every tile to a personal surface (`Music`, `SafetyHub`,
   * `BusinessOs`, the Saved/Groups tabs), so visiting Maria and tapping a tile
   * opened the signed-in user's own data.
   *
   * `unsupported` means the tile has no destination scoped to this profile
   * owner. Those tiles are filtered out of the grid for visitors, so this is a
   * backstop rather than the normal path — and it explains itself instead of
   * falling back to the viewer's data.
   */
  function handleModule(key: ProfileModuleKey) {
    const destination = profileOsDestination(key, profileContext);
    // Funnel entry. Fired on the tap rather than on arrival so a tap that never
    // reaches the screen still counts as an attempt to reach it.
    if (key === "premium" && destination.kind === "route") trackPremium("premium_tile_opened");
    if (destination.kind === "tab") return setTab(destination.tab);
    if (destination.kind === "route") {
      // The route name is validated against RootStackParamList in the tile
      // registry; navigate() cannot be called with a union of names because its
      // params type is keyed per-route, so it is widened only here.
      const navigateTo = navigation?.navigate as ((name: string, params?: Record<string, unknown>) => void) | undefined;
      return navigateTo?.(destination.name, destination.params);
    }
    setActionMessage(`${tileNoun(key)} is not available on ${subjectName(profileContext)}'s profile yet.`);
    return undefined;
  }

  function updateProfilePost(id: number, patch: Partial<PulsePost>) {
    setPosts((current) => current.map((item) => (item.id === id ? { ...item, ...patch } : item)));
  }

  // Routed through the shared save store rather than this screen's guard: the
  // same post is very often also on the feed underneath this profile, and a
  // per-screen optimistic update leaves that copy disagreeing. See
  // `social/useSaveAction.ts`.
  async function handleSave(post: PulsePost) {
    const savableId = savablePostId(post);
    const wasSaved = peekSaveState("post", savableId)?.saved ?? Boolean(post.saved ?? post.is_saved);
    const outcome = await setSaved({ type: "post", id: savableId }, !wasSaved);
    updateProfilePost(post.id, { saved: outcome.saved, is_saved: outcome.saved });
    if (!outcome.ok && outcome.message) setActionMessage(outcome.message);
  }

  async function handleReact(post: PulsePost, reactionType: string) {
    const previous = post.viewer_reaction || "";
    const previousCounts = post.reaction_counts || {};
    const removing = previous === reactionType;
    // Counts are recomputed here, not left to the server round trip, so the
    // number under the button moves with the icon. Home and PostDetail do the
    // same arithmetic; a profile card that only flipped the icon showed a stale
    // count until the next refresh.
    const counts: Record<string, number> = { ...previousCounts };
    if (previous) counts[previous] = Math.max(0, Number(counts[previous] || 0) - 1);
    if (!removing) counts[reactionType] = Number(counts[reactionType] || 0) + 1;
    await guard.run(actionKey("post_react", post.id), () => reactToPost(post.id, reactionType), {
      // Changing reaction mid-flight is legitimate, so the second tap runs and
      // the guard's sequence check discards the slower, older answer.
      supersede: true,
      optimistic: () => updateProfilePost(post.id, { viewer_reaction: removing ? "" : reactionType, reaction_counts: counts }),
      onResult: (result) => updateProfilePost(post.id, {
        viewer_reaction: String(result.removed ? "" : result.viewer_reaction ?? result.reaction_type ?? (removing ? "" : reactionType)),
        reaction_counts: result.reaction_counts ?? counts
      }),
      onRollback: () => updateProfilePost(post.id, { viewer_reaction: previous, reaction_counts: previousCounts }),
      onError: setActionMessage
    });
  }

  async function handleRepost(post: PulsePost) {
    const previousReposted = Boolean(post.reposted ?? post.is_reposted);
    const previousCount = Number(post.repost_count || 0);
    const undo = previousReposted;
    // PostCard was rendered here without an `onRepost` prop at all, so its
    // labelled repost button ran `onRepost?.(post)` against undefined and did
    // nothing — a button that looked live, announced itself to screen readers, and
    // silently discarded every tap. Same toggle as Home and PostDetail.
    await guard.run(actionKey("post_repost", post.id), () => repostPost(post.id, { undo }), {
      optimistic: () => updateProfilePost(post.id, {
        reposted: !undo,
        repost_count: undo ? Math.max(0, previousCount - 1) : previousCount + 1
      }),
      onResult: (result) => updateProfilePost(post.id, {
        reposted: Boolean(result.reposted ?? result.is_reposted ?? !undo),
        repost_count: typeof result.repost_count === "number"
          ? result.repost_count
          : undo ? Math.max(0, previousCount - 1) : previousCount + 1
      }),
      onRollback: () => updateProfilePost(post.id, { reposted: previousReposted, repost_count: previousCount }),
      onError: setActionMessage
    });
  }

  async function handleDeletePost(post: PulsePost) {
    await guard.run(actionKey("post_delete", post.id), () => deletePost(post.id), {
      // No optimistic removal: a post that vanishes and then reappears reads as
      // the feed resurrecting it. The row leaves only once the server agrees.
      onResult: () => {
        setPosts((current) => current.filter((item) => item.id !== post.id));
        invalidateNativeSync(["activity", "notifications"], "profile_delete", [
          {
            event_type: "pulse_post_deleted",
            entity_type: "post",
            entity_id: post.id,
            invalidates: ["activity", "notifications"],
            metadata: { source: "native_profile" }
          }
        ]).catch(() => undefined);
      },
      // Delete keeps its own copy: describeDeleteError distinguishes "not yours"
      // from "already gone", and users act on that difference.
      onError: (_message, deleteError) => setActionMessage(describeDeleteError(deleteError, "Post"))
    });
  }

  // A skeleton rather than a centred spinner: the profile shell is the same
  // shape for every member, so it can be drawn immediately and the identity
  // fills in underneath. A spinner would hold a full-screen blank for the whole
  // round trip and then jump straight to a dense screen.
  if (loading && !profile) {
    return <ProfileSkeleton />;
  }

  if (!profile) {
    const state = errorState || profileErrorState(new Error("Profile could not load."));
    return (
      <LogiNexusScreenShell>
        <LogiNexusStatePanel state="error" title={state.title} body={state.body}>
        {state.retryable ? (
          <Pressable style={styles.retryButton} onPress={() => load("refresh").catch(() => undefined)}>
            <Text style={styles.retryButtonText}>Retry native profile</Text>
          </Pressable>
        ) : null}
        </LogiNexusStatePanel>
      </LogiNexusScreenShell>
    );
  }

  return (
    <View style={styles.root}>
      <GalacticAtmosphere variant="profile" scrollY={scrollY} testID="profile-galactic-atmosphere" />
      <Animated.FlatList
      ref={listRef}
      style={styles.list}
      contentContainerStyle={[styles.content, dock.contentPadding]}
      data={tab === "about" ? [] : visiblePosts}
      numColumns={3}
      columnWrapperStyle={tab === "about" ? undefined : styles.gridRow}
      keyExtractor={(item) => String(item.id)}
      refreshControl={<RefreshControl refreshing={refreshing} tintColor={colors.accent} onRefresh={() => load("refresh").catch(() => undefined)} />}
      ListHeaderComponent={
        <View>
          <ProfileHeader
            profile={profile}
            publicKey={profileKey}
            owner={owner}
            followBusy={followBusy}
            scrollY={scrollY}
            onEdit={() => navigation?.navigate("ProfileEdit")}
            onCustomize={() => navigation?.navigate("ProfileEdit")}
            onGrowth={() => navigation?.navigate("GrowthCenter", { contentType: "profile", title: "Grow Profile" })}
            onSafety={() => navigation?.navigate("SafetyHub", { title: "Safety Hub", section: profileKey ? "reports" : "overview" })}
            onMessage={() => messageProfile().catch(() => undefined)}
            onFollow={() => followProfile().catch(() => undefined)}
            onCall={() => startCall("audio").catch(() => undefined)}
            onVideoCall={() => startCall("video").catch(() => undefined)}
            onRefresh={() => load("refresh").catch(() => undefined)}
            onStatPress={handleStat}
            onModulePress={handleModule}
            moduleKeys={profileOsTiles}
            moduleState={moduleState}
            moduleOwnerName={profileContext.isOwnProfile ? "" : subjectName(profileContext)}
          />
          <View style={styles.section}>
            {offline ? <Text style={styles.offline}>Showing saved profile</Text> : null}
            {errorState ? <Text style={styles.error}>{errorState.body}</Text> : null}
            {contentUnavailable ? (
              <Pressable accessibilityRole="button" style={styles.retryButton} onPress={() => load("refresh").catch(() => undefined)}>
                <Text style={styles.retryButtonText}>Retry profile content</Text>
              </Pressable>
            ) : null}
            {actionMessage ? <Text accessibilityLiveRegion="polite" style={styles.actionMessage}>{actionMessage}</Text> : null}
            <View style={styles.tabs}>
              <TabButton label="Posts" value="posts" active={tab} onPress={setTab} />
              <TabButton label="Media" value="media" active={tab} onPress={setTab} />
              <TabButton label="About" value="about" active={tab} onPress={setTab} />
            </View>
            {tab === "about" ? <AboutPanel profile={profile} owner={owner} onVerification={() => navigation?.navigate("VerificationCenter", { title: "Verification Center" })} onSafety={() => navigation?.navigate("SafetyHub", { title: "Safety Hub", section: profileKey ? "reports" : "overview" })} onSellerStore={() => navigation?.navigate("SellerStore", { title: "Seller / Store" })} /> : null}
          </View>
        </View>
      }
      ListEmptyComponent={tab === "about" ? null : <Text style={styles.empty}>{contentUnavailable ? "Profile content temporarily unavailable\nRetry to load canonical posts and media." : owner ? "No posts yet\nShare your first post and it will appear here." : "No posts yet\nThis profile has not shared any posts."}</Text>}
      ListFooterComponent={loadingMore ? <Text style={styles.loadingMore}>Loading more posts…</Text> : null}
      renderItem={({ item }) => <ProfilePostGridTile post={item} onPress={() => navigation?.navigate("ProfilePostViewer", {
        profileId: profile.user_id,
        profileKey: String(profile.user_id || "") || profile.canonical_profile_key || profile.public_player_id || profile.username || profileKey,
        postId: item.id,
        postIds: visiblePosts.map((post) => post.id),
        nextOffset,
        hasMore,
        contentTab: tab === "media" ? "media" : "posts",
        owner,
        source: "PROFILE_GRID"
      })} />}
      onEndReached={() => loadMorePosts().catch(() => undefined)}
      onEndReachedThreshold={0.55}
      onScroll={onScroll}
      onScrollBeginDrag={dock.handlers.onScrollBeginDrag}
      scrollEventThrottle={dock.handlers.scrollEventThrottle}
      />
    </View>
  );
}

export function ProfilePostGridTile({ post, onPress }: { post: PulsePost; onPress: () => void }) {
  const media = post.media?.[0] || post.media_assets?.[0] || post.attachments?.[0];
  const kind = String(media?.media_type || media?.type || (post.video_url ? "video" : post.image_url ? "image" : post.music ? "music" : "text")).toLowerCase();
  const thumbnail = media?.thumbnail_url || media?.poster_url || post.thumbnail_url || (kind === "image" ? media?.media_url || media?.url : "") || post.image_url || post.original_post?.thumbnail_url || post.original_post?.image_url || "";
  const excerpt = String(post.body || post.text || post.content || post.title || "PulseSoc post").trim();
  const duration = Number(media?.duration_seconds || (media?.duration_ms ? media.duration_ms / 1000 : 0));
  const durationLabel = duration > 0 ? `${Math.floor(duration / 60)}:${String(Math.floor(duration % 60)).padStart(2, "0")}` : "";
  const multi = Number(post.media?.length || post.media_assets?.length || post.attachments?.length || 0) > 1;
  const pinned = Boolean((post as PulsePost & { pinned?: boolean; is_pinned?: boolean }).pinned || (post as PulsePost & { is_pinned?: boolean }).is_pinned);
  const label = `${kind === "text" ? "Text" : kind === "video" ? "Video" : kind === "music" ? "Music" : "Image"} post. ${excerpt.slice(0, 80)}${durationLabel ? `. Duration ${durationLabel}` : ""}${pinned ? ". Pinned" : ""}`;
  const coverKind: ContentCoverKind =
    kind === "video" || kind === "reel" ? (kind as ContentCoverKind)
      : kind === "music" || kind === "audio" ? "audio"
      : kind === "text" ? (post.repost || post.original_post ? "shared" : "text")
      : "photo";
  return (
    <Pressable accessibilityRole="button" accessibilityLabel={label} testID={`profile-grid-tile-${post.id}`} style={styles.gridTile} onPress={onPress}>
      <ContentCover
        kind={coverKind}
        imageUrl={thumbnail || null}
        text={excerpt}
        title={excerpt.slice(0, 60)}
        durationSeconds={duration}
        hasMusic={kind === "music" || Boolean(post.music)}
        style={styles.gridImage}
      />
      <View style={styles.tileSignals}>
        {post.repost || post.original_post ? <Ionicons name="repeat" size={14} color="#fff" /> : null}
        {multi ? <Ionicons name="copy-outline" size={14} color="#fff" /> : null}
        {pinned ? <Ionicons name="pin" size={14} color="#fff" /> : null}
      </View>
    </Pressable>
  );
}

/**
 * Identity-shaped placeholder shown while the profile round trip is in flight.
 *
 * Deliberately static. A shimmer here would be an animation running during the
 * most contended moment of the screen's life — the initial fetch plus the first
 * layout — and reduced-motion users would have to be excluded from it anyway.
 */
function ProfileSkeleton() {
  return (
    <View
      style={styles.root}
      accessibilityRole="progressbar"
      accessibilityLabel="Loading profile"
      testID="profile-skeleton"
    >
      <GalacticAtmosphere variant="profile" />
      <View style={styles.skeletonBody}>
        <View style={styles.skeletonAvatar} />
        <View style={styles.skeletonName} />
        <View style={styles.skeletonHandle} />
        <View style={styles.skeletonStats} />
        <View style={styles.skeletonActions}>
          <View style={styles.skeletonAction} />
          <View style={styles.skeletonAction} />
          <View style={styles.skeletonAction} />
        </View>
      </View>
    </View>
  );
}

function TabButton({ label, value, active, onPress }: { label: string; value: TabKey; active: TabKey; onPress: (value: TabKey) => void }) {
  return (
    <Pressable style={[styles.tab, active === value ? styles.tabActive : undefined]} onPress={() => onPress(value)}>
      <Text style={[styles.tabText, active === value ? styles.tabTextActive : undefined]}>{label}</Text>
    </Pressable>
  );
}

function AboutPanel({ profile, owner, onVerification, onSafety, onSellerStore }: { profile: PulseProfile; owner: boolean; onVerification: () => void; onSafety: () => void; onSellerStore: () => void }) {
  return (
    <View style={styles.about}>
      <Text style={styles.aboutTitle}>About</Text>
      <Text style={styles.aboutBody}>{profile.bio || "No bio yet."}</Text>
      <Text style={styles.aboutMeta}>Links: {profile.social_links || "None loaded"}</Text>
      <Text style={styles.aboutMeta}>Expertise: {profile.expertise_tags || "None loaded"}</Text>
      <Text style={styles.aboutMeta}>Visibility: {profile.profile_visibility || "public"}</Text>
      <Text style={styles.aboutMeta}>Status: {profile.account_status || "active"}</Text>
      <Text style={styles.aboutMeta}>Verification: {profile.verification_status || (profile.verified_badge ? "approved" : "not started")}</Text>
      {owner ? (
        <Pressable style={styles.webLink} onPress={onVerification}>
          <Text style={styles.webLinkText}>Open Verification Center</Text>
        </Pressable>
      ) : null}
      {owner ? (
        <Pressable style={styles.webLink} onPress={onSellerStore}>
          <Text style={styles.webLinkText}>Open Seller / Store Management</Text>
        </Pressable>
      ) : null}
      <Pressable style={styles.webLink} onPress={onSafety}>
        <Text style={styles.webLinkText}>Open Safety Hub</Text>
      </Pressable>
    </View>
  );
}

const styles = createThemedStyles(() => ({
  root: { backgroundColor: "transparent", flex: 1 },
  skeletonBody: { paddingHorizontal: 18, paddingTop: 96 },
  skeletonAvatar: { backgroundColor: profileNeon.panelRaised, borderColor: profileNeon.border, borderRadius: 56, borderWidth: 1, height: 112, width: 112 },
  skeletonName: { backgroundColor: profileNeon.panelRaised, borderRadius: 8, height: 26, marginTop: 16, width: "58%" },
  skeletonHandle: { backgroundColor: profileNeon.panel, borderRadius: 6, height: 14, marginTop: 10, width: "36%" },
  skeletonStats: { backgroundColor: profileNeon.panel, borderColor: profileNeon.border, borderRadius: profileNeon.radius.panel, borderWidth: 1, height: 74, marginTop: 22 },
  skeletonActions: { flexDirection: "row", gap: 8, marginTop: 16 },
  skeletonAction: { backgroundColor: profileNeon.panel, borderColor: profileNeon.border, borderRadius: profileNeon.radius.action, borderWidth: 1, flex: 1, height: 48 },
  actionMessage: {
    backgroundColor: colors.signalSoft,
    borderColor: colors.border,
    borderRadius: 10,
    borderWidth: 1,
    color: colors.accentStrong,
    fontSize: 13,
    marginBottom: 10,
    padding: 10
  },
  about: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    gap: 8,
    marginTop: 12,
    padding: 14
  },
  aboutBody: {
    color: colors.text,
    fontSize: 15,
    lineHeight: 22
  },
  aboutMeta: {
    color: colors.muted,
    fontSize: 13,
    lineHeight: 19
  },
  aboutTitle: {
    color: colors.text,
    fontSize: 18,
    fontWeight: "900"
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
    marginTop: 10,
    textAlign: "center"
  },
  content: {
    paddingBottom: 32
  },
  section: {
    paddingHorizontal: 16,
    paddingTop: 4
  },
  gridRow: { gap: 2, paddingHorizontal: 2 },
  gridTile: { aspectRatio: 1, backgroundColor: colors.surface, flex: 1, marginBottom: 2, maxWidth: "33.333%", overflow: "hidden" },
  gridImage: { height: "100%", width: "100%" },
  textTile: { alignItems: "center", backgroundColor: "#0D2030", flex: 1, justifyContent: "center", padding: 10 },
  textTileCopy: { color: colors.text, fontSize: 13, fontWeight: "800", lineHeight: 17, textAlign: "center" },
  tileSignals: { alignItems: "center", flexDirection: "row", gap: 4, left: 7, position: "absolute", right: 7, top: 7 },
  duration: { color: "#fff", fontSize: 10, fontWeight: "900", marginLeft: "auto", textShadowColor: "#000", textShadowRadius: 4 },
  loadingMore: { color: colors.muted, padding: 16, textAlign: "center" },
  empty: {
    color: colors.muted,
    padding: 20,
    textAlign: "center"
  },
  error: {
    color: colors.danger,
    fontSize: 13,
    marginBottom: 10
  },
  errorTitle: {
    color: colors.text,
    fontSize: 20,
    fontWeight: "900"
  },
  header: {
    marginBottom: 12
  },
  list: {
    backgroundColor: "transparent",
    flex: 1
  },
  offline: {
    color: colors.warning,
    fontSize: 13,
    marginBottom: 10
  },
  retryButton: {
    backgroundColor: colors.accent,
    borderRadius: 8,
    marginTop: 16,
    paddingHorizontal: 16,
    paddingVertical: 11
  },
  retryButtonText: {
    color: colors.background,
    fontWeight: "900"
  },
  tab: {
    alignItems: "center",
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    flex: 1,
    minHeight: 40,
    justifyContent: "center"
  },
  tabActive: {
    backgroundColor: "rgba(37, 208, 167, 0.14)",
    borderColor: colors.accent
  },
  tabText: {
    color: colors.muted,
    fontSize: 13,
    fontWeight: "900"
  },
  tabTextActive: {
    color: colors.accent
  },
  tabs: {
    flexDirection: "row",
    gap: 8,
    marginTop: 12
  },
  webButton: {
    backgroundColor: "transparent",
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    marginTop: 16,
    paddingHorizontal: 16,
    paddingVertical: 11
  },
  webButtonText: {
    color: colors.accentStrong,
    fontWeight: "900"
  },
  webLink: {
    marginTop: 6
  },
  webLinkText: {
    color: colors.accentStrong,
    fontWeight: "900"
  }
}));
