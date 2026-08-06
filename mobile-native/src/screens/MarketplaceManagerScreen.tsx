/**
 * Marketplace — the screen behind card #3 of the Business "Sections" grid.
 *
 * ## Why this is not `MarketplaceScreen`
 *
 * `screens/MarketplaceScreen.tsx` already exists and is the *consumer* browse
 * surface: dark theme, bottom-nav dock, reached from the `Marketplace` tab and
 * from `MarketplaceDetail` deep links. It is shipped and it works. This is a
 * different job — item-by-item selling, with a buying feed attached — so it is
 * a different file on a different route, exactly as `BusinessProfile` was added
 * beside the profile screens rather than replacing one. Nine call sites still
 * reach `MarketplaceCreateGateway` for the composer and none of them are
 * touched.
 *
 * ## Marketplace is not Store
 *
 * Store (card #2) is a permanent catalogue with stock levels. Marketplace is
 * one-off listings that get haggled over and then disappear. That difference is
 * why this screen leads with *offers* rather than KPIs: the thing that needs a
 * Marketplace seller today is a person waiting for an answer, not a number.
 *
 * ## Both panes stay mounted
 *
 * Switching modes toggles `display: none` rather than unmounting. The brief
 * requires each mode's scroll position to survive the switch, and restoring an
 * offset by hand always lands a few pixels off and re-runs the entrance. Two
 * mounted lists cost more memory; they buy a toggle that feels like a toggle
 * rather than like a navigation push.
 *
 * ## What is real and what is not
 *
 * Offers and the cart are backed by real route packs
 * (`services/marketplace_offers_routes.py`, `services/marketplace_cart_routes.py`)
 * and their clients in `api/marketplaceCommerce`; the flags
 * `MARKETPLACE_OFFERS_ENABLED` and `MARKETPLACE_CART_ENABLED` are on, with the
 * deploy-ordering rule documented on each flag. Boost purchase still has no
 * backend — `MARKETPLACE_BOOST_ENABLED` stays `false`, and nothing here fakes
 * a boost sale. `MARKETPLACE_MOCK_DATA_GAPS` in `api/marketplaceScreen` lists
 * every remaining gap and the backend work it needs.
 *
 * The NEW and FEATURED badges, listing staleness, and the whole Add-to-cart vs
 * Make-offer split *are* real: `created_at`, `featured` and `delivery_type`
 * already existed in `marketplace_listings` and were simply never selected.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Animated,
  FlatList,
  Image,
  Modal,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
  type ViewToken
} from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { LinearGradient } from "expo-linear-gradient";
import { Ionicons } from "@expo/vector-icons";
import {
  MARKETPLACE_BOOST_ENABLED,
  MARKETPLACE_CART_ENABLED,
  MARKETPLACE_OFFERS_ENABLED,
  applyOfferActionToList,
  beginOfferAction,
  isOfferFresh,
  offersAwaitingSeller,
  resolveExpiries,
  type MarketplaceOffer,
  type OfferAction
} from "../api/marketplaceOffers";
import {
  addToCart as addToCartServer,
  fetchCart,
  fetchOffers,
  actOnOffer,
  counterOffer as counterOfferServer,
  type CartSnapshot
} from "../api/marketplaceCommerce";
import {
  ALL_SELLING_TABS,
  CORE_SELLING_TABS,
  boostCandidate,
  deriveBuyingItems,
  deriveCategories,
  deriveSellingItems,
  deriveSellingSummary,
  loadLastMarketplaceMode,
  loadMarketplaceCity,
  loadMarketplaceScreen,
  marketplaceLocation,
  saveLastMarketplaceMode,
  saveMarketplaceCity,
  sellerSnapshotFrom,
  sellingTabCounts,
  type BuyingItem,
  type MarketplaceLoadResult,
  type MarketplaceLocationActionKey,
  type SellingItem,
  type SellingTabKey
} from "../api/marketplaceScreen";
import {
  CATEGORY_ALL,
  CategoryChipRail,
  ItemGridCard,
  ModeToggle,
  OfferCard,
  SavedSearchAlert,
  type MarketplaceMode
} from "../components/marketplace";
import {
  StoreHeader,
  StoreOfflineNote,
  StoreQuickLinkGrid,
  StoreRowSkeleton,
  StoreSectionError,
  StoreSkeletonBlock
} from "../components/store";
import { registerSyncInvalidation } from "../core/eventSync";
import { refreshUnreadCounts, useBellCount } from "../core/unreadCounts";
import { useFormatters } from "../i18n/hooks";
import { setSaved } from "../social/useSaveAction";
import { BOTTOM_NAV_CONTENT_CLEARANCE } from "../navigation/BottomNavVisibility";
import { RootStackParamList } from "../navigation/types";
import { storeLight } from "../theme/storeLight";
import { marketplaceLight } from "../theme/marketplaceLight";
import { useLogiNexusReducedMotion } from "../theme/logiNexusMotion";
import {
  useStoreAmbient,
  useStoreBadgePop,
  useStoreEntrance,
  useStorePress,
  useStoreValueArrival
} from "../theme/storeMotion";
import {
  MARKETPLACE_AMBIENT,
  useMarketplaceModeSwap,
  useMarketplaceSoldWipe
} from "../theme/marketplaceMotion";
import { absentValueText, valueState } from "../api/stateLanguage";

/** Entrance slots per mode. Named so a section cannot animate out of order. */
const SELLING_SLOT = {
  chips: 0,
  offers: 1,
  tabs: 2,
  items: 3,
  boost: 4,
  more: 5,
  cta: 6
} as const;
const SELLING_SECTIONS = Object.keys(SELLING_SLOT).length;

const BUYING_SLOT = { rail: 0, alert: 1, grid: 2, more: 3 } as const;
const BUYING_SECTIONS = Object.keys(BUYING_SLOT).length;

/** How many grid cards "Show more nearby" adds, and how many start visible. */
const GRID_PAGE = 12;
/** How many rows the Selling list previews before "See all N items". */
const SELLING_PREVIEW = 6;

const SELLING_TAB_LABELS: Record<SellingTabKey, string> = {
  active: "Active",
  reserved: "Reserved",
  sold: "Sold",
  drafts: "Drafts",
  pending_review: "Pending review",
  expired: "Expired",
  removed: "Removed",
  archived: "Archived"
};

/**
 * Core tabs always render; the others only when they hold something. A
 * permanent "Removed 0" would advertise moderation trouble the seller has
 * never had, and eight always-on tabs would not fit the row anyway. The one
 * wrinkle: the selected tab stays rendered even if its count drops to zero
 * mid-session, so the selection never points at a tab that is not on screen.
 */
function visibleSellingTabs(
  counts: Record<SellingTabKey, number>,
  selected: SellingTabKey
): { key: SellingTabKey; label: string }[] {
  return ALL_SELLING_TABS.filter(
    (key) => CORE_SELLING_TABS.includes(key) || counts[key] > 0 || key === selected
  ).map((key) => ({ key, label: SELLING_TAB_LABELS[key] }));
}

type Props = {
  route?: { params?: RootStackParamList["BusinessOs"] };
  navigation: { navigate: (...args: any[]) => void; goBack?: () => void };
};

