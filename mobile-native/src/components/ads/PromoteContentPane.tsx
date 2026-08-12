/**
 * Promote your content — the Post Ads home.
 *
 * This is the real content browser that replaced the "Post ads isn't switched
 * on yet" placeholder and the sample-data rails. It lists the signed-in owner's
 * already-published Posts, Reels and finalized Live replays from
 * `GET /api/promotions/content`, each stamped with a server-decided eligibility
 * verdict, and lets the owner put ad budget behind one — the promotion
 * references the original content object; nothing is duplicated or reposted.
 *
 * Everything shown is real and server-authoritative:
 *   • eligibility (owner / public / processed / moderation / active promotion)
 *     is decided by the server — the Promote button is enabled only when the
 *     server says `promotable`.
 *   • organic metrics are omitted rather than fabricated (the listing endpoint
 *     returns none today, so none are shown).
 *   • promotion status ("Promoting" / "In review") is surfaced from the same
 *     eligibility verdict, so a merely-submitted item never reads as active.
 *   • the list is paginated (offset/limit); history is not loaded all at once.
 *
 * Promote opens the promotion wizard (`mode: "promote"`) with the selected
 * content — the wizard collects goal / budget / duration and submits against the
 * one ad engine and the one shared ad wallet.
 */

import { ReactElement, useCallback, useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  FlatList,
  Image,
  Pressable,
  RefreshControl,
  StyleSheet,
  Text,
  View
} from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import {
  PromotableContentItem,
  PromotableFilter,
  appendPromotablePage,
  listPromotableContent
} from "../../api/promotions";
import { PulseApiError } from "../../api/pulseApi";
import { useFormatters } from "../../i18n/hooks";
import { BOTTOM_NAV_CONTENT_CLEARANCE } from "../../navigation/BottomNavVisibility";
import { adsLight } from "../../theme/adsLight";

const PAGE_SIZE = 12;

type FilterTab = { key: PromotableFilter; label: string };

const FILTER_TABS: FilterTab[] = [
  { key: "all", label: "All" },
  { key: "post", label: "Posts" },
  { key: "reel", label: "Reels" },
  { key: "live_replay", label: "Live replays" }
];

const CONTENT_TYPE_LABELS: Record<string, string> = {
  post: "Post",
  reel: "Reel",
  live_replay: "Live replay"
};

/**
 * Non-promotable verdicts get a short status pill so the owner sees *why* the
 * content can't be promoted right now instead of a dead button.
 */
const STATUS_PILL: Record<string, { label: string; tone: "info" | "warning" | "muted" }> = {
  ACTIVE_PROMOTION: { label: "Promoting", tone: "info" },
  UNDER_REVIEW: { label: "In review", tone: "warning" },
  PRIVATE: { label: "Private", tone: "muted" },
  REPLAY_PROCESSING: { label: "Processing", tone: "muted" },
  PROCESSING: { label: "Processing", tone: "muted" },
  MODERATION_BLOCKED: { label: "Not eligible", tone: "warning" },
  NOT_ELIGIBLE: { label: "Not eligible", tone: "muted" }
};

type PromoteNavParams = {
  mode: "promote";
  title: string;
  accountId?: number;
  promoteContent: {
    contentType: PromotableContentItem["contentType"];
    contentId: number;
    title: string;
    thumbnailUrl: string;
    mediaKind: string;
  };
};

type Props = {
  /** True when the Post pane is the active tab; the pane is display:none otherwise. */
  visible: boolean;
  accountId?: number;
  navigation?: { navigate: (route: string, params?: PromoteNavParams) => void; goBack?: () => void };
};

function contentTypeLabel(type: string): string {
  return CONTENT_TYPE_LABELS[type] || "Content";
}

function durationLabel(seconds: number | null): string {
  if (!seconds || seconds <= 0) return "";
  const total = Math.round(seconds);
  const m = Math.floor(total / 60);
  const s = total % 60;
  return m > 0 ? `${m}:${String(s).padStart(2, "0")}` : `0:${String(s).padStart(2, "0")}`;
}

