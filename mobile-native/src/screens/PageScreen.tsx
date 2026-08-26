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
import { LinearGradient } from "expo-linear-gradient";
import {
  getPage,
  getPageByHandle,
  listPageEvents,
  listPageMusic,
  listPagePosts,
  PageEvent,
  PageTrack,
  pageTypeLabel,
  PulsePage,
  togglePageFollow
} from "../api/pages";
import { searchMarketplace, type MarketplaceListing } from "../api/marketplace";
import { PULSE_API_BASE_URL } from "../api/config";
import { PulseApiError } from "../api/pulseApi";
import type { PulsePost } from "../api/feed";
import { RootStackParamList } from "../navigation/types";
import { colors } from "../theme/colors";
import { presenceAccent } from "../theme/presenceAccent";
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
const EMPTY_LISTINGS: MarketplaceListing[] = [];
const EMPTY_POSTS: PulsePost[] = [];
const SHOP_TABS = ["shop", "merch", "menu"];

/**
 * The events module carries its flags, not just its rows.
 *
 * Every other module here loads a list and an empty list means one thing. This
 * one has three empties that read identically and need different sentences:
 * the events domain is off for the whole environment, the presence has not
 * been pointed at a business, or the business simply has no dates coming up.
 * So the module's value is the server's whole answer rather than
 * `result.events`, and the branch below decides which of the three it is.
 */
type PageEventsResult = { enabled: boolean; linked: boolean; events: PageEvent[] };
const EMPTY_EVENTS: PageEventsResult = { enabled: false, linked: false, events: [] };

/**
 * Music carries its flags for the same reason, one case fewer.
 *
 * "No catalogue is connected" and "the connected catalogue has no releases" are
 * the same empty list and need different sentences — this tab used to tell a
 * team that had already connected an artist profile to go and connect one, and
 * they would have gone looking for a step they had taken.
 *
 * `artist` is the catalogue this presence publishes under. It is worth carrying
 * because it is the one place a visitor can see that a presence is showing
 * *somebody else's* releases: the link stores a name, connecting one is a
 * `manage_links` write, and a presence pointed at the wrong catalogue looks
 * exactly like a presence pointed at the right one until the name is on screen.
 */
type PageMusicResult = { artist: string; linked: boolean; tracks: PageTrack[] };
const EMPTY_MUSIC: PageMusicResult = { artist: "", linked: false, tracks: [] };

/**
 * A stored date as something to read, or the raw text when it is not a date.
 *
 * `starts_at` is free text server-side and is never format-checked on the way
 * in, so this has to cope with not being given a timestamp at all. Showing the
 * raw string back is the honest fallback: whoever typed "Late summer 2026"
 * meant it to be read, and blanking the line would lose the only thing the
 * page says about when this happens.
 */
function eventWhen(value?: string) {
  const raw = (value || "").trim();
  if (!raw) return "";
  const parsed = new Date(raw);
  if (Number.isNaN(parsed.getTime())) return raw;
  return parsed.toLocaleDateString(undefined, {
    weekday: "short",
    day: "numeric",
    month: "short",
    year: "numeric"
  });
}

/**
 * What the cheapest still-available tier costs, or that none are left.
 *
 * The server sends `sold_out` per tier and never a remaining count, so this
 * can say "gone" but not "3 left" — which is the intended limit, not a gap to
 * fill later. An event with no tiers gets no line at all rather than "Free":
 * we do not know that it is free, only that nobody has priced it here.
 */
function eventPrice(event: PageEvent) {
  const tiers = event.ticket_types || [];
  if (!tiers.length) return "";
  const open = tiers.filter((tier) => !tier.sold_out);
  if (!open.length) return "Sold out";
  const cheapest = open.reduce((low, tier) => (tier.price_cents < low.price_cents ? tier : low));
  if (!cheapest.price_cents) return "Free entry";
  const amount = (cheapest.price_cents / 100).toFixed(2);
  return `From ${event.currency ? `${event.currency} ` : ""}${amount}`;
}