export function MarketplaceManagerScreen({ navigation }: Props) {
  const formatters = useFormatters();
  const reducedMotion = useLogiNexusReducedMotion();
  const insets = useSafeAreaInsets();

  const [mode, setMode] = useState<MarketplaceMode>("selling");
  const [result, setResult] = useState<MarketplaceLoadResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [query, setQuery] = useState("");
  const [tab, setTab] = useState<SellingTabKey>("active");
  const [expanded, setExpanded] = useState(false);
  const [category, setCategory] = useState(CATEGORY_ALL);
  const [gridLimit, setGridLimit] = useState(GRID_PAGE);
  const [offers, setOffers] = useState<readonly MarketplaceOffer[]>([]);
  const [savedIds, setSavedIds] = useState<readonly number[]>([]);
  const [cartIds, setCartIds] = useState<readonly number[]>([]);
  const [cartBadge, setCartBadge] = useState(0);
  const [confirmingId, setConfirmingId] = useState<number | null>(null);
  const [visibleIds, setVisibleIds] = useState<readonly number[]>([]);
  const [now, setNow] = useState(() => Date.now());
  const [city, setCity] = useState<string | null>(null);
  const [locationSheetOpen, setLocationSheetOpen] = useState(false);
  const [meetupSheetOpen, setMeetupSheetOpen] = useState(false);

  /**
   * What this feed may claim about location.
   *
   * The city is a stored, self-reported preference — not a geo lookup — and the
   * heading, the strip, the footer and the empty state all derive from this one
   * call, which is the reason this is one derivation rather than three strings
   * scattered through the render.
   */
  const place = marketplaceLocation({ city, categoryFiltered: category !== CATEGORY_ALL });

  const sellingEntrance = useStoreEntrance(SELLING_SECTIONS, reducedMotion);
  const buyingEntrance = useStoreEntrance(BUYING_SECTIONS, reducedMotion);
  const swap = useMarketplaceModeSwap(mode, reducedMotion);

  /* ---------------------------------------------------------------- *
   * Loading
   * ---------------------------------------------------------------- */

  const load = useCallback(
    async (kind: "initial" | "refresh" = "initial", nextQuery = query) => {
      if (kind === "refresh") setRefreshing(true);
      else setLoading(true);
      try {
        setResult(await loadMarketplaceScreen({ query: nextQuery }));
        setNow(Date.now());
      } finally {
        setLoading(false);
        setRefreshing(false);
      }
    },
    [query]
  );

  useEffect(() => {
    load("initial").catch(() => undefined);
    // Deliberately not in the dependency list: this is the first load, and
    // `load` changes identity whenever the search box changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    loadLastMarketplaceMode()
      .then(setMode)
      .catch(() => undefined);
    loadMarketplaceCity()
      .then(setCity)
      .catch(() => undefined);
  }, []);

  /** Persist, then reflect. A save failure still updates this session. */
  const changeCity = useCallback((next: string | null) => {
    const trimmed = String(next || "").trim();
    setCity(trimmed.length > 0 ? trimmed : null);
    setLocationSheetOpen(false);
    saveMarketplaceCity(trimmed).catch(() => undefined);
  }, []);

  // Header bell reads the ONE shared unread store (same as every seller header
  // and the Activity feed). Pull the authoritative count on mount; eventSync
  // keeps it fresh.
  const bellCount = useBellCount();
  useEffect(() => {
    void refreshUnreadCounts();
  }, []);

  // The same three channels the Store dashboard listens on, so a listing edited
  // in the composer or an order placed elsewhere shows up here without a pull.
  useEffect(() => {
    const refresh = () => load("refresh").catch(() => undefined);
    const unregister = [
      registerSyncInvalidation("seller_inventory", refresh),
      registerSyncInvalidation("marketplace", refresh),
      registerSyncInvalidation("orders", refresh)
    ];
    return () => unregister.forEach((fn) => fn());
  }, [load]);

  /**
   * Sweep expiries once a minute.
   *
   * An offer that lapsed while the screen was open should stop offering an
   * Accept button, and the state machine dates the expiry at the lapse rather
   * than at the moment anyone noticed — so a slow sweep is correct, not
   * approximate. `resolveExpiries` returns the same array when nothing changed,
   * so this is free on every tick that has nothing to do.
   */
  useEffect(() => {
    if (!MARKETPLACE_OFFERS_ENABLED) return;
    const timer = setInterval(() => {
      const stamp = Date.now();
      setNow(stamp);
      setOffers((current) => resolveExpiries(current, stamp));
    }, 60_000);
    return () => clearInterval(timer);
  }, []);

  const changeMode = useCallback((next: MarketplaceMode) => {
    setMode(next);
    saveLastMarketplaceMode(next).catch(() => undefined);
  }, []);

  /**
   * Server cart and offers are the source of truth; local state is a mirror.
   *
   * `applyCart` is the single funnel every cart response goes through, so the
   * badge and the per-card membership can never disagree with each other.
   * `syncOffers` replaces the whole list with server truth — after a counter,
   * reconciling row-by-row would have to dedupe the optimistic counter against
   * the real one, and a replace is both simpler and always correct.
   */
  const applyCart = useCallback((snap: CartSnapshot) => {
    setCartIds(snap.lines.map((line) => line.listing_id));
    setCartBadge(snap.badgeCount);
  }, []);

  const syncOffers = useCallback(() => {
    fetchOffers("seller")
      .then((rows) => setOffers(resolveExpiries(rows, Date.now())))
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    if (MARKETPLACE_CART_ENABLED) fetchCart().then(applyCart).catch(() => undefined);
    if (MARKETPLACE_OFFERS_ENABLED) syncOffers();
  }, [applyCart, syncOffers]);

  /**
   * Counter sheet.
   *
   * Countering is the one offer action that needs a number from the seller, so
   * it cannot be a single tap like Accept and Decline. The sheet holds the
   * target offer rather than just its id, because it has to show the buyer's
   * figure and the listed price side by side — a counter typed with neither in
   * view is a guess.
   */
  const [counterTarget, setCounterTarget] = useState<MarketplaceOffer | null>(null);
  const [counterInput, setCounterInput] = useState("");

  const openCounter = useCallback((offer: MarketplaceOffer) => {
    setCounterTarget(offer);
    // Seeded with the listed price, which is the seller's own asking figure and
    // the most common counter. Seeding with the buyer's offer would prefill a
    // number the seller has already declined by opening this sheet.
    setCounterInput(String(Math.round(offer.listPriceMinor / 100)));
  }, []);

  const closeCounter = useCallback(() => {
    setCounterTarget(null);
    setCounterInput("");
  }, []);

  const counterMinor = useMemo(() => {
    const parsed = Number(counterInput.replace(/[^0-9.]/g, ""));
    if (!Number.isFinite(parsed) || parsed <= 0) return null;
    return Math.round(parsed * 100);
  }, [counterInput]);

  const submitCounter = useCallback(() => {
    if (!counterTarget || counterMinor == null) return;
    runOfferAction(counterTarget.id, "counter", counterMinor);
    closeCounter();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [counterTarget, counterMinor, closeCounter]);

  /* ---------------------------------------------------------------- *
   * Derivations
   * ---------------------------------------------------------------- */

  const snapshot = useMemo(
    () => (result ? sellerSnapshotFrom(result) : { listings: [], orders: [] }),
    [result]
  );
  const sellingItems = useMemo(() => deriveSellingItems(snapshot, now), [snapshot, now]);
  const tabCounts = useMemo(() => sellingTabCounts(sellingItems), [sellingItems]);
  const tabItems = useMemo(
    () => sellingItems.filter((item) => item.tab === tab),
    [sellingItems, tab]
  );
  const visibleSellingItems = useMemo(
    () => (expanded ? tabItems : tabItems.slice(0, SELLING_PREVIEW)),
    [tabItems, expanded]
  );
  const boost = useMemo(() => boostCandidate(sellingItems), [sellingItems]);

  const waitingOffers = useMemo(() => offersAwaitingSeller(offers, now), [offers, now]);
  const summary = useMemo(
    () => deriveSellingSummary(sellingItems, waitingOffers.length),
    [sellingItems, waitingOffers.length]
  );

  const feedListings = useMemo(
    () => (result?.feed.status === "ok" ? result.feed.data : []),
    [result]
  );
  const categories = useMemo(() => deriveCategories(feedListings), [feedListings]);
  const buyingItems = useMemo(
    () =>
      deriveBuyingItems(feedListings, {
        now,
        cartEnabled: MARKETPLACE_CART_ENABLED,
        offersEnabled: MARKETPLACE_OFFERS_ENABLED
      }),
    [feedListings, now]
  );
  const filteredBuying = useMemo(
    () =>
      category === CATEGORY_ALL
        ? buyingItems
        : buyingItems.filter((item) => item.category.toLowerCase() === category),
    [buyingItems, category]
  );
  const pagedBuying = useMemo(
    () => filteredBuying.slice(0, gridLimit),
    [filteredBuying, gridLimit]
  );

  const savedSet = useMemo(() => new Set(savedIds), [savedIds]);
  const visibleSet = useMemo(() => new Set(visibleIds), [visibleIds]);

  // Seed the saved set from whatever the API already told us, so a heart the
  // user filled on another screen is filled here on first paint.
  useEffect(() => {
    const initial = buyingItems.filter((item) => item.saved).map((item) => item.id);
    if (initial.length) setSavedIds((current) => Array.from(new Set([...current, ...initial])));
  }, [buyingItems]);

  /* ---------------------------------------------------------------- *
   * Actions
   * ---------------------------------------------------------------- */

  const openItem = useCallback(
    (listingId: number, title: string) => {
      navigation.navigate("MarketplaceDetail", { listingId, title });
    },
    [navigation]
  );

  const openComposer = useCallback(() => {
    navigation.navigate("MarketplaceCreateGateway", { title: "List an item" });
  }, [navigation]);

  /**
   * Run one offer transition.
   *
   * `beginOfferAction` stamps `pending` *before* anything async happens, and
   * `offerActionsDisabled` reads that stamp to grey out all three buttons
   * together. Disabling all three rather than only the pressed one is
   * deliberate: Accept and Decline racing each other is a worse outcome than
   * either being pressed twice.
   */
  const runOfferAction = useCallback(
    (offerId: string, action: OfferAction, amountMinor?: number) => {
      let started = false;
      setOffers((current) => {
        const target = current.find((offer) => offer.id === offerId);
        if (!target) return current;
        const begun = beginOfferAction(target, action);
        if (!begun.ok) return current;
        started = true;
        const stamped = current.map((offer) => (offer.id === offerId ? begun.offer : offer));
        const { offers: next } = applyOfferActionToList(stamped, offerId, {
          action,
          actor: "seller",
          now: Date.now(),
          counterAmountMinor: amountMinor
        });
        return next;
      });
      // The optimistic transition above is the reducer this module always
      // promised to become; the server call settles it. Success and failure
      // both end in `syncOffers` because the counter case creates a row whose
      // real id only the server knows, and a failed accept must roll back to
      // whatever the server actually holds — replace-with-truth covers both.
      if (!started) return;
      const settle =
        action === "counter"
          ? counterOfferServer(offerId, amountMinor ?? 0)
          : actOnOffer(offerId, action);
      settle.then(syncOffers).catch(syncOffers);
    },
    [syncOffers]
  );

  const toggleSave = useCallback(async (item: BuyingItem) => {
    const wasSaved = savedSet.has(item.id);
    // Optimistic, then reconciled against what the server actually stored — the
    // same contract every other savable surface in the app uses.
    setSavedIds((current) =>
      wasSaved ? current.filter((id) => id !== item.id) : [...current, item.id]
    );
    const outcome = await setSaved({ type: "marketplace", id: item.id }, !wasSaved);
    setSavedIds((current) => {
      const without = current.filter((id) => id !== item.id);
      return outcome.saved ? [...without, item.id] : without;
    });
  }, [savedSet]);

  /**
   * Add to cart.
   *
   * Guarded by the flag rather than by a disabled button, because the button is
   * not rendered at all while the flag is off — `gridCardAction` returns null.
   * The guard is here so that turning the flag on without wiring the cart
   * cannot silently no-op into a "success" confirmation.
   */
  const addToCart = useCallback(
    (item: BuyingItem) => {
      if (!MARKETPLACE_CART_ENABLED) return;
      // Optimistic membership + badge, reconciled against the snapshot the
      // server returns. On failure the confirmation is withdrawn and the cart
      // re-read — a silently dropped "Add to cart" is a lie told to the buyer.
      const wasIn = cartIds.includes(item.id);
      setCartIds((current) => (wasIn ? current : [...current, item.id]));
      if (!wasIn) setCartBadge((count) => count + 1);
      setConfirmingId(item.id);
      setTimeout(() => setConfirmingId((current) => (current === item.id ? null : current)), 1400);
      addToCartServer(item.id)
        .then(applyCart)
        .catch(() => {
          setConfirmingId((current) => (current === item.id ? null : current));
          fetchCart().then(applyCart).catch(() => undefined);
        });
    },
    [cartIds, applyCart]
  );

  const onViewableItemsChanged = useRef(({ viewableItems }: { viewableItems: ViewToken[] }) => {
    setVisibleIds(viewableItems.map((token) => Number((token.item as BuyingItem)?.id)));
  }).current;

  /* ---------------------------------------------------------------- *
   * Header
   * ---------------------------------------------------------------- */

  // Badge count comes from the server snapshot (optimistically bumped on add),
  // so it agrees with what the cart screen will show — `cartIds` is membership
  // for per-card state, not a counter.
  const cartCount = cartBadge;
  const savedCount = savedIds.length;

  const header = (
    <StoreHeader
      title="Marketplace"
      query={query}
      onQueryChange={setQuery}
      onSubmitSearch={() => load("refresh", query).catch(() => undefined)}
      onBack={() => navigation.goBack?.()}
      onNotifications={() => navigation.navigate("BusinessOsActivity")}
      // The bell now reads the ONE shared unread store (was hard-coded 0) and
      // routes to the unified Activity feed — the same number and destination as
      // every other seller header.
      unreadCount={bellCount}
      hideNotifications={mode === "buying"}
      searchPlaceholder={
        mode === "selling" ? "Search your items and offers" : "Search Marketplace"
      }
      reducedMotion={reducedMotion}
      accessories={
        mode === "buying" ? (
          <BuyingHeaderCounts
            savedCount={savedCount}
            cartCount={cartCount}
            onSaved={() => navigation.navigate("Saved")}
            // With the cart live this routes to the real cart screen; before
            // the flag flip it honestly went to Purchases instead.
            onCart={() =>
              MARKETPLACE_CART_ENABLED
                ? navigation.navigate("MarketplaceCart", { title: "Cart" })
                : navigation.navigate("BuyerPurchases", { title: "Purchases" })
            }
            reducedMotion={reducedMotion}
          />
        ) : null
      }
      below={
        <View style={styles.headerBelow}>
          <ModeToggle mode={mode} onChange={changeMode} reducedMotion={reducedMotion} />
          {mode === "buying" ? (
            <LocationStrip
              text={place.stripText}
              actionLabel={place.stripAction.label}
              onPress={() => setLocationSheetOpen(true)}
            />
          ) : null}
        </View>
      }
    />
  );

  /* ---------------------------------------------------------------- *
   * Render
   * ---------------------------------------------------------------- */

  const bottomPad = insets.bottom + BOTTOM_NAV_CONTENT_CLEARANCE;

  return (
    <View style={styles.root}>
      {header}

      {result?.offline ? (
        <StoreOfflineNote
          text={
            result.cachedAt
              ? `Offline — showing items saved ${formatters.relative(result.cachedAt)}`
              : "Offline — showing saved items"
          }
        />
      ) : null}

      <Animated.View style={[styles.panes, { opacity: swap }]}>
        <View style={[styles.pane, mode !== "selling" && styles.paneHidden]}>
          <SellingPane
            loading={loading}
            refreshing={refreshing}
            onRefresh={() => load("refresh").catch(() => undefined)}
            entrance={sellingEntrance}
            reducedMotion={reducedMotion}
            formatters={formatters}
            summary={summary}
            offers={waitingOffers}
            offersError={result?.listings.status === "error" ? null : null}
            listingsError={
              result?.listings.status === "error" ? result.listings.message : null
            }
            onRetryListings={() => load("refresh").catch(() => undefined)}
            city={city}
            onOpenLocation={() => setLocationSheetOpen(true)}
            tab={tab}
            tabCounts={tabCounts}
            onTabChange={(next) => {
              setTab(next);
              setExpanded(false);
            }}
            items={visibleSellingItems}
            totalInTab={tabItems.length}
            expanded={expanded}
            onExpand={() => setExpanded(true)}
            boost={boost}
            now={now}
            onOfferAction={runOfferAction}
            onCounterRequest={openCounter}
            onOpenItem={openItem}
            onCompose={openComposer}
            onOpenMeetupSafety={() => setMeetupSheetOpen(true)}
            navigation={navigation}
            bottomPad={bottomPad}
          />
        </View>

        <View style={[styles.pane, mode !== "buying" && styles.paneHidden]}>
          <BuyingPane
            loading={loading}
            refreshing={refreshing}
            onRefresh={() => load("refresh").catch(() => undefined)}
            entrance={buyingEntrance}
            reducedMotion={reducedMotion}
            city={city}
            onOpenLocation={() => setLocationSheetOpen(true)}
            categories={categories}
            category={category}
            onCategoryChange={(next) => {
              setCategory(next);
              setGridLimit(GRID_PAGE);
            }}
            items={pagedBuying}
            total={filteredBuying.length}
            onShowMore={() => setGridLimit((current) => current + GRID_PAGE)}
            feedError={result?.feed.status === "error" ? result.feed.message : null}
            onRetryFeed={() => load("refresh").catch(() => undefined)}
            savedSet={savedSet}
            visibleSet={visibleSet}
            confirmingId={confirmingId}
            onToggleSave={(item) => {
              toggleSave(item).catch(() => undefined);
            }}
            onAction={(item) => {
              if (item.action === "cart") addToCart(item);
              else if (item.action === "offer") openItem(item.id, item.title);
            }}
            onOpenItem={openItem}
            onViewableItemsChanged={onViewableItemsChanged}
            bottomPad={bottomPad}
          />
        </View>
      </Animated.View>

      {/* The counter sheet lives at the screen root, not inside SellingPane,
          because the pane is one of two absolutely-positioned siblings and a
          modal anchored inside a `display: none` subtree would vanish if the
          user somehow toggled modes underneath it. */}
      <CounterSheet
        offer={counterTarget}
        amount={counterInput}
        amountMinor={counterMinor}
        formatters={formatters}
        onChangeAmount={setCounterInput}
        onCancel={closeCounter}
        onSubmit={submitCounter}
      />

      {/* Same root-level placement as CounterSheet, for the same reason. */}
      <LocationSheet
        visible={locationSheetOpen}
        city={city}
        onCancel={() => setLocationSheetOpen(false)}
        onSave={changeCity}
      />

      <MeetupSafetySheet visible={meetupSheetOpen} onClose={() => setMeetupSheetOpen(false)} />
    </View>
  );
}

/**
 * Meetup safety sheet.
 *
 * MOCK-DATA: there is no meetup-spot storage, so the tile cannot list saved
 * spots — but the safety guidance is real content in its own right, which is
 * why the tile routes here instead of sitting locked. When spot storage
 * exists, this sheet grows a list above the tips.
 */
const MEETUP_SAFETY_TIPS: readonly { title: string; body: string }[] = [
  {
    title: "Meet in a busy public place",
    body: "Cafés, shopping centres, and transit stations with foot traffic. Many police stations offer designated exchange zones."
  },
  {
    title: "Daylight, and bring someone",
    body: "Prefer daytime meetups and tell a friend where you are going — or bring them along."
  },
  {
    title: "Keep the conversation in the app",
    body: "Chat history is your record if something goes wrong. Be wary of buyers who push to move off-platform."
  },
  {
    title: "Inspect before money moves",
    body: "Let the buyer check the item, then settle. Avoid carrying more cash than the sale needs."
  },
  {
    title: "Trust the bad feeling",
    body: "If anything feels off, leave. No sale is worth ignoring your instincts."
  }
];

function MeetupSafetySheet({ visible, onClose }: { visible: boolean; onClose: () => void }) {
  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}>
      <Pressable
        style={styles.sheetScrim}
        onPress={onClose}
        accessibilityRole="button"
        accessibilityLabel="Close meetup safety tips"
      />
      <View style={styles.sheet}>
        <Text style={styles.sheetTitle}>Meet up safely</Text>
        <Text style={styles.sheetSub}>
          Saved meetup spots are not available yet. Until they are, these are the basics for a
          safe in-person exchange.
        </Text>
        {MEETUP_SAFETY_TIPS.map((tip) => (
          <View key={tip.title} style={styles.safetyTip}>
            <Text style={styles.sheetLabel}>{tip.title}</Text>
            <Text style={styles.sheetSub}>{tip.body}</Text>
          </View>
        ))}
        <View style={styles.sheetActions}>
          <Pressable
            onPress={onClose}
            style={[styles.sheetButton, styles.sheetCancel]}
            accessibilityRole="button"
            accessibilityLabel="Close"
          >
            <Text style={styles.sheetCancelText}>Close</Text>
          </Pressable>
        </View>
      </View>
    </Modal>
  );
}

