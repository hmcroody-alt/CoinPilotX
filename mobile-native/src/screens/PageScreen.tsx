import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { useCallback, useEffect, useRef, useState } from "react";
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
  listPageMusic,
  listPagePosts,
  PageTrack,
  pageTypeLabel,
  PulsePage,
  togglePageFollow
} from "../api/pages";
import { searchMarketplace, type MarketplaceListing } from "../api/marketplace";
import { PULSE_API_BASE_URL } from "../api/config";
import type { PulsePost } from "../api/feed";
import { RootStackParamList } from "../navigation/types";
import { colors } from "../theme/colors";
import { createThemedStyles } from "../theme/themedStyles";

type Props = NativeStackScreenProps<RootStackParamList, "Page">;

type ModuleState = "idle" | "loading" | "ready" | "error";

/**
 * One tab's worth of lazily-loaded data, isolated from every other tab: a
 * module that fails leaves the rest of the presence usable.
 *
 * `key` identifies what is being loaded (presence id + retry count). The ref
 * holds it, not state — an effect that depended on the state it sets would
 * cancel its own in-flight request on the loading re-render and hang forever.
 */
function useLazyModule<T>(active: boolean, key: string, empty: T, load: () => Promise<T>) {
  const [value, setValue] = useState<{ state: ModuleState; data: T }>({ state: "idle", data: empty });
  const [attempt, setAttempt] = useState(0);
  const fetched = useRef("");
  const loadRef = useRef(load);
  loadRef.current = load;
  const emptyRef = useRef(empty);

  useEffect(() => {
    if (!active || !key) return;
    const token = `${key}:${attempt}`;
    if (fetched.current === token) return;
    fetched.current = token;
    let cancelled = false;
    setValue({ state: "loading", data: emptyRef.current });
    loadRef
      .current()
      .then((data) => {
        if (!cancelled) setValue({ state: "ready", data });
      })
      .catch(() => {
        if (!cancelled) setValue({ state: "error", data: emptyRef.current });
      });
    return () => {
      cancelled = true;
    };
  }, [active, key, attempt]);

  const retry = useCallback(() => setAttempt((n) => n + 1), []);
  return { state: value.state, data: value.data, retry };
}

