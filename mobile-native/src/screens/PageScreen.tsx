import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { useCallback, useEffect, useState } from "react";
import {
  ActivityIndicator,
  FlatList,
  Image,
  Pressable,
  RefreshControl,
  Share,
  StyleSheet,
  Text,
  View
} from "react-native";
import {
  getPage,
  getPageByHandle,
  listPagePosts,
  pageTypeLabel,
  PulsePage,
  togglePageFollow
} from "../api/pages";
import { PULSE_API_BASE_URL } from "../api/config";
import type { PulsePost } from "../api/feed";
import { RootStackParamList } from "../navigation/types";
import { colors } from "../theme/colors";
import { createThemedStyles } from "../theme/themedStyles";

type Props = NativeStackScreenProps<RootStackParamList, "Page">;

/**
 * The public page surface — one component for every page type. The tab set is
 * SERVER-decided per type (artist: posts/music/videos/events/merch/about;
 * business: home/services/shop/about; …) and rendered as delivered.
 *
 * Real metrics only: follower and post counts come from the server's measured
 * counts. Tabs without a canonical data source render an honest empty state —
 * never invented numbers, never placeholder reviews.
 */
export function PageScreen({ route, navigation }: Props) {
  const params = route.params || {};
  const [page, setPage] = useState<PulsePage | null>(null);
  const [posts, setPosts] = useState<PulsePost[]>([]);
  const [tab, setTab] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [followBusy, setFollowBusy] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setError("");
    try {
      const loaded = params.pageId
        ? await getPage(params.pageId)
        : await getPageByHandle(params.handle || "");
      setPage(loaded);
      navigation.setOptions({ title: loaded.name });
      const defaultTab = loaded.tabs.includes("posts") ? "posts" : loaded.tabs[0] || "about";
      setTab((current) => (current && loaded.tabs.includes(current) ? current : defaultTab));
      const feed = await listPagePosts(loaded.id, { limit: 20 });
      setPosts(feed.posts);
    } catch {
      setError("This page could not be loaded.");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [params.pageId, params.handle, navigation]);

  useEffect(() => {
    load();
  }, [load]);

  async function onFollow() {
    if (!page || followBusy) return;
    setFollowBusy(true);
    try {
      const result = await togglePageFollow(page.id);
      setPage({
        ...page,
        followers_count: result.followers_count,
        viewer: { role: page.viewer?.role || null, following: result.following }
      });
    } catch {
      // keep prior state; server remains authoritative
    } finally {
      setFollowBusy(false);
    }
  }

  async function onShare() {
    if (!page) return;
    await Share.share({ message: `${page.name} on PulseSoc — ${PULSE_API_BASE_URL}/pulse/pages/@${page.handle}` }).catch(() => undefined);
  }

  if (loading) {
    return (
      <View style={[styles.root, styles.center]}>
        <ActivityIndicator color={colors.accent} size="large" />
      </View>
    );
  }
  if (!page) {
    return (
      <View style={[styles.root, styles.center]}>
        <Text style={styles.error}>{error || "Page not found."}</Text>
      </View>
    );
  }

  const following = Boolean(page.viewer?.following);
  const isTeam = Boolean(page.viewer?.role);

  const header = (
    <View>
      {page.cover_url ? <Image source={{ uri: page.cover_url }} style={styles.cover} /> : <View style={styles.cover} />}
      <View style={styles.hero}>
        {page.avatar_url ? (
          <Image source={{ uri: page.avatar_url }} style={styles.avatar} />
        ) : (
          <View style={[styles.avatar, styles.avatarFallback]}>
            <Text style={styles.avatarInitial}>{page.name.slice(0, 1).toUpperCase()}</Text>
          </View>
        )}
        <View style={styles.heroText}>
          <View style={styles.nameRow}>
            <Text style={styles.name}>{page.name}</Text>
            {page.verified ? <Text style={styles.verified}>Verified</Text> : null}
          </View>
          <Text style={styles.handle}>@{page.handle} · {pageTypeLabel(page.page_type)}</Text>
          {page.category ? <Text style={styles.category}>{page.category}</Text> : null}
          <Text style={styles.counts}>
            {page.followers_count} followers · {page.posts_count} posts
          </Text>
        </View>
      </View>

      <View style={styles.actions}>
        <Pressable
          accessibilityRole="button"
          style={[styles.actionPrimary, following && styles.actionFollowing]}
          disabled={followBusy}
          onPress={onFollow}
        >
          <Text style={[styles.actionPrimaryText, following && styles.actionFollowingText]}>
            {following ? "Following" : "Follow"}
          </Text>
        </Pressable>
        <Pressable accessibilityRole="button" style={styles.actionSecondary} onPress={onShare}>
          <Text style={styles.actionSecondaryText}>Share</Text>
        </Pressable>
        {isTeam ? (
          <Pressable
            accessibilityRole="button"
            style={styles.actionSecondary}
            onPress={() => navigation.navigate("PagesHub", { focusPageId: page.id })}
          >
            <Text style={styles.actionSecondaryText}>Manage</Text>
          </Pressable>
        ) : null}
      </View>

      <View style={styles.tabBar}>
        {page.tabs.map((tabKey) => (
          <Pressable
            key={tabKey}
            accessibilityRole="button"
            accessibilityState={{ selected: tab === tabKey }}
            style={[styles.tabButton, tab === tabKey && styles.tabActive]}
            onPress={() => setTab(tabKey)}
          >
            <Text style={[styles.tabText, tab === tabKey && styles.tabTextActive]}>
              {tabKey.charAt(0).toUpperCase() + tabKey.slice(1)}
            </Text>
          </Pressable>
        ))}
      </View>
    </View>
  );

  function renderTabBody(page: PulsePage) {
    if (tab === "posts" || tab === "home") {
      if (!posts.length) {
        return <Text style={styles.empty}>No posts yet.</Text>;
      }
      return null; // posts render in the FlatList below
    }
    if (tab === "about") {
      return (
        <View style={styles.aboutCard}>
          {page.description ? <Text style={styles.aboutText}>{page.description}</Text> : null}
          {page.genre ? <Text style={styles.aboutMeta}>Genre: {page.genre}</Text> : null}
          {page.website ? <Text style={styles.aboutMeta}>Website: {page.website}</Text> : null}
          {page.email ? <Text style={styles.aboutMeta}>Contact: {page.email}</Text> : null}
          {page.location ? <Text style={styles.aboutMeta}>Location: {page.location}</Text> : null}
          {!page.description && !page.website && !page.email && !page.location ? (
            <Text style={styles.empty}>Nothing here yet.</Text>
          ) : null}
        </View>
      );
    }
    if (tab === "music") {
      return (
        <Pressable
          accessibilityRole="button"
          style={styles.linkCard}
          onPress={() => navigation.navigate("Music", { artist: undefined, title: page.name })}
        >
          <Text style={styles.linkCardText}>Listen in PulseSoc Music</Text>
        </Pressable>
      );
    }
    if (tab === "merch" || tab === "shop") {
      return (
        <Pressable
          accessibilityRole="button"
          style={styles.linkCard}
          onPress={() => navigation.navigate("Tabs", { screen: "Marketplace" })}
        >
          <Text style={styles.linkCardText}>Browse in Marketplace</Text>
        </Pressable>
      );
    }
    if (tab === "events") {
      return (
        <Pressable
          accessibilityRole="button"
          style={styles.linkCard}
          onPress={() => navigation.navigate("Events", { title: page.name })}
        >
          <Text style={styles.linkCardText}>See events</Text>
        </Pressable>
      );
    }
    // services / reviews / videos / menu — no canonical source wired yet:
    // honest empty state, never fabricated content.
    return <Text style={styles.empty}>Nothing here yet.</Text>;
  }

  const showPosts = tab === "posts" || tab === "home";

  return (
    <FlatList
      style={styles.root}
      data={showPosts ? posts : []}
      keyExtractor={(item) => String(item.id || item.post_id)}
      ListHeaderComponent={header}
      ListFooterComponent={<View style={styles.footer}>{renderTabBody(page)}</View>}
      refreshControl={
        <RefreshControl
          refreshing={refreshing}
          onRefresh={() => {
            setRefreshing(true);
            load();
          }}
          tintColor={colors.accent}
        />
      }
      renderItem={({ item }) => (
        <Pressable
          accessibilityRole="button"
          style={styles.postCard}
          onPress={() => navigation.navigate("PostDetail", { postId: Number(item.id || item.post_id) })}
        >
          {item.title ? <Text style={styles.postTitle}>{item.title}</Text> : null}
          <Text style={styles.postBody} numberOfLines={6}>
            {item.body || item.text || item.content || ""}
          </Text>
          {item.created_at ? <Text style={styles.postMeta}>{item.created_at}</Text> : null}
        </Pressable>
      )}
    />
  );
}