/**
 * Location sheet.
 *
 * Collects one thing: a self-reported city or postal area, stored as a
 * preference. It is not a geo lookup — nothing here reads device location —
 * and the sheet says what setting it changes. Clearing returns the screen to
 * the no-claim wording.
 */
function LocationSheet({
  visible,
  city,
  onCancel,
  onSave
}: {
  visible: boolean;
  city: string | null;
  onCancel: () => void;
  onSave: (next: string | null) => void;
}) {
  const [draft, setDraft] = useState("");

  // Re-seed the field each time the sheet opens, so reopening after a cancel
  // shows the saved value rather than the abandoned edit.
  useEffect(() => {
    if (visible) setDraft(city || "");
  }, [visible, city]);

  const trimmed = draft.trim();

  return (
    <Modal
      visible={visible}
      transparent
      animationType="slide"
      onRequestClose={onCancel}
      accessibilityViewIsModal
    >
      <Pressable style={styles.sheetScrim} onPress={onCancel} accessibilityLabel="Close" />
      <View style={styles.sheet}>
        <Text style={styles.sheetTitle}>{city ? "Change location" : "Set location"}</Text>
        <Text style={styles.sheetSub}>
          Enter a city or area. This sets what the Marketplace shows you — it is not shared
          with sellers and does not read your device location.
        </Text>

        <Text style={styles.sheetLabel}>City or area</Text>
        <TextInput
          value={draft}
          onChangeText={setDraft}
          style={styles.sheetInput}
          accessibilityLabel="City or area"
          placeholder="e.g. New York"
          placeholderTextColor={storeLight.text.muted}
          autoFocus
          autoCapitalize="words"
          autoCorrect={false}
          returnKeyType="done"
          onSubmitEditing={() => (trimmed ? onSave(trimmed) : undefined)}
        />

        <View style={styles.sheetActions}>
          <Pressable
            onPress={onCancel}
            style={[styles.sheetButton, styles.sheetCancel]}
            accessibilityRole="button"
            accessibilityLabel="Cancel"
          >
            <Text style={styles.sheetCancelText}>Cancel</Text>
          </Pressable>
          <Pressable
            onPress={() => onSave(trimmed || null)}
            disabled={!trimmed && !city}
            style={[
              styles.sheetButton,
              styles.sheetSend,
              !trimmed && !city && styles.sheetSendOff
            ]}
            accessibilityRole="button"
            accessibilityState={{ disabled: !trimmed && !city }}
            accessibilityLabel={
              trimmed
                ? `Save location ${trimmed}`
                : city
                  ? "Clear location"
                  : "Save location, enter a city first"
            }
          >
            <Text style={styles.sheetSendText}>{trimmed ? "Save" : city ? "Clear location" : "Save"}</Text>
          </Pressable>
        </View>
      </View>
    </Modal>
  );
}