export function PromoteContentPane({ visible, accountId, navigation }: Props) {
  const fmt = useFormatters();
  const insets = useSafeAreaInsets();

  const [filter, setFilter] = useState<PromotableFilter>("all");
  const [items, setItems] = useState<PromotableContentItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string>("");
  const [nextOffset, setNextOffset] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [loadedOnce, setLoadedOnce] = useState(false);

  // A monotonic token guards against a slow response for a stale filter
  // overwriting the list after the user has already switched tabs.
  const requestToken = useRef(0);

  const load = useCallback(
    async (nextFilter: PromotableFilter, mode: "initial" | "refresh") => {
      const token = ++requestToken.current;
      if (mode === "refresh") setRefreshing(true);
      else setLoading(true);
      setError("");
      try {
        const page = await listPromotableContent({ filter: nextFilter, limit: PAGE_SIZE, offset: 0 });
        if (token !== requestToken.current) return;
        setItems(page.items);
        setNextOffset(page.nextOffset);
        setHasMore(page.hasMore);
      } catch (err) {
        if (token !== requestToken.current) return;
        setError(err instanceof PulseApiError ? err.message : "We couldn't load your content. Try again.");
        setItems([]);
        setHasMore(false);
      } finally {
        if (token === requestToken.current) {
          setLoading(false);
          setRefreshing(false);
          setLoadedOnce(true);
        }
      }
    },
    []
  );

  // Load on first reveal and whenever the filter changes while visible. The
  // deferral to first-visible keeps the inactive pane from fetching on mount.
  useEffect(() => {
    if (!visible) return;
    if (!loadedOnce || filter) {
      void load(filter, "initial");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visible, filter]);

  const onRefresh = useCallback(() => {
    void load(filter, "refresh");
  }, [filter, load]);

  const loadMore = useCallback(async () => {
    if (loadingMore || loading || refreshing || !hasMore) return;
    const token = requestToken.current;
    setLoadingMore(true);
    try {
      const page = await listPromotableContent({ filter, limit: PAGE_SIZE, offset: nextOffset });
      if (token !== requestToken.current) return;
      setItems((prev) => appendPromotablePage(prev, page));
      setNextOffset(page.nextOffset);
      setHasMore(page.hasMore);
    } catch {
      // A failed "load more" leaves the existing list intact; the footer stops
      // spinning and the user can scroll to retry.
      if (token === requestToken.current) setHasMore(false);
    } finally {
      if (token === requestToken.current) setLoadingMore(false);
    }
  }, [filter, hasMore, loading, loadingMore, nextOffset, refreshing]);

  const openPromote = useCallback(
    (item: PromotableContentItem) => {
      navigation?.navigate("BusinessOsAdvertising", {
        mode: "promote",
        title: "Promote your content",
        accountId,
        promoteContent: {
          contentType: item.contentType,
          contentId: item.contentId,
          title: item.title,
          thumbnailUrl: item.thumbnailUrl,
          mediaKind: item.mediaKind
        }
      });
    },
    [navigation, accountId]
  );

  const renderItem = useCallback(
    ({ item }: { item: PromotableContentItem }) => {
      const pill = item.promotable ? null : STATUS_PILL[item.eligibility] || STATUS_PILL.NOT_ELIGIBLE;
      const duration = durationLabel(item.durationSeconds);
      const published = item.createdAt ? fmt.relative(item.createdAt) : "";
      return (
        <View style={styles.card}>
          <View style={styles.thumbWrap}>
            {item.thumbnailUrl ? (
              <Image source={{ uri: item.thumbnailUrl }} style={styles.thumb} resizeMode="cover" />
            ) : (
              <View style={[styles.thumb, styles.thumbFallback]}>
                <Text style={styles.thumbFallbackText}>{contentTypeLabel(item.contentType).charAt(0)}</Text>
              </View>
            )}
            {duration ? (
              <View style={styles.durationChip}>
                <Text style={styles.durationText}>{duration}</Text>
              </View>
            ) : null}
          </View>

          <View style={styles.cardBody}>
            <View style={styles.cardHeaderRow}>
              <View style={[styles.typeBadge, typeBadgeStyle(item.contentType)]}>
                <Text style={[styles.typeBadgeText, typeBadgeTextStyle(item.contentType)]}>
                  {contentTypeLabel(item.contentType)}
                </Text>
              </View>
              {pill ? (
                <View style={[styles.statusPill, pillToneStyle(pill.tone)]}>
                  <Text style={[styles.statusPillText, pillToneTextStyle(pill.tone)]}>{pill.label}</Text>
                </View>
              ) : null}
            </View>

            <Text style={styles.cardTitle} numberOfLines={2}>
              {item.title}
            </Text>
            {item.snippet ? (
              <Text style={styles.cardSnippet} numberOfLines={2}>
                {item.snippet}
              </Text>
            ) : null}
            {published ? <Text style={styles.cardMeta}>{published}</Text> : null}

            {item.promotable ? (
              <Pressable
                onPress={() => openPromote(item)}
                style={styles.promoteBtn}
                accessibilityRole="button"
                accessibilityLabel={`Promote ${contentTypeLabel(item.contentType).toLowerCase()}`}
                hitSlop={6}
              >
                <Text style={styles.promoteBtnText}>Promote</Text>
              </Pressable>
            ) : (
              <Text style={styles.ineligibleReason}>{item.eligibilityReason}</Text>
            )}
          </View>
        </View>
      );
    },
    [fmt, openPromote]
  );

  const header = (
    <View style={styles.header}>
      <Text style={styles.heading}>Promote your content</Text>
      <Text style={styles.subhead}>Choose something you've already posted and reach more people.</Text>
      <View style={styles.filterRow}>
        {FILTER_TABS.map((tab) => {
          const active = tab.key === filter;
          return (
            <Pressable
              key={tab.key}
              onPress={() => setFilter(tab.key)}
              style={[styles.filterChip, active && styles.filterChipActive]}
              accessibilityRole="button"
              accessibilityState={{ selected: active }}
              accessibilityLabel={`Filter: ${tab.label}`}
              hitSlop={4}
            >
              <Text style={[styles.filterChipText, active && styles.filterChipTextActive]}>{tab.label}</Text>
            </Pressable>
          );
        })}
      </View>
    </View>
  );

  const footer = loadingMore ? (
    <View style={styles.footer}>
      <ActivityIndicator color={adsLight.post.base} />
    </View>
  ) : null;

  let body: ReactElement;
  if (loading && !refreshing) {
    body = (
      <View style={styles.centered}>
        <ActivityIndicator color={adsLight.post.base} />
      </View>
    );
  } else if (error) {
    body = (
      <View style={styles.centered}>
        <Text style={styles.stateTitle}>Couldn't load your content</Text>
        <Text style={styles.stateBody}>{error}</Text>
        <Pressable onPress={onRefresh} style={styles.retryBtn} accessibilityRole="button" accessibilityLabel="Retry">
          <Text style={styles.retryBtnText}>Try again</Text>
        </Pressable>
      </View>
    );
  } else if (!items.length) {
    body = (
      <View style={styles.centered}>
        <Text style={styles.stateTitle}>Nothing to promote yet</Text>
        <Text style={styles.stateBody}>
          Post something, share a Reel, or finish a live stream — it'll show up here, ready to promote.
        </Text>
      </View>
    );
  } else {
    body = (
      <FlatList
        data={items}
        keyExtractor={(item) => `${item.contentType}:${item.contentId}`}
        renderItem={renderItem}
        contentContainerStyle={styles.listContent}
        showsVerticalScrollIndicator={false}
        onEndReachedThreshold={0.4}
        onEndReached={loadMore}
        ListFooterComponent={footer}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={adsLight.post.base} />}
      />
    );
  }

  return (
    <View style={[styles.root, !visible && styles.hidden, { paddingBottom: bottomPad(insets.bottom) }]}>
      {header}
      {body}
    </View>
  );
}