/**
 * The public page surface — one component for every page type. The tab set is
 * SERVER-decided per type (artist: posts/music/videos/merch/about; business:
 * home/shop/about; …) and rendered as delivered.
 *
 * The branches in `renderTabBody` are the client half of a contract: the server
 * keeps the same set as `RENDERABLE_TABS` and refuses to offer a tab outside it,
 * so a page type cannot name a tab that nothing here draws. Adding a tab means
 * adding a branch below and the rule that says when it is backed.
 *
 * Real metrics only: follower and post counts come from the server's measured
 * counts. An empty module says so — never invented numbers, never placeholder
 * reviews — and says it differently to the team, who can do something about it.
 */
export function PageScreen({ route, navigation }: Props) {
  const params = route.params || {};
  const [page, setPage] = useState<PulsePage | null>(null);
  const [posts, setPosts] = useState<PulsePost[]>([]);
  const [tab, setTab] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [followBusy, setFollowBusy] = useState(false);
  const [followError, setFollowError] = useState("");
  const [error, setError] = useState("");

  const pageKey = page ? String(page.id) : "";
  const sellerId = Number(page?.shop_seller_id || 0);
  const music = useLazyModule<PageMusicResult>(tab === "music", pageKey, EMPTY_MUSIC, () =>
    listPageMusic(Number(pageKey))
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
  const events = useLazyModule<PageEventsResult>(tab === "events", pageKey, EMPTY_EVENTS, () =>
    listPageEvents(Number(pageKey))
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
    setFollowError("");
    try {
      const result = await togglePageFollow(page.id);
      setPage({
        ...page,
        followers_count: result.followers_count,
        viewer: { role: page.viewer?.role || null, following: result.following }
      });
    } catch (toggleError) {
      // The prior state stands — the server is authoritative about who follows
      // what, and this screen does not get to pretend otherwise. But the
      // refusal is said out loud. Swallowing it left the button lifting under
      // the finger and changing nothing, which reads as a broken app rather
      // than as a page that is not accepting followers.
      setFollowError(
        toggleError instanceof PulseApiError ? toggleError.message : "That did not go through."
      );
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
  /**
   * Whether anyone outside the team can reach this presence at all.
   *
   * The server answers a follow on an UNPUBLISHED or DEACTIVATED page with a
   * flat 403, and `_load_visible_page` answers a *visitor* with a 404 — so the
   * only person who can be looking at a hidden presence is a member of its own
   * team, and the only Follow button that ever reached this state was one the
   * server was always going to refuse. The same fact is why a link to it is not
   * worth sending yet: it opens for nobody but the people who already have it.
   */
  const isPublic = page.status === "ACTIVE" || page.status === "PAUSED";

  /**
   * The colour this presence is drawn in, from its type.
   *
   * The type already decides the tab set and the labels; this is the same fact
   * reaching the viewer before the words do. It is one lookup at the top rather
   * than a colour picked per style rule, so a restaurant cannot end up with an
   * artist's tab underline because one line was missed.
   *
   * `verified` is deliberately left out of it below. A verification badge is a
   * claim about trust, and a trust marker that is a different colour on every
   * page is a trust marker people stop reading — it stays brand teal wherever
   * it appears.
   */
  const tone = presenceAccent(page.page_type);

  const header = (
    <View>
      {page.cover_url ? (
        <Image source={{ uri: page.cover_url }} style={styles.cover} />
      ) : (
        // No cover is not a reason for a grey bar. The wash is the presence's
        // own accent falling away to nothing, so an unfurnished page still
        // reads as somebody's rather than as a loading state.
        <LinearGradient colors={tone.wash} style={styles.cover} testID="page-cover-wash">
          <View style={[styles.coverEdge, { backgroundColor: tone.border }]} />
        </LinearGradient>
      )}
      <View style={styles.hero}>
        {page.avatar_url ? (
          <Image source={{ uri: page.avatar_url }} style={[styles.avatar, { borderColor: tone.base }]} />
        ) : (
          <View
            style={[styles.avatar, styles.avatarFallback, { backgroundColor: tone.fill, borderColor: tone.base }]}
          >
            <Text style={[styles.avatarInitial, { color: tone.base }]}>
              {page.name.slice(0, 1).toUpperCase()}
            </Text>
          </View>
        )}
        <View style={styles.heroText}>
          <View style={styles.nameRow}>
            <Text style={styles.name}>{page.name}</Text>
            {page.verified ? <Text style={styles.verified}>Verified</Text> : null}
          </View>
          <Text style={[styles.handle, { color: tone.base }]}>
            @{page.handle} · {pageTypeLabel(page.page_type)}
          </Text>
          {page.category ? <Text style={styles.category}>{page.category}</Text> : null}
          <Text style={styles.counts}>
            {page.followers_count} followers · {page.posts_count} posts
          </Text>
        </View>
      </View>

      <View style={styles.actions}>
        {isPublic ? (
          <Pressable
            accessibilityRole="button"
            testID="page-follow"
            style={[
              styles.actionPrimary,
              { backgroundColor: tone.base },
              following && [styles.actionFollowing, { borderColor: tone.base }]
            ]}
            disabled={followBusy}
            onPress={onFollow}
          >
            <Text
              style={[
                styles.actionPrimaryText,
                { color: tone.ink },
                following && { color: tone.base }
              ]}
            >
              {following ? "Following" : "Follow"}
            </Text>
          </Pressable>
        ) : null}
        {/* Share stays: the team still has reason to copy their own link while
            they finish setting the presence up. What changes is that the note
            below says who it will open for. */}
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
      {isPublic ? null : (
        <Text style={styles.actionNote}>
          {page.status === "DEACTIVATED"
            ? "Deactivated. Only the team can open this presence, and nobody can follow it."
            : "Not published yet. Only the team can open this presence, and nobody can follow it."}
        </Text>
      )}
      {followError ? <Text style={styles.actionError}>{followError}</Text> : null}

      <View style={styles.tabBar}>
        {page.tabs.map((tabKey) => (
          <Pressable
            key={tabKey}
            accessibilityRole="button"
            accessibilityState={{ selected: tab === tabKey }}
            style={[
              styles.tabButton,
              tab === tabKey && [styles.tabActive, { borderBottomColor: tone.base }]
            ]}
            onPress={() => setTab(tabKey)}
          >
            <Text style={[styles.tabText, tab === tabKey && { color: tone.base }]}>
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
          <Pressable accessibilityRole="button" style={[styles.linkCard, { backgroundColor: tone.fill, borderColor: tone.border }]}
            onPress={action.go}
          >
            <Text style={[styles.linkCardText, { color: tone.base }]}>{action.label}</Text>
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
      if (!music.data.tracks.length) {
        return emptyModule(
          "No music yet.",
          music.data.linked
            ? // Already connected. Releases are published to the catalogue
              // itself, not from here, so there is no step left to offer — the
              // same shape as a shop that is connected and has no listings.
              `Releases published to ${music.data.artist} appear here.`
            : "Tracks are uploaded to an artist profile. Connect the one these releases live under and they appear here.",
          music.data.linked ? null : { label: "Connect an artist profile", go: goConnections }
        );
      }
      return (
        <View>
          {/*
            Named only when it differs from the presence. "From the catalogue of
            Night Signal" on Night Signal's own page is noise, and noise is what
            stops a line like this being read on the one page where it matters —
            the presence quietly publishing somebody else's releases.
          */}
          {music.data.artist && music.data.artist !== page.name ? (
            <Text style={styles.moduleSource}>
              From the catalogue of {music.data.artist}.
            </Text>
          ) : null}
          {music.data.tracks.map((track) => (
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
    if (tab === "events") {
      if (events.state === "loading") {
        return <ActivityIndicator color={colors.accent} style={styles.moduleSpinner} />;
      }
      if (events.state === "error") {
        return moduleFailure(events.retry);
      }
      if (!events.data.events.length) {
        if (!events.data.enabled) {
          /*
            The events domain is switched off for this whole environment. The
            team gets told why the tab is empty and explicitly told there is no
            step for them — offering "Connect a business" here would be sending
            them to do work that changes nothing, which is worse than saying
            nothing. The headline stays the visitor's sentence because from the
            outside it is still simply true.
          */
          return emptyModule(
            "No dates announced yet.",
            "Events are switched off for this environment, so there is nothing to connect yet.",
            null
          );
        }
        return emptyModule(
          "No dates announced yet.",
          events.data.linked
            ? "Dates you schedule for the connected business appear here."
            : "Events are scheduled against a business. Connect the one that runs these dates and they appear here.",
          // Same shape as the shop: connecting is a Connections action, and
          // once something is connected the dates are created where the
          // organiser already works rather than through a second door here.
          events.data.linked ? null : { label: "Connect a business", go: goConnections }
        );
      }
      return (
        <View>
          {events.data.events.map((event) => {
            const when = eventWhen(event.starts_at);
            const price = eventPrice(event);
            /*
              A View, not a Pressable. There is no event detail screen in this
              app, and a row that lifts under the finger and then does nothing
              is the exact defect this work is about — a control with no depth
              behind it. Everything the visitor is allowed to know is on the
              row already.
            */
            return (
              <View key={event.event_id} style={styles.eventRow}>
                <Text style={styles.trackTitle} numberOfLines={2}>
                  {event.title}
                </Text>
                {when || event.venue ? (
                  <Text style={styles.trackSub} numberOfLines={1}>
                    {[when, event.venue].filter(Boolean).join(" · ")}
                  </Text>
                ) : null}
                {price ? <Text style={[styles.eventPrice, { color: tone.base }]}>{price}</Text> : null}
              </View>
            );
          })}
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
    /*
      A tab this build has no branch for. It should be unreachable: the server
      only offers tabs in its `RENDERABLE_TABS`, which is this file's branch set
      written down, and it raises rather than serving one without a rule.

      What is left is version skew — a newer server offering a tab to an older
      app. So this says the module needs a newer app, which is true and
      actionable, instead of "Nothing here yet.", which is a claim about the
      page and would be false. It offers nothing to the team either: updating is
      not something they do from here.
    */
    return <Text style={styles.empty}>This section needs a newer version of the app.</Text>;
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
  // A refusal reads differently from a standing fact, so it is coloured
  // differently: `actionError` is something that just went wrong, `actionNote`
  // is how the presence is set up.
  actionError: {
    color: colors.danger,
    fontSize: 13,
    paddingHorizontal: 16,
    paddingTop: 8
  },
  actionNote: {
    color: colors.muted,
    fontSize: 13,
    paddingHorizontal: 16,
    paddingTop: 8
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
    justifyContent: "flex-end",
    width: "100%"
  },
  // A single hairline where the wash meets the page. It is what stops an
  // accent-filled block from reading as a placeholder rectangle, and it is
  // cheaper and steadier than a gradient behind a header that scrolls.
  coverEdge: {
    height: StyleSheet.hairlineWidth * 2,
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
  eventPrice: {
    color: colors.accent,
    fontSize: 12,
    fontWeight: "800",
    marginTop: 4
  },
  eventRow: {
    borderBottomColor: colors.border,
    borderBottomWidth: 1,
    gap: 2,
    paddingHorizontal: 16,
    paddingVertical: 12
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
  moduleSource: {
    color: colors.muted,
    fontSize: 12,
    paddingBottom: 4,
    paddingTop: 2
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