/**
 * Counter-offer sheet.
 *
 * Deliberately plain. The seller is being asked for one number, with the two
 * numbers that matter — what the buyer offered and what the item is listed at —
 * held in view above the field. Submit is disabled until the input parses to a
 * positive amount, so the sheet cannot post a counter of zero or of nothing.
 */
function CounterSheet({
  offer,
  amount,
  amountMinor,
  formatters,
  onChangeAmount,
  onCancel,
  onSubmit
}: {
  offer: MarketplaceOffer | null;
  amount: string;
  amountMinor: number | null;
  formatters: Formatters;
  onChangeAmount: (next: string) => void;
  onCancel: () => void;
  onSubmit: () => void;
}) {
  const valid = amountMinor != null;

  return (
    <Modal
      visible={offer != null}
      transparent
      animationType="slide"
      onRequestClose={onCancel}
      accessibilityViewIsModal
    >
      <Pressable style={styles.sheetScrim} onPress={onCancel} accessibilityLabel="Close" />
      <View style={styles.sheet}>
        {offer ? (
          <>
            <Text style={styles.sheetTitle}>Counter offer</Text>
            <Text style={styles.sheetSub}>
              {offer.buyerName} offered{" "}
              {formatters.currency(offer.amountMinor / 100, { currency: offer.currency })} for{" "}
              {offer.itemTitle}, listed at{" "}
              {formatters.currency(offer.listPriceMinor / 100, { currency: offer.currency })}.
            </Text>

            <Text style={styles.sheetLabel}>Your counter</Text>
            <TextInput
              value={amount}
              onChangeText={onChangeAmount}
              keyboardType="numeric"
              style={styles.sheetInput}
              accessibilityLabel="Counter amount"
              placeholder="0"
              placeholderTextColor={storeLight.text.muted}
            />

            <View style={styles.sheetActions}>
              <Pressable
                onPress={onCancel}
                style={[styles.sheetButton, styles.sheetCancel]}
                accessibilityRole="button"
                accessibilityLabel="Cancel counter offer"
              >
                <Text style={styles.sheetCancelText}>Cancel</Text>
              </Pressable>
              <Pressable
                onPress={onSubmit}
                disabled={!valid}
                style={[styles.sheetButton, styles.sheetSend, !valid && styles.sheetSendOff]}
                accessibilityRole="button"
                accessibilityState={{ disabled: !valid }}
                accessibilityLabel={
                  valid && amountMinor != null
                    ? `Send counter offer of ${formatters.currency(amountMinor / 100, {
                        currency: offer.currency
                      })}`
                    : "Send counter offer, enter an amount first"
                }
              >
                <Text style={styles.sheetSendText}>Send counter</Text>
              </Pressable>
            </View>
          </>
        ) : null}
      </View>
    </Modal>
  );
}

/* ------------------------------------------------------------------ *
 * Selling mode
 * ------------------------------------------------------------------ */

type Formatters = ReturnType<typeof useFormatters>;

