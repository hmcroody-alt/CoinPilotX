import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { useEffect, useMemo, useRef, useState } from "react";
import { AccessibilityInfo, Animated, FlatList, Pressable, RefreshControl, StyleSheet, Text, View } from "react-native";
import { deletePost, listFeed, PulsePost, pulsePostUrl, reactToPost, repostPost, savablePostId } from "../api/feed";
import { describeDeleteError } from "../api/deleteErrors";
import { getMyProfile, getPublicProfile, listPublicProfilePosts, loadCachedProfile, profileErrorState, PulseProfile, toggleProfileFollow } from "../api/profile";
import { MessengerUserSearchResult, openDirectConversation } from "../api/messenger";
import { NativeProfileTarget, profileNavigationParams, profileTargetFromAuthor, resolveProfileTarget } from "../api/profileTarget";
import { PostCard } from "../components/PostCard";
import { peekSaveState } from "../social/savedStore";
import { setSaved } from "../social/useSaveAction";
import { ProfileHeader, ProfileModuleKey, ProfileStatKey } from "../components/ProfileHeader";
import { LogiNexusScreenShell, LogiNexusStatePanel } from "../components/Screen";
import { invalidateNativeSync } from "../core/eventSync";
import { useBottomNavSurface } from "../navigation/BottomNavVisibility";
import { registerRefreshDestination } from "../navigation/refreshCoordinator";
import { RootStackParamList } from "../navigation/types";
import { actionKey, useSocialActionGuard } from "../social/actionGuard";
import { colors } from "../theme/colors";
import { sharePulseObject } from "../sharing/nativeShare";

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
  const [profile, setProfile] = useState<PulseProfile | null>(null);
  const [posts, setPosts] = useState<PulsePost[]>([]);
  const [tab, setTab] = useState<TabKey>("posts");
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [offline, setOffline] = useState(false);
  const [errorState, setErrorState] = useState<ReturnType<typeof profileErrorState> | null>(null);
  const [actionMessage, setActionMessage] = useState("");
  const [followBusy, setFollowBusy] = useState(false);
  const refreshingRef = useRef(false);
  // Replaces a `busyPostId` scalar. A scalar can mark at most one card, so acting
  // on one post greyed out every other card's buttons, and the handlers guarded
  // themselves by reading state that React had not committed yet. The guard locks
  // per action+id in a ref, which is what actually stops the second tap.
  const guard = useSocialActionGuard();

  const visiblePosts = useMemo(() => (tab === "media" ? posts.filter((post) => post.media?.length) : posts), [posts, tab]);

  async function load(mode: "initial" | "refresh" = "initial") {
    setErrorState(null);
    setOffline(false);
    if (mode === "initial") setLoading(true);
    if (mode === "refresh") {
      if (refreshingRef.current) return;
      refreshingRef.current = true;
      setRefreshing(true);
    }
    try {
      if (owner) {
        const me = await getMyProfile();
        setProfile(me);
        const key = me.public_player_id || me.username || "";
        const feed = key ? await listFeed({ feed: "for_you", profile: key, limit: 20, offset: 0 }) : { posts: [] };
        setPosts(feed.posts || []);
      } else {
        const [publicProfile, feedPosts] = await Promise.all([getPublicProfile(profileTarget), listPublicProfilePosts(profileTarget)]);
        setPosts(feedPosts);
        setProfile(publicProfile);
      }
    } catch (loadError) {
      const mappedError = profileErrorState(loadError);
      const cached = await loadCachedProfile(owner ? "me" : profileTarget || profileKey);
      if (cached) {
        setProfile(cached);
        setOffline(Boolean(mappedError.offline || mappedError.retryable));
        setErrorState(mappedError.retryable ? mappedError : null);
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

  useEffect(() => {
    load("initial").catch(() => undefined);
  }, [profileKey]);

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
  }, [owner, profileKey]);

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

  function handleModule(key: ProfileModuleKey) {
    switch (key) {
      case "identity":
        return owner ? navigation?.navigate("ProfileEdit") : setTab("about");
      case "media":
        return setTab("media");
      case "music":
        return navigation?.navigate("Music", { title: "Music" });
      case "trust":
        return navigation?.navigate("TrustCenter", { title: "Trust Center" });
      case "safety":
        return navigation?.navigate("SafetyHub", { title: "Safety Hub", section: profileKey ? "reports" : "overview" });
      case "pulse_dna":
        return navigation?.navigate("IntelligenceCenter", { title: "Pulse DNA" });
      case "achievements":
        return navigation?.navigate("GrowthCenter", { contentType: "profile", title: "Achievements" });
      case "activity":
        return navigation?.navigate("ActivityInbox", { title: "Activity" });
      case "collections":
        return navigation?.navigate("Saved");
      case "communities":
        return navigation?.navigate("Tabs", { screen: "Groups" });
      case "marketplace":
        return navigation?.navigate("Tabs", { screen: "Marketplace" });
      case "events":
        return navigation?.navigate("Events", { mode: "events", title: "Events" });
      case "business":
        // Business is the single entry point for running a business: the store,
        // seller marketplace tools and advertising all live inside Business OS.
        // Consumer marketplace browsing stays on the Marketplace tab above.
        return navigation?.navigate("BusinessOs", { title: "Business OS" });
      case "memories":
        return navigation?.navigate("Tabs", { screen: "Status" });
      default:
        return undefined;
    }
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

  if (loading && !profile) {
    return (
      <LogiNexusScreenShell>
        <LogiNexusStatePanel state="loading" title="Loading profile" body="Resolving identity, trust, and creator signals." loading />
      </LogiNexusScreenShell>
    );
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
    <Animated.FlatList
      ref={listRef}
      style={styles.list}
      contentContainerStyle={[styles.content, dock.contentPadding]}
      data={tab === "about" ? [] : visiblePosts}
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
          />
          <View style={styles.section}>
            {offline ? <Text style={styles.offline}>Showing saved profile</Text> : null}
            {errorState ? <Text style={styles.error}>{errorState.body}</Text> : null}
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
      ListEmptyComponent={tab === "about" ? null : <Text style={styles.empty}>{tab === "media" ? "No media posts loaded." : "No profile posts loaded."}</Text>}
      renderItem={({ item }) => (
        <View style={styles.postWrap}>
          <PostCard
            post={item}
            busy={guard.isItemBusy(item.id)}
            onOpen={(post) => navigation?.navigate("PostDetail", { postId: post.id, title: "Post" })}
            onReact={handleReact}
            onSave={handleSave}
            onRepost={handleRepost}
            onComment={(post) => navigation?.navigate("PostDetail", { postId: post.id, title: "Comments" })}
            onShare={(post) => sharePulseObject({
              kind: "post",
              url: pulsePostUrl(post.id),
              title: post.title || "PulseSoc post",
              description: post.body || post.text || post.content,
              author: post.author?.display_name || post.author?.name || post.author?.username || post.author_name,
              previewImageUrl: post.thumbnail_url || post.image_url
            }).catch(() => undefined)}
            onDelete={owner ? handleDeletePost : undefined}
            onAuthorPress={(post) => {
              const target = profileTargetFromAuthor(post.author as Record<string, unknown> | undefined, post as unknown as Record<string, unknown>);
              const params = profileNavigationParams(target, post.author?.display_name || "Profile");
              if (params) navigation?.navigate("ProfileDetail", params);
            }}
          />
        </View>
      )}
      onScroll={onScroll}
      onScrollBeginDrag={dock.handlers.onScrollBeginDrag}
      scrollEventThrottle={dock.handlers.scrollEventThrottle}
    />
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

const styles = StyleSheet.create({
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
    backgroundColor: colors.background,
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
  postWrap: {
    paddingHorizontal: 16
  },
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
    backgroundColor: colors.background,
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
});
