/**
 * Messages — the commerce inbox (Business "Sections" card #6).
 *
 * This is not a generic chat list. Every row is about a money object — an offer,
 * an order, a pickup, a listing question, a completed sale — and the screen
 * surfaces that object on the row via a context chip so a seller can triage the
 * money-relevant threads at a glance. The whole derived model (rows, chips,
 * filters, reply stat, expiry banner, tool counts) lives in `api/commerceInbox`;
 * this screen only wires that model to real navigation and live updates.
 *
 * ## What is live vs. what is honestly dark
 *
 *   • Conversations, unread counts, timestamps and snippets are LIVE from the
 *     Messenger v2 surface (`loadInboxModel` → `listConversations`), with the same
 *     live→cache offline fallback the Messenger tab uses.
 *   • Row taps open the EXISTING thread (`Chat` route). Compose opens `NewChat`.
 *   • Context chips resolve AFTER first render (batched, cached, non-blocking) and
 *     deep-link to the OBJECT (MarketplaceDetail / BuyerOrderDetail), never the
 *     thread. With no commerce backend, a chip appears only when a conversation
 *     carries a real association or the mock-chips flag is on (design review).
 *   • The expiring-offer banner reads the Marketplace offer state machine via
 *     `deriveExpiryBanner`. There is no offers backend, so it is dark by default;
 *     it collects offers from resolved chip links so flipping the flag lights it.
 *   • Typing, presence and the reorder animation are all flag-gated and off by
 *     default — the inbox is then a correct pull-to-refresh list.
 *
 * ## Real-time mechanism
 *
 * No websocket. `subscribeConversationUpdates` is an in-process listener fired
 * when any thread writes the local cache; this screen rides it to upsert + reorder
 * the touched conversation to the top in place (no reload, no scroll jump), and
 * `registerSyncInvalidation("messenger" / "marketplace")` plus pull-to-refresh are
 * the manual refresh paths.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { FlatList, RefreshControl, StyleSheet, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import {
  CommerceLink,
  ContextChipData,
  ExpiryBanner as ExpiryBannerData,
  InboxFilter,
  InboxRow,
  InboxTools,
  ReplyStat,
  awaySubtitle,
  buildContextChip,
  deriveExpiryBanner,
  deriveReplyStat,
  filterCounts,
  loadInboxModel,
  messagesAwayModeEnabled,
  messagesPresenceEnabled,
  messagesTypingEnabled,
  resolveContextChips,
  rowMatchesFilter,
  toInboxRow
} from "../api/commerceInbox";
import { MarketplaceOffer } from "../api/marketplaceOffers";
import { MessengerConversation, subscribeConversationUpdates } from "../api/messenger";
import {
  ConversationRow,
  ExpiryBanner,
  FilterChips,
  InboxToolsGrid,
  MessagesEmpty,
  MessagesError,
  MessagesFilterEmpty,
  MessagesHeader,
  MessagesOffline,
  MessagesSkeleton,
  ReplyStatsStrip
} from "../components/messages";
import { registerSyncInvalidation } from "../core/eventSync";
import { RootStackParamList } from "../navigation/types";
import { messagesLight } from "../theme/messagesLight";
import { useLogiNexusReducedMotion } from "../theme/logiNexusMotion";
import AsyncStorage from "@react-native-async-storage/async-storage";

const FILTER_KEY = "business_os_messages_filter";
const VALID_FILTERS: InboxFilter[] = ["all", "unread", "offers", "orders", "starred", "archived"];

type Nav = { navigate: (...args: any[]) => void; goBack?: () => void };
type Props = {
  route?: { params?: RootStackParamList["BusinessOsMessages"] };
  navigation?: Nav;
};

/** Merge a resolved chip map into rows, non-destructively. */
function withChips(
  rows: InboxRow[],
  links: Map<number, CommerceLink>,
  now: number
): InboxRow[] {
  if (!links.size) return rows;
  return rows.map((row) => {
    const link = links.get(row.id);
    if (!link) return row;
    return { ...row, chip: buildContextChip(link, now) };
  });
}