function SellingPane({
  loading,
  refreshing,
  onRefresh,
  entrance,
  reducedMotion,
  formatters,
  summary,
  offers,
  listingsError,
  onRetryListings,
  city,
  onOpenLocation,
  tab,
  tabCounts,
  onTabChange,
  items,
  totalInTab,
  expanded,
  onExpand,
  boost,
  now,
  onOfferAction,
  onCounterRequest,
  onOpenItem,
  onCompose,
  onOpenMeetupSafety,
  navigation,
  bottomPad
}: {
  loading: boolean;
  refreshing: boolean;
  onRefresh: () => void;
  entrance: ReturnType<typeof useStoreEntrance>;
  reducedMotion: boolean;
  formatters: Formatters;
  summary: { activeCount: number; offersWaiting: number; savesThisWeek: number | null };
  offers: readonly MarketplaceOffer[];
  offersError: string | null;
  listingsError: string | null;
  onRetryListings: () => void;
  city: string | null;
  onOpenLocation: () => void;
  tab: SellingTabKey;
  tabCounts: Record<SellingTabKey, number>;
  onTabChange: (next: SellingTabKey) => void;
  items: readonly SellingItem[];
  totalInTab: number;
  expanded: boolean;
  onExpand: () => void;
  boost: SellingItem | null;
  now: number;
  onOfferAction: (offerId: string, action: OfferAction, amountMinor?: number) => void;
  onCounterRequest: (offer: MarketplaceOffer) => void;
  onOpenItem: (listingId: number, title: string) => void;
  onCompose: () => void;
  onOpenMeetupSafety: () => void;
  navigation: { navigate: (...args: any[]) => void };
  bottomPad: number;
}) {
  const ctaPress = useStorePress(reducedMotion, 0.98);

  if (loading) {
    return (
      <ScrollView contentContainerStyle={[styles.scroll, { paddingBottom: bottomPad }]}>
        <View style={styles.chipRow}>
          {[0, 1, 2].map((key) => (
            <View key={key} style={styles.chipSkeleton}>
              <StoreSkeletonBlock width="60%" height={10} reducedMotion={reducedMotion} />
              <StoreSkeletonBlock width="45%" height={20} reducedMotion={reducedMotion} />
            </View>
          ))}
        </View>
        {[0, 1].map((key) => (
          <View key={key} style={styles.offerSkeleton}>
            <StoreSkeletonBlock width="55%" height={12} reducedMotion={reducedMotion} />
            <StoreSkeletonBlock width="35%" height={24} reducedMotion={reducedMotion} />
            <StoreSkeletonBlock width="90%" height={40} reducedMotion={reducedMotion} />
          </View>
        ))}
        {[0, 1, 2, 3].map((key) => (
          <StoreRowSkeleton key={key} reducedMotion={reducedMotion} />
        ))}
      </ScrollView>
    );
  }

  const empty = ALL_SELLING_TABS.every((key) => tabCounts[key] === 0);

  return (
    <ScrollView
      contentContainerStyle={[styles.scroll, { paddingBottom: bottomPad }]}
      refreshControl={
        <RefreshControl
          refreshing={refreshing}
          onRefresh={onRefresh}
          tintColor={storeLight.text.link}
        />
      }
    >
      {/* 1. Summary chips */}
      <Animated.View style={[styles.chipRow, entrance.styleFor(SELLING_SLOT.chips)]}>
        <SummaryChip
          label="Active items"
          value={formatters.count(summary.activeCount)}
          reducedMotion={reducedMotion}
          onPress={() => onTabChange("active")}
        />
        <SummaryChip
          label="Offers waiting"
          // Two different situations shared this dash: no offers backend at all,
          // and a seller who genuinely has none waiting. The backend is real now
          // (`services/marketplace_offers_routes.py`), so the count is a measured
          // figure at every value — zero included, which is good news, not an
          // unknown. No dash, no flag: a counted number never needed either.
          value={
            MARKETPLACE_OFFERS_ENABLED
              ? formatters.count(summary.offersWaiting)
              : absentValueText(
                  valueState({ value: summary.offersWaiting, configured: false }),
                  { zeroText: formatters.count(0) }
                )
          }
          amber={summary.offersWaiting > 0}
          reducedMotion={reducedMotion}
          arrivalDelay={70}
          onPress={() => undefined}
          // MOCK-DATA: with no offers backend this is always zero, so the chip
          // is not a link to anywhere.
          disabled={!MARKETPLACE_OFFERS_ENABLED}
        />
        <SummaryChip
          label="Saves this week"
          // MOCK-DATA: no per-listing saves aggregate. The number is unknown,
          // not known to be none — and "Not measured yet" says which, where the
          // dash left the seller to guess between an absence and a zero.
          //
          // The real number goes first, explicitly. `absentValueText` answers
          // "what do I render when there is no number", and a count that exists
          // is not that question — it falls through to the function's zero
          // fallback, which would print 0 over a real figure the day this field
          // starts arriving. So the count is rendered here and the helper is
          // asked only about the absence.
          value={
            summary.savesThisWeek != null
              ? formatters.count(summary.savesThisWeek)
              : absentValueText(valueState({ value: null, configured: false }), {
                  notConfiguredText: "Not measured yet"
                })
          }
          reducedMotion={reducedMotion}
          arrivalDelay={140}
          disabled
        />
      </Animated.View>

      {/* 2. Offers to answer. Hidden entirely when there is nothing waiting —
             an empty "Offers" heading is worse than no heading. */}
      {offers.length > 0 ? (
        <Animated.View style={entrance.styleFor(SELLING_SLOT.offers)}>
          <SectionHeader
            title="Offers to answer"
            actionLabel="All offers"
            onAction={() => navigation.navigate("Messenger")}
          />
          {offers.map((offer) => (
            <OfferCard
              key={offer.id}
              offer={offer}
              fresh={isOfferFresh(offer, now)}
              amountText={formatters.currency(offer.amountMinor / 100, {
                currency: offer.currency
              })}
              listPriceText={formatters.currency(offer.listPriceMinor / 100, {
                currency: offer.currency
              })}
              ageText={formatters.relative(new Date(offer.createdAt).toISOString())}
              acceptLabel={`Accept ${formatters.currency(offer.amountMinor / 100, {
                currency: offer.currency
              })}`}
              onAccept={() => onOfferAction(offer.id, "accept")}
              onCounter={() => onCounterRequest(offer)}
              onDecline={() => onOfferAction(offer.id, "decline")}
              onPressItem={() => onOpenItem(Number(offer.listingId), offer.itemTitle)}
              reducedMotion={reducedMotion}
            />
          ))}
        </Animated.View>
      ) : null}

      {/* 3. Your items */}
      <Animated.View style={entrance.styleFor(SELLING_SLOT.tabs)}>
        <SectionHeader title="Your items" />
        <View style={styles.tabRow} accessibilityRole="tablist">
          {visibleSellingTabs(tabCounts, tab).map((entry) => {
            const selected = entry.key === tab;
            return (
              <Pressable
                key={entry.key}
                onPress={() => onTabChange(entry.key)}
                style={[styles.tab, selected && styles.tabActive]}
                accessibilityRole="tab"
                accessibilityState={{ selected }}
                accessibilityLabel={`${entry.label}, ${tabCounts[entry.key]} items`}
              >
                <Text style={[styles.tabText, selected && styles.tabTextActive]}>
                  {entry.label} {tabCounts[entry.key]}
                </Text>
              </Pressable>
            );
          })}
        </View>
      </Animated.View>

      <Animated.View style={entrance.styleFor(SELLING_SLOT.items)}>
        {listingsError ? (
          <StoreSectionError
            message={listingsError}
            onRetry={onRetryListings}
            reducedMotion={reducedMotion}
          />
        ) : empty ? (
          <View style={styles.emptyCard}>
            <Text style={styles.emptyTitle}>Nothing listed yet.</Text>
            {/* "Buyers nearby will see it" is only claimable once a location
                exists; without one the honest pitch is the location control
                itself. Same rule the buying feed follows. */}
            <Text style={styles.emptyBody}>
              {city
                ? `List something you no longer need. Buyers near ${city} can discover it, and offers land right at the top of this screen.`
                : "List something you no longer need. Set your Marketplace location so nearby buyers can discover it."}
            </Text>
            {!city ? (
              <Pressable
                onPress={onOpenLocation}
                style={styles.emptyAction}
                accessibilityRole="button"
                accessibilityLabel="Set Marketplace location"
              >
                <Text style={styles.emptyActionText}>Set Marketplace location</Text>
              </Pressable>
            ) : null}
          </View>
        ) : items.length === 0 ? (
          <View style={styles.emptyCard}>
            <Text style={styles.emptyTitle}>Nothing in this tab.</Text>
          </View>
        ) : (
          items.map((item) => (
            <SellingRow
              key={item.id}
              item={item}
              formatters={formatters}
              reducedMotion={reducedMotion}
              onPress={() => onOpenItem(item.id, item.title)}
              onEdit={() =>
                navigation.navigate("SellerStore", { mode: "create", title: "Edit listing" })
              }
            />
          ))
        )}

        {!expanded && totalInTab > items.length ? (
          <Pressable
            onPress={onExpand}
            style={styles.seeAll}
            accessibilityRole="button"
            accessibilityLabel={`See all ${totalInTab} items`}
          >
            <Text style={styles.seeAllText}>See all {totalInTab} items ›</Text>
          </Pressable>
        ) : null}
      </Animated.View>

      {/* 5. Boost. Only ever for one item, only ever a stale one. */}
      {boost ? (
        <Animated.View style={entrance.styleFor(SELLING_SLOT.boost)}>
          <BoostCard item={boost} reducedMotion={reducedMotion} />
        </Animated.View>
      ) : null}

      {/* 6. More
          Two per row, laid out by the grid rather than by this screen. These
          four tiles used to sit in a wrapping row, which gave each of them a
          quarter of the width and rendered the labels as "S…" and "M…". The
          row count is no longer this screen's decision — see the note in
          `StoreQuickLinkTile.tsx`. */}
      <Animated.View style={entrance.styleFor(SELLING_SLOT.more)}>
        <StoreQuickLinkGrid
          reducedMotion={reducedMotion}
          items={[
            {
              icon: "chatbubble-ellipses-outline",
              label: "Buyer messages",
              // MOCK-DATA: no unread counter scoped to marketplace conversations.
              subtitle: "Open your inbox",
              onPress: () => navigation.navigate("Messenger"),
              reducedMotion
            },
            {
              icon: "star-outline",
              label: "Seller rating",
              // MOCK-DATA: no seller review aggregate on any endpoint. But an
              // absent rating is a normal state for a new seller, not a locked
              // feature — informational, no lock, no dimming.
              subtitle: "Not enough completed sales yet",
              informational: true,
              reducedMotion
            },
            {
              icon: "location-outline",
              label: "Meetup spots",
              // MOCK-DATA: no meetup-spot storage, so no saved spots — but the
              // safety guidance itself is real content, not a dead end.
              subtitle: "Safe exchange tips",
              onPress: onOpenMeetupSafety,
              reducedMotion
            },
            {
              icon: "receipt-outline",
              label: "Sold history",
              subtitle: "Your sales and payouts",
              onPress: () =>
                navigation.navigate("SellerStore", { mode: "orders", title: "Sales" }),
              reducedMotion
            }
          ]}
        />
      </Animated.View>

      {/* 7. Footer CTA */}
      <Animated.View style={entrance.styleFor(SELLING_SLOT.cta)}>
        <Animated.View style={ctaPress.style}>
          <Pressable
            onPress={onCompose}
            onPressIn={ctaPress.onPressIn}
            onPressOut={ctaPress.onPressOut}
            accessibilityRole="button"
            accessibilityLabel="List an item"
          >
            <LinearGradient
              colors={[storeLight.cta.from, storeLight.cta.to]}
              style={styles.footerCta}
            >
              <Text style={[styles.footerCtaText, { color: storeLight.cta.text }]}>
                ＋ List an item
              </Text>
            </LinearGradient>
          </Pressable>
        </Animated.View>
      </Animated.View>
    </ScrollView>
  );
}

/**
 * One row in "Your items".
 *
 * The flag line is the row's only editorial claim, and each of the three states
 * is derived from something real: `attention` from sales in the last seven days,
 * `stale` from `created_at`, `rate_buyer` from a completed order. When none of
 * them holds, the line is absent — not filled with an encouraging guess.
 */
