import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { useEffect, useMemo, useState } from "react";
import { FlatList, Pressable, RefreshControl, Share, StyleSheet, Text, View } from "react-native";
import { deletePost, listFeed, PulsePost, pulsePostUrl, reactToPost, savePost } from "../api/feed";
import { describeDeleteError } from "../api/deleteErrors";
import { getMyProfile, getPublicProfile, listPublicProfilePosts, loadCachedProfile, profileErrorState, PulseProfile, toggleProfileFollow } from "../api/profile";
import { MessengerUserSearchResult, openDirectConversation } from "../api/messenger";
import { NativeProfileTarget, profileNavigationParams, profileTargetFromAuthor, resolveProfileTarget } from "../api/profileTarget";
import { PostCard } from "../components/PostCard";
import { ProfileHeader } from "../components/ProfileHeader";
import { LogiNexusScreenShell, LogiNexusStatePanel } from "../components/Screen";
import { invalidateNativeSync } from "../core/eventSync";
import { useBottomNavScrollVisibility } from "../navigation/BottomNavVisibility";
import { RootStackParamList } from "../navigation/types";
import { colors } from "../theme/colors";

type Props = Partial<NativeStackScreenProps<RootStackParamList, "ProfileDetail">>;
type TabKey = "posts" | "media" | "about";

export function ProfileScreen({ route, navigation }: Props) {
  const bottomNavScroll = useBottomNavScrollVisibility();
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
  const [busyPostId, setBusyPostId] = useState<number | null>(null);

  const visiblePosts = useMemo(() => (tab === "media" ? posts.filter((post) => post.media?.length) : posts), [posts, tab]);

  async function load(mode: "initial" | "refresh" = "initial") {
    setErrorState(null);
    setOffline(false);
    if (mode === "initial") setLoading(true);
    if (mode === "refresh") setRefreshing(true);
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
    }
  }

  useEffect(() => {
    load("initial").catch(() => undefined);
  }, [profileKey]);

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

  function updateProfilePost(id: number, patch: Partial<PulsePost>) {
    setPosts((current) => current.map((item) => (item.id === id ? { ...item, ...patch } : item)));
  }

  async function handleSave(post: PulsePost) {
    if (busyPostId === post.id) return;
    const wasSaved = Boolean(post.saved ?? post.is_saved);
    setBusyPostId(post.id);
    updateProfilePost(post.id, { saved: !wasSaved });
    try {
      const result = await savePost(post.id);
      updateProfilePost(post.id, { saved: Boolean(result.saved ?? result.is_saved ?? !wasSaved) });
    } catch {
      updateProfilePost(post.id, { saved: wasSaved });
    } finally {
      setBusyPostId(null);
    }
  }

  async function handleReact(post: PulsePost, reactionType: string) {
    if (busyPostId === post.id) return;
    const previous = post.viewer_reaction || "";
    const removing = previous === reactionType;
    setBusyPostId(post.id);
    updateProfilePost(post.id, { viewer_reaction: removing ? "" : reactionType });
    try {
      const result = await reactToPost(post.id, reactionType);
      updateProfilePost(post.id, {
        viewer_reaction: String(result.viewer_reaction ?? (removing ? "" : reactionType)),
        reaction_counts: result.reaction_counts ?? post.reaction_counts
      });
    } catch {
      updateProfilePost(post.id, { viewer_reaction: previous });
    } finally {
      setBusyPostId(null);
    }
  }

  async function handleDeletePost(post: PulsePost) {
    if (busyPostId === post.id) return;
    setBusyPostId(post.id);
    try {
      await deletePost(post.id);
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
    } catch (deleteError) {
      setActionMessage(describeDeleteError(deleteError, "Post"));
    } finally {
      setBusyPostId(null);
    }
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
    <FlatList
      style={styles.list}
      contentContainerStyle={styles.content}
      data={tab === "about" ? [] : visiblePosts}
      keyExtractor={(item) => String(item.id)}
      refreshControl={<RefreshControl refreshing={refreshing} tintColor={colors.accent} onRefresh={() => load("refresh").catch(() => undefined)} />}
      ListHeaderComponent={
        <View style={styles.header}>
          {offline ? <Text style={styles.offline}>Showing saved profile</Text> : null}
          {errorState ? <Text style={styles.error}>{errorState.body}</Text> : null}
          {actionMessage ? <Text accessibilityLiveRegion="polite" style={styles.actionMessage}>{actionMessage}</Text> : null}
          <ProfileHeader
            profile={profile}
            publicKey={profileKey}
            owner={owner}
            followBusy={followBusy}
            onEdit={() => navigation?.navigate("ProfileEdit")}
            onCustomize={() => navigation?.navigate("ProfileEdit")}
            onGrowth={() => navigation?.navigate("GrowthCenter", { contentType: "profile", title: "Grow Profile" })}
            onSafety={() => navigation?.navigate("SafetyHub", { title: "Safety Hub", section: profileKey ? "reports" : "overview" })}
            onMessage={() => messageProfile().catch(() => undefined)}
            onFollow={() => followProfile().catch(() => undefined)}
            onRefresh={() => load("refresh").catch(() => undefined)}
          />
          <View style={styles.tabs}>
            <TabButton label="Posts" value="posts" active={tab} onPress={setTab} />
            <TabButton label="Media" value="media" active={tab} onPress={setTab} />
            <TabButton label="About" value="about" active={tab} onPress={setTab} />
          </View>
          {tab === "about" ? <AboutPanel profile={profile} owner={owner} onVerification={() => navigation?.navigate("VerificationCenter", { title: "Verification Center" })} onSafety={() => navigation?.navigate("SafetyHub", { title: "Safety Hub", section: profileKey ? "reports" : "overview" })} onSellerStore={() => navigation?.navigate("SellerStore", { title: "Seller / Store" })} /> : null}
        </View>
      }
      ListEmptyComponent={tab === "about" ? null : <Text style={styles.empty}>{tab === "media" ? "No media posts loaded." : "No profile posts loaded."}</Text>}
      renderItem={({ item }) => (
        <PostCard
          post={item}
          busy={busyPostId === item.id}
          onOpen={(post) => navigation?.navigate("PostDetail", { postId: post.id, title: "Post" })}
          onReact={handleReact}
          onSave={handleSave}
          onComment={(post) => navigation?.navigate("PostDetail", { postId: post.id, title: "Comments" })}
          onShare={(post) => Share.share({ message: pulsePostUrl(post.id) }).catch(() => undefined)}
          onDelete={owner ? handleDeletePost : undefined}
          onAuthorPress={(post) => {
            const target = profileTargetFromAuthor(post.author as Record<string, unknown> | undefined, post as unknown as Record<string, unknown>);
            const params = profileNavigationParams(target, post.author?.display_name || "Profile");
            if (params) navigation?.navigate("ProfileDetail", params);
          }}
        />
      )}
      onScroll={bottomNavScroll.onScroll}
      onScrollBeginDrag={bottomNavScroll.onScrollBeginDrag}
      scrollEventThrottle={bottomNavScroll.scrollEventThrottle}
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
    padding: 16,
    paddingBottom: 32
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