function bottomPad(inset: number) {
  return Math.max(inset, 12) + BOTTOM_NAV_CONTENT_CLEARANCE;
}

function typeBadgeStyle(type: string) {
  if (type === "reel") return { backgroundColor: adsLight.content.reelBg };
  if (type === "live_replay") return { backgroundColor: adsLight.content.liveBg };
  return { backgroundColor: adsLight.content.postBg };
}

function typeBadgeTextStyle(type: string) {
  if (type === "reel") return { color: adsLight.content.reelText };
  if (type === "live_replay") return { color: adsLight.content.liveText };
  return { color: adsLight.content.postText };
}

function pillToneStyle(tone: "info" | "warning" | "muted") {
  if (tone === "info") return { backgroundColor: adsLight.post.tint };
  if (tone === "warning") return { backgroundColor: adsLight.bg.warning };
  return { backgroundColor: adsLight.bg.strip };
}

function pillToneTextStyle(tone: "info" | "warning" | "muted") {
  if (tone === "info") return { color: adsLight.post.base };
  if (tone === "warning") return { color: adsLight.status.warning };
  return { color: adsLight.text.muted };
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
    backgroundColor: adsLight.bg.page
  },
  hidden: {
    display: "none"
  },
  header: {
    paddingHorizontal: adsLight.space.gutter,
    paddingTop: 16,
    paddingBottom: 8,
    backgroundColor: adsLight.bg.page
  },
  heading: {
    fontSize: 22,
    fontWeight: "800",
    color: adsLight.text.primary
  },
  subhead: {
    marginTop: 4,
    fontSize: 14,
    lineHeight: 20,
    color: adsLight.text.muted
  },
  filterRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8,
    marginTop: 14
  },
  filterChip: {
    paddingHorizontal: 14,
    paddingVertical: 8,
    borderRadius: adsLight.radius.pill,
    backgroundColor: adsLight.bg.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: adsLight.border.hairline
  },
  filterChipActive: {
    backgroundColor: adsLight.post.base,
    borderColor: adsLight.post.base
  },
  filterChipText: {
    fontSize: 14,
    fontWeight: "600",
    color: adsLight.text.primary
  },
  filterChipTextActive: {
    color: adsLight.post.onViolet
  },
  listContent: {
    paddingHorizontal: adsLight.space.gutter,
    paddingTop: 8,
    gap: 12
  },
  card: {
    flexDirection: "row",
    backgroundColor: adsLight.bg.card,
    borderRadius: adsLight.radius.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: adsLight.border.hairline,
    overflow: "hidden"
  },
  thumbWrap: {
    width: 96,
    backgroundColor: adsLight.bg.skeleton
  },
  thumb: {
    width: 96,
    height: "100%",
    minHeight: 96
  },
  thumbFallback: {
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: adsLight.bg.postSurface
  },
  thumbFallbackText: {
    fontSize: 28,
    fontWeight: "800",
    color: adsLight.post.base
  },
  durationChip: {
    position: "absolute",
    bottom: 6,
    right: 6,
    backgroundColor: "rgba(0,0,0,0.7)",
    borderRadius: 4,
    paddingHorizontal: 5,
    paddingVertical: 2
  },
  durationText: {
    color: "#FFFFFF",
    fontSize: 11,
    fontWeight: "700"
  },
  cardBody: {
    flex: 1,
    padding: 12,
    gap: 4
  },
  cardHeaderRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: 8
  },
  typeBadge: {
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: adsLight.radius.pill
  },
  typeBadgeText: {
    fontSize: 11,
    fontWeight: "700"
  },
  statusPill: {
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: adsLight.radius.pill
  },
  statusPillText: {
    fontSize: 11,
    fontWeight: "700"
  },
  cardTitle: {
    fontSize: 15,
    fontWeight: "700",
    color: adsLight.text.primary,
    marginTop: 2
  },
  cardSnippet: {
    fontSize: 13,
    lineHeight: 18,
    color: adsLight.text.muted
  },
  cardMeta: {
    fontSize: 12,
    color: adsLight.text.muted,
    marginTop: 2
  },
  promoteBtn: {
    alignSelf: "flex-start",
    marginTop: 8,
    paddingHorizontal: 18,
    paddingVertical: 9,
    borderRadius: adsLight.radius.control,
    backgroundColor: adsLight.post.base
  },
  promoteBtnText: {
    color: adsLight.post.onViolet,
    fontSize: 14,
    fontWeight: "700"
  },
  ineligibleReason: {
    marginTop: 8,
    fontSize: 12,
    lineHeight: 17,
    color: adsLight.text.muted,
    fontStyle: "italic"
  },
  centered: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    paddingHorizontal: 32,
    gap: 8
  },
  stateTitle: {
    fontSize: 16,
    fontWeight: "700",
    color: adsLight.text.primary,
    textAlign: "center"
  },
  stateBody: {
    fontSize: 14,
    lineHeight: 20,
    color: adsLight.text.muted,
    textAlign: "center"
  },
  retryBtn: {
    marginTop: 12,
    paddingHorizontal: 20,
    paddingVertical: 10,
    borderRadius: adsLight.radius.control,
    backgroundColor: adsLight.post.base
  },
  retryBtnText: {
    color: adsLight.post.onViolet,
    fontSize: 14,
    fontWeight: "700"
  },
  footer: {
    paddingVertical: 20,
    alignItems: "center"
  }
});

export default PromoteContentPane;