function SellingRow({
  item,
  formatters,
  reducedMotion,
  onPress,
  onEdit
}: {
  item: SellingItem;
  formatters: Formatters;
  reducedMotion: boolean;
  onPress: () => void;
  onEdit: () => void;
}) {
  const press = useStorePress(reducedMotion, 0.99);

  /**
   * The SOLD overlay wipes in on the transition, not on every render. The hook
   * watches for the `false → true` edge, so a row already sold when the list
   * first paints shows the banner at rest, and a row that sells while the list
   * is on screen wipes in place — which is the whole point: no reload, the row
   * the seller is already looking at changes underneath them.
   */
  const soldWipe = useMarketplaceSoldWipe(reducedMotion, item.sold);

  // Engagement is entirely unbacked. Rather than "0 views · 0 saves", which is a
  // claim, the line shows only what exists — and disappears when nothing does.
  const engagement = [
    item.views == null ? null : `${formatters.count(item.views)} views`,
    item.saves == null ? null : `${formatters.count(item.saves)} saves`,
    item.offerCount == null ? null : `${formatters.count(item.offerCount)} offers`
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <Animated.View style={press.style}>
      <Pressable
        onPress={onPress}
        onPressIn={press.onPressIn}
        onPressOut={press.onPressOut}
        style={styles.row}
        accessibilityRole="button"
        accessibilityLabel={`${item.title}, ${item.priceLabel}${item.sold ? ", sold" : ""}`}
      >
        <View style={styles.thumbWrap}>
          {item.thumbnailUrl ? (
            <Image source={{ uri: item.thumbnailUrl }} style={styles.thumb} resizeMode="cover" />
          ) : (
            <View style={[styles.thumb, styles.thumbFallback]} />
          )}
          {item.sold ? (
            <Animated.View
              style={[
                styles.soldBanner,
                {
                  opacity: soldWipe,
                  transform: [
                    {
                      // Scales along the row's width from the left edge, so it
                      // reads as a banner being stamped across the thumbnail
                      // rather than fading in from nowhere.
                      scaleX: soldWipe
                    }
                  ]
                }
              ]}
            >
              <Text style={styles.soldBannerText}>SOLD</Text>
            </Animated.View>
          ) : null}
        </View>

        <View style={styles.rowBody}>
          <Text style={styles.rowTitle} numberOfLines={1}>
            {item.title}
          </Text>
          <View style={styles.priceLine}>
            <Text style={styles.rowPrice}>{item.priceLabel}</Text>
            {item.originalPriceLabel ? (
              <Text style={styles.rowWas}>{item.originalPriceLabel}</Text>
            ) : null}
          </View>
          {engagement ? <Text style={styles.rowMeta}>{engagement}</Text> : null}

          {item.flag === "attention" ? (
            <Text style={styles.flagGood}>Getting attention — priced well</Text>
          ) : item.flag === "stale" ? (
            <Text style={styles.flagWarn}>Listing is getting stale · Renew or drop price</Text>
          ) : item.flag === "rate_buyer" ? (
            <Pressable
              onPress={onEdit}
              style={styles.ratePill}
              accessibilityRole="button"
              accessibilityLabel={`Leave a rating for the buyer of ${item.title}`}
            >
              <Text style={styles.ratePillText}>Rate buyer</Text>
            </Pressable>
          ) : null}
        </View>

        <Pressable
          onPress={onEdit}
          style={styles.editButton}
          hitSlop={8}
          accessibilityRole="button"
          accessibilityLabel={`Edit ${item.title}`}
        >
          <Text style={styles.editText}>Edit</Text>
        </Pressable>
      </Pressable>
    </Animated.View>
  );
}

/**
 * The Boost promo.
 *
 * The button carries no price. The brief asks for "Boost $X.XX", but nothing in
 * this app prices a boost — there is no product, no SKU and no payment path for
 * one — and a made-up price on a button that takes money is the single worst
 * place to invent a number. It reads "See boost options" and is disabled behind
 * `MARKETPLACE_BOOST_ENABLED` until a real price and the existing payment
 * infrastructure are wired to it.
 */
function BoostCard({ item, reducedMotion }: { item: SellingItem; reducedMotion: boolean }) {
  const bob = useStoreAmbient(MARKETPLACE_AMBIENT.rocketBob, reducedMotion, {
    resetTo: 0,
    pingPong: true
  });

  return (
    <LinearGradient
      colors={[marketplaceLight.boost.from, marketplaceLight.boost.to]}
      style={styles.boostCard}
    >
      <Animated.View
        style={{
          transform: [{ translateY: bob.interpolate({ inputRange: [0, 1], outputRange: [2, -4] }) }]
        }}
        accessibilityElementsHidden
        importantForAccessibility="no"
      >
        <Ionicons name="rocket-outline" size={22} color={storeLight.status.warning} />
      </Animated.View>
      <View style={styles.boostBody}>
        <Text style={styles.boostTitle} numberOfLines={2}>
          “{item.title}” has been quiet
        </Text>
        <Text style={styles.boostSub}>
          A boost puts it at the top of nearby feeds and marks it Featured for a few days.
        </Text>
      </View>
      <Pressable
        disabled={!MARKETPLACE_BOOST_ENABLED}
        style={[styles.boostButton, !MARKETPLACE_BOOST_ENABLED && styles.boostButtonOff]}
        accessibilityRole="button"
        accessibilityState={{ disabled: !MARKETPLACE_BOOST_ENABLED }}
        accessibilityLabel={
          MARKETPLACE_BOOST_ENABLED
            ? `See boost options for ${item.title}`
            : "Boost is not available yet"
        }
        onPress={() => undefined}
      >
        <Text style={styles.boostButtonText}>
          {MARKETPLACE_BOOST_ENABLED ? "See boost options" : "Coming soon"}
        </Text>
      </Pressable>
    </LinearGradient>
  );
}

/* ------------------------------------------------------------------ *
 * Buying mode
 * ------------------------------------------------------------------ */

function BuyingPane({
  loading,
  refreshing,
  onRefresh,
  entrance,
  reducedMotion,
  city,
  onOpenLocation,
  categories,
  category,
  onCategoryChange,
  items,
  total,
  onShowMore,
  feedError,
  onRetryFeed,
  savedSet,
  visibleSet,
  confirmingId,
  onToggleSave,
  onAction,
  onOpenItem,
  onViewableItemsChanged,
  bottomPad
}: {
  loading: boolean;
  refreshing: boolean;
  onRefresh: () => void;
  entrance: ReturnType<typeof useStoreEntrance>;
  reducedMotion: boolean;
  city: string | null;
  onOpenLocation: () => void;
  categories: readonly { key: string; label: string }[];
  category: string;
  onCategoryChange: (next: string) => void;
  items: readonly BuyingItem[];
  total: number;
  onShowMore: () => void;
  feedError: string | null;
  onRetryFeed: () => void;
  savedSet: Set<number>;
  visibleSet: Set<number>;
  confirmingId: number | null;
  onToggleSave: (item: BuyingItem) => void;
  onAction: (item: BuyingItem) => void;
  onOpenItem: (listingId: number, title: string) => void;
  onViewableItemsChanged: (info: { viewableItems: ViewToken[] }) => void;
  bottomPad: number;
}) {
  const rail = useMemo(
    () => [{ key: CATEGORY_ALL, label: "For you" }, ...categories],
    [categories]
  );

  /**
   * Recomputed here rather than passed down as strings. It is a pure function
   * of the city and the category, so the heading here and the strip in the
   * header derive from the same inputs and cannot disagree.
   */
  const place = useMemo(
    () => marketplaceLocation({ city, categoryFiltered: category !== CATEGORY_ALL }),
    [city, category]
  );

  /** Every empty-state action maps to a control this screen really has. */
  const runLocationAction = (key: MarketplaceLocationActionKey) => {
    if (key === "clear_category") onCategoryChange(CATEGORY_ALL);
    else onOpenLocation();
  };

  return (
    <View style={styles.buyingRoot}>
      {/* The rail sits outside the list on purpose: the brief requires that a
          feed failure never hide the category rail or the search field, and the
          reliable way to guarantee that is for them not to be part of the thing
          that failed. */}
      <Animated.View style={entrance.styleFor(BUYING_SLOT.rail)}>
        <CategoryChipRail categories={rail} active={category} onChange={onCategoryChange} />
      </Animated.View>

      {loading ? (
        <ScrollView contentContainerStyle={[styles.scroll, { paddingBottom: bottomPad }]}>
          <View style={styles.gridSkeleton}>
            {[0, 1, 2, 3, 4, 5].map((key) => (
              <View key={key} style={styles.gridCardSkeleton}>
                <StoreSkeletonBlock width="100%" height={140} reducedMotion={reducedMotion} />
                <StoreSkeletonBlock width="45%" height={14} reducedMotion={reducedMotion} />
                <StoreSkeletonBlock width="85%" height={12} reducedMotion={reducedMotion} />
                <StoreSkeletonBlock width="60%" height={10} reducedMotion={reducedMotion} />
              </View>
            ))}
          </View>
        </ScrollView>
      ) : (
        <FlatList
          data={items}
          keyExtractor={(item) => String(item.id)}
          numColumns={2}
          columnWrapperStyle={styles.gridRow}
          contentContainerStyle={[styles.scroll, { paddingBottom: bottomPad }]}
          refreshControl={
            <RefreshControl
              refreshing={refreshing}
              onRefresh={onRefresh}
              tintColor={storeLight.text.link}
            />
          }
          // Virtualization plus a viewability feed into every card's `visible`
          // prop, which is what parks the ambient glows off-screen.
          initialNumToRender={6}
          maxToRenderPerBatch={6}
          windowSize={5}
          removeClippedSubviews
          onViewableItemsChanged={onViewableItemsChanged}
          ListHeaderComponent={
            <View>
              <Animated.View style={entrance.styleFor(BUYING_SLOT.alert)}>
                {/* MOCK-DATA: saved searches have no backend, so this never
                    renders. Wired rather than removed so turning the data on is
                    a data change, not a UI change. */}
                <SavedSearchAlert
                  searchCount={0}
                  matchCount={0}
                  onPress={() => undefined}
                  reducedMotion={reducedMotion}
                />
              </Animated.View>
              {/* Only claims "near you" when a location is set — the same
                  derivation the strip uses, so they cannot contradict. */}
              <SectionHeader title={place.feedTitle} />
            </View>
          }
          ListEmptyComponent={
            feedError ? (
              <StoreSectionError
                message={feedError}
                onRetry={onRetryFeed}
                reducedMotion={reducedMotion}
              />
            ) : (
              <View style={styles.emptyCard}>
                <Text style={styles.emptyTitle}>{place.empty.title}</Text>
                <Text style={styles.emptyBody}>{place.empty.body}</Text>
                {/* Every button here opens a control that really exists: the
                    location sheet, or the category rail's "all" chip. */}
                {place.empty.actions.map((action) => (
                  <Pressable
                    key={action.key}
                    onPress={() => runLocationAction(action.key)}
                    style={styles.emptyAction}
                    accessibilityRole="button"
                    accessibilityLabel={`${action.label}. ${place.empty.title}`}
                  >
                    <Text style={styles.emptyActionText}>{action.label}</Text>
                  </Pressable>
                ))}
              </View>
            )
          }
          ListFooterComponent={
            items.length < total ? (
              <Animated.View style={entrance.styleFor(BUYING_SLOT.more)}>
                <Pressable
                  onPress={onShowMore}
                  style={styles.showMore}
                  accessibilityRole="button"
                  accessibilityLabel={`${place.moreLabel} listings`}
                >
                  <Text style={styles.showMoreText}>{place.moreLabel}</Text>
                </Pressable>
              </Animated.View>
            ) : null
          }
          renderItem={({ item, index }) => (
            <View style={styles.gridCell}>
              <ItemGridCard
                id={String(item.id)}
                title={item.title}
                priceText={item.priceLabel}
                originalPriceText={item.originalPriceLabel}
                imageUrl={item.imageUrl}
                category={item.category}
                badge={item.badge}
                metaText={metaLineFor(item)}
                metaIsFulfillment={item.distanceMeters == null}
                saved={savedSet.has(item.id)}
                onToggleSave={() => onToggleSave(item)}
                onPress={() => onOpenItem(item.id, item.title)}
                action={
                  item.action
                    ? {
                        variant: item.action,
                        label: item.action === "cart" ? "Add to cart" : "Make offer",
                        accessibilityLabel:
                          item.action === "cart"
                            ? `Add ${item.title} to cart, ${item.priceLabel}`
                            : `Make an offer on ${item.title}, listed at ${item.priceLabel}`,
                        confirming: confirmingId === item.id,
                        onPress: () => onAction(item)
                      }
                    : null
                }
                visible={visibleSet.has(item.id)}
                index={index}
                reducedMotion={reducedMotion}
              />
            </View>
          )}
        />
      )}
    </View>
  );
}

