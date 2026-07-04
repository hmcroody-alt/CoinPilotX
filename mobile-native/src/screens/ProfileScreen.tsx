import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { useEffect, useMemo, useState } from "react";
import { ActivityIndicator, FlatList, Linking, Pressable, RefreshControl, StyleSheet, Text, View } from "react-native";
import { listFeed, PulsePost } from "../api/feed";
import { getMyProfile, listPublicProfilePosts, loadCachedProfile, normalizeProfile, profileWebUrl, PulseProfile } from "../api/profile";
import { PostCard } from "../components/PostCard";
import { ProfileHeader } from "../components/ProfileHeader";
import { RootStackParamList } from "../navigation/types";
import { colors } from "../theme/colors";

type Props = Partial<NativeStackScreenProps<RootStackParamList, "ProfileDetail">>;
type TabKey = "posts" | "media" | "about";

export function ProfileScreen({ route, navigation }: Props) {
  const profileKey = route?.params?.profileKey || "";
  const owner = !profileKey;
  const [profile, setProfile] = useState<PulseProfile | null>(null);
  const [posts, setPosts] = useState<PulsePost[]>([]);
  const [tab, setTab] = useState<TabKey>("posts");
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [offline, setOffline] = useState(false);
  const [error, setError] = useState("");

  const visiblePosts = useMemo(() => (tab === "media" ? posts.filter((post) => post.media?.length) : posts), [posts, tab]);

  async function load(mode: "initial" | "refresh" = "initial") {
    setError("");
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
        const feedPosts = await listPublicProfilePosts(profileKey);
        setPosts(feedPosts);
        setProfile(profileFromPublicPosts(profileKey, feedPosts));
      }
    } catch (loadError) {
      const cached = owner ? await loadCachedProfile("me") : null;
      if (cached) {
        setProfile(cached);
        setOffline(true);
      } else if (!owner && posts.length) {
        setOffline(true);
      } else {
        setError(loadError instanceof Error ? loadError.message : "Profile could not load.");
      }
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }

  useEffect(() => {
    load("initial").catch(() => undefined);
  }, [profileKey]);

  if (loading && !profile) {
    return (
      <View style={styles.center}>
        <ActivityIndicator color={colors.accent} />
        <Text style={styles.centerText}>Loading profile</Text>
      </View>
    );
  }

  if (!profile) {
    return (
      <View style={styles.center}>
        <Text style={styles.errorTitle}>Profile unavailable</Text>
        <Text style={styles.centerText}>{error || "PulseSoc could not load this profile."}</Text>
        {profileKey ? (
          <Pressable style={styles.webButton} onPress={() => Linking.openURL(profileWebUrl(profileKey)).catch(() => undefined)}>
            <Text style={styles.webButtonText}>Open Web Profile</Text>
          </Pressable>
        ) : null}
      </View>
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
          {error ? <Text style={styles.error}>{error}</Text> : null}
          <ProfileHeader
            profile={profile}
            publicKey={profileKey}
            owner={owner}
            onEdit={() => navigation?.navigate("ProfileEdit")}
            onPremium={() => navigation?.navigate("Premium")}
            onGrowth={() => navigation?.navigate("GrowthCenter", { contentType: "profile", title: "Grow Profile" })}
            onRefresh={() => load("refresh").catch(() => undefined)}
          />
          <View style={styles.tabs}>
            <TabButton label="Posts" value="posts" active={tab} onPress={setTab} />
            <TabButton label="Media" value="media" active={tab} onPress={setTab} />
            <TabButton label="About" value="about" active={tab} onPress={setTab} />
          </View>
          {tab === "about" ? <AboutPanel profile={profile} profileKey={profileKey} owner={owner} /> : null}
        </View>
      }
      ListEmptyComponent={tab === "about" ? null : <Text style={styles.empty}>{tab === "media" ? "No media posts loaded." : "No profile posts loaded."}</Text>}
      renderItem={({ item }) => (
        <PostCard
          post={item}
          onOpen={(post) => navigation?.navigate("PostDetail", { postId: post.id, title: "Post" })}
          onAuthorPress={(post) => {
            const key = post.author?.public_player_id || post.author?.username || "";
            if (key) navigation?.navigate("ProfileDetail", { profileKey: key, title: post.author?.display_name || "Profile" });
          }}
        />
      )}
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

function AboutPanel({ profile, profileKey, owner }: { profile: PulseProfile; profileKey: string; owner: boolean }) {
  return (
    <View style={styles.about}>
      <Text style={styles.aboutTitle}>About</Text>
      <Text style={styles.aboutBody}>{profile.bio || "No bio yet."}</Text>
      <Text style={styles.aboutMeta}>Links: {profile.social_links || "None loaded"}</Text>
      <Text style={styles.aboutMeta}>Expertise: {profile.expertise_tags || "None loaded"}</Text>
      <Text style={styles.aboutMeta}>Visibility: {profile.profile_visibility || "public"}</Text>
      <Text style={styles.aboutMeta}>Status: {profile.account_status || "active"}</Text>
      <Pressable style={styles.webLink} onPress={() => Linking.openURL(profileWebUrl(owner ? undefined : profileKey)).catch(() => undefined)}>
        <Text style={styles.webLinkText}>Open full PulseSoc profile</Text>
      </Pressable>
    </View>
  );
}

function profileFromPublicPosts(profileKey: string, posts: PulsePost[]) {
  const first = posts[0];
  const author = first?.author || {};
  return normalizeProfile({
    user_id: Number(author.user_id || author.id || 0),
    display_name: author.display_name || author.name || profileKey,
    username: author.username || author.handle || "",
    public_player_id: author.public_player_id || profileKey,
    avatar_url: author.avatar_url || "",
    premium_status: author.premium || author.premium_verified ? "active" : "",
    post_count: posts.length,
    media_count: posts.filter((post) => post.media?.length).length,
    bio: posts.length ? "" : "Open the full PulseSoc profile for details."
  });
}

const styles = StyleSheet.create({
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
    backgroundColor: colors.accent,
    borderRadius: 8,
    marginTop: 16,
    paddingHorizontal: 16,
    paddingVertical: 11
  },
  webButtonText: {
    color: colors.background,
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