export function CommerceInboxScreen({ route, navigation }: Props) {
  const reducedMotion = useLogiNexusReducedMotion();
  const insets = useSafeAreaInsets();

  const presenceEnabled = messagesPresenceEnabled();
  const typingEnabled = messagesTypingEnabled();
  const awayEnabled = messagesAwayModeEnabled();

  // Raw conversations are the source of truth the screen owns, so live updates
  // and chip resolution operate in the same currency as the loader.
  const conversationsRef = useRef<MessengerConversation[]>([]);
  const [rows, setRows] = useState<InboxRow[]>([]);
  const [links, setLinks] = useState<Map<number, CommerceLink>>(new Map());
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [offline, setOffline] = useState(false);
  const [error, setError] = useState<string | undefined>();

  const [filter, setFilter] = useState<InboxFilter>("all");
  const [search, setSearch] = useState("");
  const [awayOn, setAwayOn] = useState(false);

  /* -------------------------------------------------------------- *
   * Persisted filter (per session)
   * -------------------------------------------------------------- */
  useEffect(() => {
    AsyncStorage.getItem(FILTER_KEY)
      .then((value) => {
        if (value && (VALID_FILTERS as string[]).includes(value)) {
          setFilter(value as InboxFilter);
        }
      })
      .catch(() => undefined);
  }, []);

  const changeFilter = useCallback((next: InboxFilter) => {
    setFilter(next);
    AsyncStorage.setItem(FILTER_KEY, next).catch(() => undefined);
  }, []);

  /* -------------------------------------------------------------- *
   * Chip resolution — after render, batched, then merged by id.
   * -------------------------------------------------------------- */
  const resolveChips = useCallback(async (conversations: MessengerConversation[]) => {
    const now = Date.now();
    const map = await resolveContextChips(conversations, now);
    setLinks(map);
    setRows((current) => withChips(current, map, now));
  }, []);

  /* -------------------------------------------------------------- *
   * Load — live with cache fallback (mirrors the Messenger tab).
   * -------------------------------------------------------------- */
  const load = useCallback(
    async (kind: "initial" | "refresh" = "initial") => {
      if (kind === "initial") setLoading(true);
      else setRefreshing(true);
      try {
        const model = await loadInboxModel();
        conversationsRef.current = model.conversations;
        setOffline(model.offline);
        setError(model.error);
        const now = Date.now();
        // Re-apply any already-resolved chips immediately so a refresh doesn't
        // blank the chips while the resolver runs again.
        setRows(withChips(model.rows, links, now));
        resolveChips(model.conversations).catch(() => undefined);
      } finally {
        setLoading(false);
        setRefreshing(false);
      }
    },
    [links, resolveChips]
  );

  useEffect(() => {
    load("initial").catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /* -------------------------------------------------------------- *
   * Live updates — in-place upsert + reorder to top, no reload.
   * -------------------------------------------------------------- */
  useEffect(() => {
    const onConversation = (conversation: MessengerConversation) => {
      const next = [
        conversation,
        ...conversationsRef.current.filter((item) => item.id !== conversation.id)
      ];
      conversationsRef.current = next;
      const now = Date.now();
      setRows(withChips(next.map(toInboxRow), links, now));
      // Resolve a chip for the newly-touched conversation if we don't have one.
      if (!links.has(conversation.id)) {
        resolveChips(next).catch(() => undefined);
      }
    };
    const unsubscribe = subscribeConversationUpdates(onConversation);

    const refresh = () => load("refresh").catch(() => undefined);
    const unregister = [
      registerSyncInvalidation("messenger", refresh),
      registerSyncInvalidation("marketplace", refresh)
    ];

    return () => {
      unsubscribe();
      unregister.forEach((fn) => fn());
    };
  }, [links, load, resolveChips]);

  /* -------------------------------------------------------------- *
   * Derived — counts, filtered+searched rows, reply stat, banner.
   * -------------------------------------------------------------- */
  const counts = useMemo(() => filterCounts(rows), [rows]);

  const visibleRows = useMemo(() => {
    const base = rows.filter((row) => rowMatchesFilter(row, filter));
    const q = search.trim().toLowerCase();
    if (!q) return base;
    return base.filter(
      (row) =>
        row.title.toLowerCase().includes(q) ||
        row.snippet.toLowerCase().includes(q) ||
        (row.chip?.line || "").toLowerCase().includes(q)
    );
  }, [rows, filter, search]);

  // Reply stat: no live avg-reply field, so this hides unless a real value is
  // present. Kept honest — never a fabricated "avg reply 2h".
  const replyStat: ReplyStat = useMemo(() => deriveReplyStat(), []);

  // Collect offers the resolved chips point at, so the banner reads the SAME
  // offer state machine the chips do — no second expiry clock. With no offers
  // backend the banner is dark (deriveExpiryBanner is gated on the flag).
  const banner: ExpiryBannerData | null = useMemo(() => {
    const offers: MarketplaceOffer[] = [];
    const offerConversation = new Map<string, number>();
    links.forEach((link, conversationId) => {
      if (link.kind === "offer") {
        offers.push(link.offer);
        offerConversation.set(link.offer.id, conversationId);
      }
    });
    return deriveExpiryBanner(offers, (offer) => offerConversation.get(offer.id));
  }, [links]);

  const tools: InboxTools = useMemo(
    () => ({ awayOn }),
    [awayOn]
  );

  /* -------------------------------------------------------------- *
   * Navigation — every destination is an existing screen.
   * -------------------------------------------------------------- */
  const openThread = useCallback(
    (row: InboxRow) => {
      // Optimistic read clear so the row settles immediately; the thread marks
      // read server-side on open.
      if (row.unreadCount > 0) {
        setRows((current) =>
          current.map((r) => (r.id === row.id ? { ...r, unreadCount: 0 } : r))
        );
      }
      navigation?.navigate("Chat", {
        conversationId: row.id,
        title: row.title,
        avatarUrl: row.avatarUrl,
        presence: row.presence
      });
    },
    [navigation]
  );

  const openChip = useCallback(
    (chip: ContextChipData) => {
      if (!chip.target) return;
      navigation?.navigate(chip.target.screen, chip.target.params);
    },
    [navigation]
  );

  const openBanner = useCallback(
    (b: ExpiryBannerData) => {
      if (b.conversationId != null) {
        navigation?.navigate("Chat", { conversationId: b.conversationId });
      }
    },
    [navigation]
  );

  const compose = useCallback(() => {
    navigation?.navigate("NewChat");
  }, [navigation]);

  const goBack = useCallback(() => {
    navigation?.goBack?.();
  }, [navigation]);

  const openSpamBlocked = useCallback(() => {
    navigation?.navigate("BlockedUsers");
  }, [navigation]);

  const openNotifications = useCallback(() => {
    navigation?.navigate("NotificationSettings");
  }, [navigation]);

  const openSavedReplies = useCallback(() => {
    // No saved-replies manager exists yet (declared MOCK-DATA gap); this is an
    // honest no-op rather than a route that would crash.
  }, []);

  const toggleAway = useCallback(
    (next: boolean) => {
      // Optimistic-local only — no away/auto-reply backend field exists yet.
      if (!awayEnabled) return;
      setAwayOn(next);
    },
    [awayEnabled]
  );

  /* -------------------------------------------------------------- *
   * List chrome
   * -------------------------------------------------------------- */
  const listHeader = useMemo(
    () => (
      <View>
        {offline ? <MessagesOffline message={error} /> : null}
        {banner ? (
          <ExpiryBanner banner={banner} onOpen={openBanner} reducedMotion={reducedMotion} />
        ) : null}
      </View>
    ),
    [offline, error, banner, openBanner, reducedMotion]
  );

  const listFooter = useMemo(
    () =>
      loading ? null : (
        <InboxToolsGrid
          tools={tools}
          awayEnabled={awayEnabled}
          onSavedReplies={openSavedReplies}
          onToggleAway={toggleAway}
          onSpamBlocked={openSpamBlocked}
          onNotifications={openNotifications}
        />
      ),
    [loading, tools, awayEnabled, openSavedReplies, toggleAway, openSpamBlocked, openNotifications]
  );

  const listEmpty = useMemo(() => {
    if (loading) return <MessagesSkeleton />;
    if (error && !offline && rows.length === 0) {
      return <MessagesError message={error} onRetry={() => load("initial")} />;
    }
    if (rows.length === 0) return <MessagesEmpty />;
    return <MessagesFilterEmpty filter={filter} />;
  }, [loading, error, offline, rows.length, filter, load]);

  const renderRow = useCallback(
    ({ item }: { item: InboxRow }) => (
      <ConversationRow
        row={item}
        reducedMotion={reducedMotion}
        presenceEnabled={presenceEnabled}
        typingEnabled={typingEnabled}
        onPress={openThread}
        onChipPress={openChip}
      />
    ),
    [reducedMotion, presenceEnabled, typingEnabled, openThread, openChip]
  );

  return (
    <View style={styles.screen}>
      <MessagesHeader
        title={route?.params?.title || "Messages"}
        canCompose
        onBack={goBack}
        onCompose={compose}
        searchValue={search}
        onSearchChange={setSearch}
      />
      <ReplyStatsStrip stat={replyStat} />
      <FilterChips active={filter} counts={counts} onChange={changeFilter} />

      <FlatList
        data={visibleRows}
        keyExtractor={(item) => String(item.id)}
        renderItem={renderRow}
        ListHeaderComponent={listHeader}
        ListFooterComponent={listFooter}
        ListEmptyComponent={listEmpty}
        contentContainerStyle={[
          styles.content,
          { paddingBottom: insets.bottom + 24 },
          visibleRows.length === 0 && styles.contentEmpty
        ]}
        showsVerticalScrollIndicator={false}
        initialNumToRender={10}
        windowSize={11}
        removeClippedSubviews
        refreshControl={
          <RefreshControl
            refreshing={refreshing}
            onRefresh={() => load("refresh")}
            tintColor={messagesLight.text.muted}
          />
        }
      />
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: messagesLight.bg.page },
  content: { paddingTop: 4, flexGrow: 1 },
  contentEmpty: { justifyContent: "flex-start" }
});