/**
 * The card's meta line.
 *
 * Distance first when there is one — there is not, because listings carry no
 * geo — then the fulfillment promise, which is real and comes from
 * `delivery_type`. Seller name last. Never a star rating, because no endpoint
 * aggregates one.
 */
function metaLineFor(item: BuyingItem): string {
  if (item.fulfillment === "platform") return "Ships";
  if (item.fulfillment === "local") return "Local pickup";
  if (item.fulfillment === "both") return "Ships or pickup";
  return item.sellerName;
}

/* ------------------------------------------------------------------ *
 * Small pieces
 * ------------------------------------------------------------------ */

function SectionHeader({
  title,
  actionLabel,
  onAction
}: {
  title: string;
  actionLabel?: string;
  onAction?: () => void;
}) {
  return (
    <View style={styles.sectionHeader}>
      <Text style={styles.sectionTitle} accessibilityRole="header">
        {title}
      </Text>
      {actionLabel && onAction ? (
        <Pressable onPress={onAction} accessibilityRole="button" accessibilityLabel={actionLabel}>
          <Text style={styles.sectionAction}>{actionLabel} ›</Text>
        </Pressable>
      ) : null}
    </View>
  );
}

function SummaryChip({
  label,
  value,
  amber = false,
  disabled = false,
  onPress,
  reducedMotion,
  arrivalDelay = 0
}: {
  label: string;
  value: string;
  amber?: boolean;
  disabled?: boolean;
  onPress?: () => void;
  reducedMotion: boolean;
  arrivalDelay?: number;
}) {
  const press = useStorePress(reducedMotion, 0.97);

  /**
   * The label lands with the card; the number arrives after it. The hook's own
   * delay already offsets it to half the entrance, and `arrivalDelay` staggers
   * the three chips against each other so they do not all land at once.
   */
  const arrival = useStoreValueArrival(reducedMotion, arrivalDelay);
  const valueStyle = {
    opacity: arrival,
    transform: [
      {
        translateY: arrival.interpolate({ inputRange: [0, 1], outputRange: [8, 0] })
      }
    ]
  };

  return (
    <Animated.View style={[styles.chip, amber && styles.chipAmber, press.style]}>
      <Pressable
        onPress={onPress}
        onPressIn={press.onPressIn}
        onPressOut={press.onPressOut}
        disabled={disabled || !onPress}
        accessibilityRole={disabled || !onPress ? "text" : "button"}
        accessibilityLabel={`${label}: ${value}`}
        style={styles.chipInner}
      >
        <Text style={styles.chipLabel} numberOfLines={2}>
          {label}
        </Text>
        <Animated.Text style={[styles.chipValue, amber && styles.chipValueAmber, valueStyle]}>
          {value}
        </Animated.Text>
      </Pressable>
    </Animated.View>
  );
}

/** Heart and cart, for the buying header's top row. */
function BuyingHeaderCounts({
  savedCount,
  cartCount,
  onSaved,
  onCart,
  reducedMotion
}: {
  savedCount: number;
  cartCount: number;
  onSaved: () => void;
  onCart: () => void;
  reducedMotion: boolean;
}) {
  const press = useStorePress(reducedMotion, 0.94);

  /**
   * The brief asks for a confirmation that flies from the card to the cart, or
   * failing that, a spring on the badge. A flight would need the grid card's
   * screen position measured and an overlay above the header — real work in a
   * stack with no shared-element transition — so this is the stated fallback:
   * the badge springs when the count crosses from nothing to something.
   */
  const cartPop = useStoreBadgePop(reducedMotion, cartCount > 0);
  const savedPop = useStoreBadgePop(reducedMotion, savedCount > 0);

  return (
    <View style={styles.headerCounts}>
      <Pressable
        onPress={onSaved}
        style={styles.headerIcon}
        hitSlop={6}
        accessibilityRole="button"
        accessibilityLabel={`Saved items, ${savedCount} saved`}
      >
        <Ionicons name="heart-outline" size={22} color={storeLight.text.onDark} />
        {savedCount > 0 ? (
          <Animated.View style={[styles.headerBadge, { transform: [{ scale: savedPop }] }]}>
            <Text style={styles.headerBadgeText}>{savedCount > 99 ? "99+" : savedCount}</Text>
          </Animated.View>
        ) : null}
      </Pressable>
      <Animated.View style={press.style}>
        <Pressable
          onPress={onCart}
          onPressIn={press.onPressIn}
          onPressOut={press.onPressOut}
          style={styles.headerIcon}
          hitSlop={6}
          accessibilityRole="button"
          accessibilityLabel={`Cart, ${cartCount} items`}
        >
          <Ionicons name="cart-outline" size={22} color={storeLight.text.onDark} />
          {cartCount > 0 ? (
            <Animated.View style={[styles.headerBadge, { transform: [{ scale: cartPop }] }]}>
              <Text style={styles.headerBadgeText}>{cartCount > 99 ? "99+" : cartCount}</Text>
            </Animated.View>
          ) : null}
        </Pressable>
      </Animated.View>
    </View>
  );
}

/**
 * The location strip.
 *
 * MOCK-DATA: there is no stored radius, no city, and no geo on a listing. The
 * strip says so plainly rather than printing "Within 10 mi of San Francisco",
 * which would be a fabricated claim about where the user is and what they are
 * being shown.
 *
 * The strip is a working control now that the city is a stored preference: it
 * states what the feed is showing and opens the location sheet. The action
 * label is rendered so the row reads as pressable — a bare line next to a pin
 * looked tappable when it was not; the reverse would be as bad.
 */