const styles = createThemedStyles(() => ({
  aboutCard: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 10,
    borderWidth: 1,
    gap: 6,
    margin: 16,
    padding: 14
  },
  aboutMeta: {
    color: colors.muted,
    fontSize: 13
  },
  aboutText: {
    color: colors.text,
    fontSize: 14,
    lineHeight: 21
  },
  actionFollowing: {
    backgroundColor: colors.surface,
    borderColor: colors.accent,
    borderWidth: 1
  },
  actionFollowingText: {
    color: colors.accent
  },
  actionPrimary: {
    alignItems: "center",
    backgroundColor: colors.accent,
    borderRadius: 8,
    flex: 1,
    justifyContent: "center",
    minHeight: 40
  },
  actionPrimaryText: {
    color: colors.background,
    fontWeight: "900"
  },
  actionSecondary: {
    alignItems: "center",
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    justifyContent: "center",
    minHeight: 40,
    paddingHorizontal: 16
  },
  actionSecondaryText: {
    color: colors.text,
    fontWeight: "800"
  },
  actions: {
    flexDirection: "row",
    gap: 8,
    paddingHorizontal: 16,
    paddingTop: 12
  },
  avatar: {
    borderColor: colors.background,
    borderRadius: 40,
    borderWidth: 3,
    height: 80,
    width: 80
  },
  avatarFallback: {
    alignItems: "center",
    backgroundColor: colors.surfaceRaised,
    justifyContent: "center"
  },
  avatarInitial: {
    color: colors.accent,
    fontSize: 30,
    fontWeight: "900"
  },
  category: {
    color: colors.muted,
    fontSize: 13
  },
  center: {
    alignItems: "center",
    justifyContent: "center"
  },
  counts: {
    color: colors.text,
    fontSize: 13,
    fontWeight: "700",
    marginTop: 2
  },
  cover: {
    backgroundColor: colors.surfaceRaised,
    height: 130,
    width: "100%"
  },
  empty: {
    color: colors.muted,
    padding: 24,
    textAlign: "center"
  },
  error: {
    color: colors.danger,
    fontWeight: "800"
  },
  footer: {
    paddingBottom: 48
  },
  handle: {
    color: colors.accent,
    fontSize: 13,
    fontWeight: "700"
  },
  hero: {
    flexDirection: "row",
    gap: 14,
    marginTop: -28,
    paddingHorizontal: 16
  },
  linkCard: {
    alignItems: "center",
    backgroundColor: colors.surface,
    borderColor: colors.accent,
    borderRadius: 10,
    borderWidth: 1,
    margin: 16,
    padding: 16
  },
  linkCardText: {
    color: colors.accent,
    fontSize: 14,
    fontWeight: "900"
  },
  heroText: {
    flex: 1,
    justifyContent: "flex-end"
  },
  name: {
    color: colors.text,
    fontSize: 20,
    fontWeight: "900"
  },
  nameRow: {
    alignItems: "center",
    flexDirection: "row",
    gap: 8
  },
  postBody: {
    color: colors.text,
    fontSize: 14,
    lineHeight: 21
  },
  postCard: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 10,
    borderWidth: 1,
    gap: 6,
    marginHorizontal: 16,
    marginTop: 10,
    padding: 14
  },
  postMeta: {
    color: colors.muted,
    fontSize: 11
  },
  postTitle: {
    color: colors.text,
    fontSize: 15,
    fontWeight: "900"
  },
  root: {
    backgroundColor: colors.background,
    flex: 1
  },
  tabActive: {
    borderBottomColor: colors.accent,
    borderBottomWidth: 2
  },
  tabBar: {
    borderBottomColor: colors.border,
    borderBottomWidth: 1,
    flexDirection: "row",
    flexWrap: "wrap",
    marginTop: 14,
    paddingHorizontal: 8
  },
  tabButton: {
    paddingHorizontal: 12,
    paddingVertical: 10
  },
  tabText: {
    color: colors.muted,
    fontSize: 13,
    fontWeight: "800"
  },
  tabTextActive: {
    color: colors.accent
  },
  verified: {
    backgroundColor: colors.signalDim,
    borderRadius: 6,
    color: colors.accent,
    fontSize: 10,
    fontWeight: "900",
    overflow: "hidden",
    paddingHorizontal: 6,
    paddingVertical: 2
  }
}));