// Stable identities: a fresh [] each render would reset every module.
const EMPTY_TRACKS: PageTrack[] = [];
const EMPTY_LISTINGS: MarketplaceListing[] = [];
const EMPTY_POSTS: PulsePost[] = [];
const SHOP_TABS = ["shop", "merch", "menu"];

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

  const pageKey = page ? String(page.id) : "";
  const sellerId = Number(page?.shop_seller_id || 0);
  const music = useLazyModule<PageTrack[]>(tab === "music", pageKey, EMPTY_TRACKS, () =>
    listPageMusic(Number(pageKey)).then((result) => result.tracks)
  );
  const shop = useLazyModule<MarketplaceListing[]>(
    SHOP_TABS.includes(tab) && sellerId > 0,
    `${pageKey}:${sellerId}`,
    EMPTY_LISTINGS,
    () => searchMarketplace({ sellerUserId: sellerId, limit: 24 }).then((result) => result.items)
  );
  const videos = useLazyModule<PulsePost[]>(tab === "videos", pageKey, EMPTY_POSTS, () =>
    listPagePosts(Number(pageKey), { limit: 24, kind: "videos" }).then((result) => result.posts)
  );

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

  function goManage() {
    navigation.navigate("PagesHub", { focusPageId: page!.id });
  }

  function goConnections() {
    navigation.navigate("PageConnections", { pageId: page!.id, title: page!.name });
  }

  function goEdit() {
    navigation.navigate("PageEdit", { pageId: page!.id, title: page!.name });
  }

  /**
   * An empty module, said differently depending on who is looking.
   *
   * A visitor is told what this page does not have and that is the end of it:
   * there is nothing for them to do about it, and a call to action they cannot
   * perform is noise dressed up as help.
   *
   * A team member gets the same sentence plus the one step that fills it,
   * because they are the only person who can take it. "No music yet." with no
   * route to adding music is how a page stays empty — the team sees the same
   * dead end as a stranger and has to go hunting for the screen that fixes it.
   *
   * `isTeam` comes from the server's `viewer.role`, and the CTA leads to a
   * screen that does its own per-role gating. It is not a permission check.
   */
  function emptyModule(
    headline: string,
    teamHint: string,
    // Nullable, not just optional: some callers decide at runtime that there is
    // no step left to offer (a shop is already connected, it simply has no
    // listings yet) and `null` says that out loud where a bare `undefined`
    // would read like an argument someone forgot.
    action?: { label: string; go: () => void } | null
  ) {
    if (!isTeam) {
      return <Text style={styles.empty}>{headline}</Text>;
    }
    return (
      <View style={styles.aboutCard}>
        <Text style={styles.empty}>{headline}</Text>
        <Text style={styles.aboutMeta}>{teamHint}</Text>
        {action ? (
          <Pressable accessibilityRole="button" style={styles.linkCard} onPress={action.go}>
            <Text style={styles.linkCardText}>{action.label}</Text>
          </Pressable>
        ) : null}
      </View>
    );
  }

  function renderTabBody(page: PulsePage) {
    if (tab === "posts" || tab === "home") {
      if (!posts.length) {
        return emptyModule(
          "No posts yet.",
          "Open Manage to write the first post as this presence.",
          { label: "Open Manage", go: goManage }
        );
      }
      return null; // posts render in the FlatList below
    }
    if (tab === "about") {
      // `genre` is deliberately not part of this test. It is set by the page
      // type rather than written by anyone, so a page whose only "about" is a
      // genre has still had nothing said about it, and the team should be asked
      // to write something.
      if (!page.description && !page.website && !page.email && !page.location) {
        return emptyModule(
          "Nothing here yet.",
          "A description, a link and a way to get in touch are what a visitor reads before deciding to follow.",
          { label: "Edit details", go: goEdit }
        );
      }
      return (
        <View style={styles.aboutCard}>
          {page.description ? <Text style={styles.aboutText}>{page.description}</Text> : null}
          {page.genre ? <Text style={styles.aboutMeta}>Genre: {page.genre}</Text> : null}
          {page.website ? <Text style={styles.aboutMeta}>Website: {page.website}</Text> : null}
          {page.email ? <Text style={styles.aboutMeta}>Contact: {page.email}</Text> : null}
          {page.location ? <Text style={styles.aboutMeta}>Location: {page.location}</Text> : null}
        </View>
      );
    }
    if (tab === "music") {
      if (music.state === "loading") {
        return <ActivityIndicator color={colors.accent} style={styles.moduleSpinner} />;
      }
      if (music.state === "error") {
        return moduleFailure(music.retry);
      }
      if (!music.data.length) {
        return emptyModule(
          "No music yet.",
          "Tracks are uploaded to an artist profile. Connect the one these releases live under and they appear here.",
          { label: "Connect an artist profile", go: goConnections }
        );
      }
      return (
        <View>
          {music.data.map((track) => (
            <Pressable
              key={track.id}
              accessibilityRole="button"
              style={styles.trackRow}
              onPress={() => navigation.navigate("Music", { trackId: track.id, title: track.title })}
            >
              {track.cover_art_url ? (
                <Image source={{ uri: track.cover_art_url }} style={styles.trackArt} />
              ) : (
                <View style={[styles.trackArt, styles.trackArtEmpty]} />
              )}
              <View style={styles.trackMeta}>
                <Text style={styles.trackTitle} numberOfLines={1}>
                  {track.title}
                </Text>
                <Text style={styles.trackSub} numberOfLines={1}>
                  {track.genre || track.artist}
                </Text>
              </View>
            </Pressable>
          ))}
        </View>
      );
    }
    if (SHOP_TABS.includes(tab)) {
      if (shop.state === "loading") {
        return <ActivityIndicator color={colors.accent} style={styles.moduleSpinner} />;
      }
      if (shop.state === "error") {
        return moduleFailure(shop.retry);
      }
      if (!shop.data.length) {
        return emptyModule(
          "Nothing for sale yet.",
          sellerId > 0
            ? "Listings you publish in Marketplace appear here."
            : "Connect the shop you already run and its listings appear here.",
          // Connecting a shop is a Connections action, so it goes to
          // Connections. Once one is connected there is nothing to offer here:
          // the listings are created in Marketplace, which is where the seller
          // already works, and a second door into it would be a second place to
          // keep in sync.
          sellerId > 0 ? null : { label: "Connect a shop", go: goConnections }
        );
      }
      return (
        <View>
          {shop.data.map((listing) => (
            <Pressable
              /*
                `listing.id` plainly, with no `|| listing.listing_id` fallback:
                these rows come from `searchMarketplace`, which runs every item
                through `normalizeMarketplaceListings` — that collapses the two
                spellings into one number and drops anything that resolves to 0.
                Re-deriving the id here would be a second, weaker copy of a rule
                that already has one home.
              */
              key={String(listing.id)}
              accessibilityRole="button"
              style={styles.trackRow}
              /*
                Straight to the product page, carrying the listing this screen
                already holds. This used to push `MarketplaceDetail`, which is
                the *browse* grid: it only forwards to the product if the
                listing happens to appear in an unfiltered 32-item search of the
                entire marketplace, and otherwise silently drops the buyer into
                the global marketplace. For a small artist's merch that is the
                normal case, so tapping an item on a page reliably lost it.
              */
              onPress={() =>
                navigation.navigate("MarketplaceProduct", {
                  listingId: listing.id,
                  listing,
                  title: listing.title
                })
              }
            >
              {listing.cover_image_url || listing.image_url ? (
                <Image
                  source={{ uri: listing.cover_image_url || listing.image_url }}
                  style={styles.trackArt}
                />
              ) : (
                <View style={[styles.trackArt, styles.trackArtEmpty]} />
              )}
              <View style={styles.trackMeta}>
                <Text style={styles.trackTitle} numberOfLines={1}>
                  {listing.title || ""}
                </Text>
                {listing.price_label ? (
                  <Text style={styles.trackSub} numberOfLines={1}>
                    {listing.price_label}
                  </Text>
                ) : null}
              </View>
            </Pressable>
          ))}
        </View>
      );
    }
    if (tab === "videos") {
      if (videos.state === "loading") {
        return <ActivityIndicator color={colors.accent} style={styles.moduleSpinner} />;
      }
      if (videos.state === "error") {
        return moduleFailure(videos.retry);
      }
      if (!videos.data.length) {
        return emptyModule(
          "No videos yet.",
          "A post with a video attached shows up here. Open Manage to publish one as this presence.",
          { label: "Open Manage", go: goManage }
        );
      }
      return (
        <View>
          {videos.data.map((post) => (
            <Pressable
              key={String(post.id || post.post_id)}
              accessibilityRole="button"
              style={styles.postCard}
              onPress={() => navigation.navigate("PostDetail", { postId: Number(post.id || post.post_id) })}
            >
              <Text style={styles.postTitle} numberOfLines={2}>
                {post.title || post.body || post.text || ""}
              </Text>
              {post.created_at ? <Text style={styles.postMeta}>{post.created_at}</Text> : null}
            </Pressable>
          ))}
        </View>
      );
    }
    // services / reviews — no canonical source wired yet: honest empty state,
    // never fabricated content.
    return <Text style={styles.empty}>Nothing here yet.</Text>;
  }

  function moduleFailure(retry: () => void) {
    return (
      <View style={styles.aboutCard}>
        <Text style={styles.empty}>We couldn&apos;t load this section.</Text>
        <Pressable accessibilityRole="button" style={styles.linkCard} onPress={retry}>
          <Text style={styles.linkCardText}>Try Again</Text>
        </Pressable>
      </View>
    );
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
  moduleSpinner: {
    padding: 24
  },
  trackRow: {
    alignItems: "center",
    flexDirection: "row",
    gap: 12,
    paddingHorizontal: 16,
    paddingVertical: 10
  },
  trackArt: {
    borderRadius: 8,
    height: 48,
    width: 48
  },
  trackArtEmpty: {
    backgroundColor: colors.surface
  },
  trackMeta: {
    flex: 1
  },
  trackTitle: {
    color: colors.text,
    fontSize: 15,
    fontWeight: "700"
  },
  trackSub: {
    color: colors.muted,
    fontSize: 12,
    marginTop: 2
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