function LocationStrip({
  text,
  actionLabel,
  onPress
}: {
  text: string;
  actionLabel: string;
  onPress: () => void;
}) {
  return (
    <Pressable
      style={styles.locationStrip}
      onPress={onPress}
      accessibilityRole="button"
      accessibilityLabel={`${text}. ${actionLabel}`}
    >
      <Ionicons
        name="location-outline"
        size={14}
        color={storeLight.text.onDarkMuted}
        accessibilityElementsHidden
        importantForAccessibility="no"
      />
      <View style={styles.locationTextGroup}>
        <Text style={styles.locationText}>{text}</Text>
      </View>
      <Text style={styles.locationAction}>{actionLabel}</Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: storeLight.bg.page },
  panes: { flex: 1 },
  pane: { ...StyleSheet.absoluteFillObject },
  /** Kept mounted so each mode's scroll offset survives the toggle. */
  paneHidden: { display: "none" },
  headerBelow: { gap: 8, marginTop: 10 },
  headerCounts: { flexDirection: "row", alignItems: "center" },
  headerIcon: {
    minWidth: storeLight.size.tapTarget,
    minHeight: storeLight.size.tapTarget,
    alignItems: "center",
    justifyContent: "center"
  },
  headerBadge: {
    position: "absolute",
    top: 6,
    right: 6,
    minWidth: 16,
    height: 16,
    paddingHorizontal: 3,
    borderRadius: 8,
    backgroundColor: storeLight.cta.from,
    alignItems: "center",
    justifyContent: "center"
  },
  headerBadgeText: { fontSize: 10, fontWeight: "800", color: storeLight.text.primary },
  locationStrip: {
    flexDirection: "row",
    alignItems: "center",
    gap: 5,
    minHeight: storeLight.size.tapTarget - 16
  },
  locationTextGroup: { flex: 1, gap: 1 },
  locationText: { fontSize: 12, color: storeLight.text.onDarkMuted },
  locationAction: { fontSize: 12, fontWeight: "700", color: storeLight.text.onDarkMuted },

  scroll: { padding: storeLight.space.card, gap: 12 },
  buyingRoot: { flex: 1 },

  chipRow: { flexDirection: "row", gap: 8 },
  chip: {
    flex: 1,
    borderRadius: storeLight.radius.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: storeLight.border.hairline,
    backgroundColor: storeLight.bg.card
  },
  chipAmber: {
    backgroundColor: marketplaceLight.boost.from,
    borderColor: marketplaceLight.boost.border
  },
  chipInner: { minHeight: 72, padding: 10, justifyContent: "space-between" },
  chipLabel: { fontSize: 11, color: storeLight.text.muted, fontWeight: "600" },
  chipValue: { fontSize: 22, fontWeight: "800", color: storeLight.text.primary },
  chipValueAmber: { color: storeLight.status.warning },
  chipSkeleton: {
    flex: 1,
    gap: 8,
    minHeight: 72,
    padding: 10,
    borderRadius: storeLight.radius.card,
    backgroundColor: storeLight.bg.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: storeLight.border.hairline,
    justifyContent: "center"
  },
  offerSkeleton: {
    gap: 8,
    padding: storeLight.space.card,
    borderRadius: storeLight.radius.card,
    backgroundColor: storeLight.bg.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: storeLight.border.hairline
  },

  sectionHeader: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    minHeight: 32
  },
  sectionTitle: { fontSize: 16, fontWeight: "800", color: storeLight.text.primary },
  sectionAction: { fontSize: 13, fontWeight: "700", color: storeLight.text.link },

  /* Wraps: up to eight tabs can be visible when moderation states exist, and a
     clipped "Removed" tab would hide exactly the listings the seller most
     needs to know about. */
  tabRow: { flexDirection: "row", flexWrap: "wrap", gap: 6, marginTop: 4 },
  tab: {
    minHeight: 34,
    paddingHorizontal: 12,
    borderRadius: storeLight.radius.pill,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: storeLight.border.hairline,
    backgroundColor: storeLight.bg.card,
    alignItems: "center",
    justifyContent: "center"
  },
  tabActive: {
    backgroundColor: marketplaceLight.chip.activeBg,
    borderColor: marketplaceLight.chip.activeBg
  },
  tabText: { fontSize: 12, fontWeight: "700", color: storeLight.text.muted },
  tabTextActive: { color: marketplaceLight.chip.activeText },

  row: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    padding: storeLight.space.card,
    borderRadius: storeLight.radius.card,
    backgroundColor: storeLight.bg.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: storeLight.border.hairline
  },
  thumbWrap: { width: 64, height: 64, borderRadius: storeLight.radius.thumb, overflow: "hidden" },
  thumb: { width: 64, height: 64 },
  thumbFallback: { backgroundColor: storeLight.bg.skeleton },
  soldBanner: {
    ...StyleSheet.absoluteFillObject,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: marketplaceLight.badge.soldOverlay
  },
  soldBannerText: {
    fontSize: 11,
    fontWeight: "800",
    letterSpacing: 1,
    color: marketplaceLight.badge.soldText
  },
  rowBody: { flex: 1, gap: 2 },
  rowTitle: { fontSize: 14, fontWeight: "700", color: storeLight.text.primary },
  priceLine: { flexDirection: "row", alignItems: "baseline", gap: 6 },
  rowPrice: { fontSize: 14, fontWeight: "800", color: storeLight.text.primary },
  rowWas: {
    fontSize: 12,
    color: storeLight.text.muted,
    textDecorationLine: "line-through"
  },
  rowMeta: { fontSize: 11, color: storeLight.text.muted },
  flagGood: { fontSize: 11, fontWeight: "700", color: storeLight.status.success },
  flagWarn: { fontSize: 11, fontWeight: "700", color: storeLight.status.warning },
  ratePill: {
    alignSelf: "flex-start",
    minHeight: 30,
    paddingHorizontal: 10,
    justifyContent: "center",
    borderRadius: storeLight.radius.pill,
    backgroundColor: storeLight.status.success
  },
  ratePillText: { fontSize: 12, fontWeight: "700", color: "#FFFFFF" },
  editButton: {
    minWidth: 44,
    minHeight: 44,
    alignItems: "center",
    justifyContent: "center"
  },
  editText: { fontSize: 13, fontWeight: "700", color: storeLight.text.link },

  seeAll: { minHeight: 44, justifyContent: "center", paddingHorizontal: 4 },
  seeAllText: { fontSize: 13, fontWeight: "700", color: storeLight.text.link },

  emptyCard: {
    gap: 6,
    padding: storeLight.space.card,
    borderRadius: storeLight.radius.card,
    backgroundColor: storeLight.bg.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: storeLight.border.hairline
  },
  emptyTitle: { fontSize: 15, fontWeight: "800", color: storeLight.text.primary },
  emptyBody: { fontSize: 13, lineHeight: 19, color: storeLight.text.muted },
  emptyAction: {
    minHeight: storeLight.size.tapTarget,
    alignSelf: "flex-start",
    justifyContent: "center",
    paddingHorizontal: 14,
    marginTop: 4,
    borderRadius: storeLight.radius.pill,
    borderWidth: 1,
    borderColor: storeLight.border.secondaryButton
  },
  emptyActionText: { fontSize: 13, fontWeight: "700", color: storeLight.text.primary },

  boostCard: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    padding: storeLight.space.card,
    borderRadius: storeLight.radius.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: marketplaceLight.boost.border
  },
  boostBody: { flex: 1, gap: 2 },
  boostTitle: { fontSize: 13, fontWeight: "800", color: storeLight.text.primary },
  boostSub: { fontSize: 11, lineHeight: 16, color: storeLight.text.muted },
  boostButton: {
    minHeight: 36,
    paddingHorizontal: 12,
    borderRadius: storeLight.radius.pill,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: storeLight.cta.from
  },
  boostButtonOff: { backgroundColor: storeLight.bg.skeleton },
  boostButtonText: { fontSize: 12, fontWeight: "800", color: storeLight.text.primary },

  /* `moreGrid` is gone. It was `flexDirection: "row", flexWrap: "wrap"` around
     four `flex: 1` tiles, which is how "Seller rating" became "S…". The grid
     component owns that layout now. */

  gridRow: { gap: marketplaceLight.grid.gutter },
  gridCell: { flex: 1 },
  gridSkeleton: { flexDirection: "row", flexWrap: "wrap", gap: marketplaceLight.grid.gutter },
  gridCardSkeleton: {
    width: "47%",
    gap: 6,
    padding: 8,
    borderRadius: marketplaceLight.grid.radius,
    backgroundColor: storeLight.bg.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: storeLight.border.hairline
  },

  showMore: {
    minHeight: 44,
    alignItems: "center",
    justifyContent: "center",
    borderRadius: storeLight.radius.pill,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: storeLight.border.hairline,
    backgroundColor: storeLight.bg.card
  },
  showMoreText: { fontSize: 13, fontWeight: "700", color: storeLight.text.link },

  footerCta: {
    minHeight: 48,
    alignItems: "center",
    justifyContent: "center",
    borderRadius: storeLight.radius.pill
  },
  footerCtaText: { fontSize: 15, fontWeight: "800" },

  /* Counter sheet */
  sheetScrim: { flex: 1, backgroundColor: "rgba(19,26,34,0.5)" },
  sheet: {
    backgroundColor: storeLight.bg.card,
    borderTopLeftRadius: storeLight.radius.card,
    borderTopRightRadius: storeLight.radius.card,
    padding: storeLight.space.gutter,
    paddingBottom: storeLight.space.gutter * 2,
    gap: 10
  },
  sheetTitle: { fontSize: 18, fontWeight: "800", color: storeLight.text.primary },
  sheetSub: { fontSize: 13, lineHeight: 19, color: storeLight.text.muted },
  sheetLabel: {
    fontSize: 12,
    fontWeight: "700",
    color: storeLight.text.primary,
    marginTop: 4
  },
  sheetInput: {
    borderWidth: 1,
    borderColor: storeLight.border.hairline,
    borderRadius: storeLight.radius.control,
    paddingHorizontal: 12,
    // Tall enough to clear the 44pt target on its own, without a hitSlop that
    // would not apply to a text field anyway.
    minHeight: storeLight.size.tapTarget,
    fontSize: 20,
    fontWeight: "700",
    color: storeLight.text.primary
  },
  sheetActions: { flexDirection: "row", gap: 10, marginTop: 6 },
  safetyTip: { gap: 2 },
  sheetButton: {
    flex: 1,
    minHeight: storeLight.size.tapTarget,
    borderRadius: storeLight.radius.pill,
    alignItems: "center",
    justifyContent: "center"
  },
  sheetCancel: {
    borderWidth: 1,
    borderColor: storeLight.border.secondaryButton
  },
  sheetCancelText: { fontSize: 15, fontWeight: "700", color: storeLight.text.primary },
  sheetSend: { backgroundColor: storeLight.status.success },
  // Opacity rather than a grey fill, so the button stays recognisably the same
  // control it will be once the amount is valid.
  sheetSendOff: { opacity: 0.45 },
  sheetSendText: { fontSize: 15, fontWeight: "800", color: storeLight.text.onDark }
});
