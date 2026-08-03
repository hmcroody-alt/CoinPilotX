/**
 * The Business Hub — the seller's front door.
 *
 * THE ONE STRUCTURAL IDEA. This screen does not load. It has no `loading` state,
 * no full-screen spinner and no aggregate model, because it owns no data. Every
 * number on it belongs to a section built by another mission, and each is read
 * through its own binding in `core/hubBindings`. The root component subscribes
 * to NOTHING: it renders a header component, a strip component and a list of
 * card components, and each of those subscribes to exactly the one source it
 * shows. That is why one slow or broken section cannot hold the other nine
 * hostage, and why a sale re-renders the Orders card and the "To fulfil" cell
 * and nothing else.
 *
 * Per-card isolation is therefore structural, not a discipline anyone has to
 * remember: a card that never calls `useHubBinding(storeBinding)` cannot
 * re-render when the store binding publishes. The one place to be careful is
 * this file — adding a `useHubBinding` call to `BusinessHubScreen` itself would
 * quietly re-couple all eleven cards to one source. It has none, deliberately.
 *
 * WHAT THE SCREEN DOES OWN: navigation, motion choreography, and the decision
 * about which sections exist. Even that last one is delegated —
 * `businessOsHubSections()` is the registry every other Business OS surface
 * uses, and `businessOsNavigationArgs` resolves each destination, so a card
 * cannot point somewhere the registry does not sanction and a repointed section
 * moves this screen with it for free.
 *
 * DEVIATION, stated plainly: the design describes a ten-card grid. The registry
 * currently yields ELEVEN backed sections, because the Events mission split the
 * hosted-events manager out from the activity feed. The grid renders what the
 * registry says rather than slicing to ten, since a hard-coded ten would start
 * dropping real sections the moment another one is backed.
 */

import { useCallback, useEffect, useMemo, useState, type ReactElement } from "react";
import {
  Animated,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  View,
  useWindowDimensions
} from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import {
  HUB_CARD_TINTS,
  advertisingStateLine,
  eventsStateLine,
  hubBadge,
  hubContextLine,
  insightsStateLine,
  marketplaceStateLine,
  messagesStateLine,
  ordersStateLine,
  paymentsStateLine,
  profileCompletenessFraction,
  profileStateLine,
  storeStateLine,
  todayStripCells,
  verificationStateLine,
  verificationTick,
  type HubCardKey,
  type HubStripCell,
  type HubTickState
} from "../api/businessHub";
import {
  BUSINESS_OS_SECTIONS,
  businessOsHubSections,
  businessOsNavigationArgs,
  type BusinessOsSection
} from "../api/businessOs";
import { insightsRevenueMajor } from "../api/insightsDashboard";
import { ordersAwaitingSeller } from "../api/ordersDashboard";
import { snapshotFrom, deriveRows, storeHealthCounts } from "../api/storeDashboard";
import { SectionCard, TodayStrip } from "../components/hub";
import { StoreHeader } from "../components/store/StoreHeader";
import {
  adsBinding,
  asOfLabel,
  initHubBindings,
  insights7dBinding,
  insightsTodayBinding,
  ordersBinding,
  profileBinding,
  refreshHubBindings,
  startHubBindings,
  storeBinding,
  useHubBinding,
  verificationBinding,
  type HubBinding
} from "../core/hubBindings";
import { refreshUnreadCounts, useUnreadCounts } from "../core/unreadCounts";
import { useFormatters } from "../i18n/hooks";
import { BOTTOM_NAV_CONTENT_CLEARANCE } from "../navigation/BottomNavVisibility";
import type { RootStackParamList } from "../navigation/types";
import { hubGridColumns, hubLight } from "../theme/hubLight";
import { useLogiNexusReducedMotion } from "../theme/logiNexusMotion";
import { useStoreEntrance } from "../theme/storeMotion";

type Nav = { navigate: (...args: any[]) => void; goBack?: () => void; addListener?: (...args: any[]) => any };

type Props = {
  route?: { params?: RootStackParamList["BusinessOs"] };
  navigation: Nav;
};

/**
 * Entrance slots. Header, strip and banner take the first three; every grid row
 * after them takes one. Rows are indexed by `3 + floor(cardIndex / columns)`,
 * and the pool is sized for the worst case (one column) so the largest text
 * sizes cannot index past the end.
 */
const SLOT = { header: 0, strip: 1, banner: 2, firstRow: 3 } as const;

/* ------------------------------------------------------------------ *
 * Header
 * ------------------------------------------------------------------ */

/**
 * The navy header, as its own subscriber.
 *
 * It reads verification (for the tick and the context line) and the profile
 * (for the business name). Both are here rather than in the screen so a
 * verification transition repaints the tick and NOT the eleven cards below it.
 */
function HubHeader({ onBack, onBell, reducedMotion }: { onBack: () => void; onBell: () => void; reducedMotion: boolean }) {
  const verification = useHubBinding(verificationBinding);
  const profile = useHubBinding(profileBinding);
  const unread = useUnreadCounts();

  const tick = verificationTick(verification.data);
  // The seller's own business name when the application has one, and the
  // generic word when it does not. Never a placeholder name.
  const name = profile.data?.fields?.business_name?.trim() || "Your business";

  return (
    <StoreHeader
      title={name}
      titleAdornment={<VerificationTick state={tick} />}
      query=""
      onQueryChange={() => undefined}
      onSubmitSearch={() => undefined}
      // The hub has nothing of its own to search: every searchable thing lives
      // behind a card, and a field here would either duplicate a section's
      // search or filter nothing. Same reasoning as Insights.
      hideSearch
      onBack={onBack}
      onNotifications={onBell}
      unreadCount={unread.bellCount}
      searchPlaceholder=""
      reducedMotion={reducedMotion}
      below={
        <Text style={styles.context} numberOfLines={1}>
          {hubContextLine(verification.data)}
        </Text>
      }
    />
  );
}

/**
 * The tick beside the business name. Four states, and three of them are visible:
 * an unverified seller gets no mark at all rather than a grey one, because a
 * greyed tick reads as "failed" when the truth is "not started".
 */
function VerificationTick({ state }: { state: HubTickState }) {
  if (state === "none") return null;
  const icon = state === "problem" ? "alert-circle" : state === "review" ? "time" : "checkmark-circle";
  const color =
    state === "problem" ? hubLight.tone.warn : state === "review" ? hubLight.tone.review : hubLight.tone.green;
  const label =
    state === "problem" ? "Verification needs you" : state === "review" ? "Verification in review" : "Verified";
  return (
    <Ionicons
      name={icon as never}
      size={16}
      color={color}
      accessibilityRole="image"
      accessibilityLabel={label}
    />
  );
}

/* ------------------------------------------------------------------ *
 * Today strip
 * ------------------------------------------------------------------ */

/**
 * The four-cell strip, as its own subscriber: today's insights, orders, and the
 * shared unread store. It reads three sources and eleven cards read none of
 * them, so a refresh here repaints four small numbers and nothing else.
 */
function HubStrip({
  onOpen,
  reducedMotion
}: {
  onOpen: (cell: HubStripCell) => void;
  reducedMotion: boolean;
}) {
  const formatters = useFormatters();
  const today = useHubBinding(insightsTodayBinding);
  const orders = useHubBinding(ordersBinding);
  const unread = useUnreadCounts();

  const cells = useMemo(() => {
    // The owner decides which field is "sales" and what unit it is in; this
    // screen only hands the result to the app's shared currency formatter, the
    // same one the Insights screen uses. No money arithmetic happens here.
    const revenue = insightsRevenueMajor(today.data?.summary ?? null);
    const salesLabel = revenue
      ? formatters.currency(revenue.amount, { currency: revenue.currency, maximumFractionDigits: 0 })
      : null;

    return todayStripCells({
      salesLabel,
      awaitingFulfilment: orders.data ? ordersAwaitingSeller(orders.data.orders) : null,
      // No offers endpoint exists; `todayStripCells` renders "—" and still links.
      openOffers: null,
      unread: unread.loadedAt ? unread.messageCount : null
    });
  }, [formatters, orders.data, today.data, unread.loadedAt, unread.messageCount]);

  return <TodayStrip cells={cells} onPressCell={onOpen} reducedMotion={reducedMotion} />;
}

/* ------------------------------------------------------------------ *
 * Offline note
 * ------------------------------------------------------------------ */

/**
 * "Showing your last update · as of 3:07 PM".
 *
 * Deliberately reads two bindings — orders and store — which makes it the one
 * component on the screen that is not single-source. It renders no card state,
 * so the coupling costs a one-line repaint and buys the seller the honest
 * timestamp the offline case requires. It appears only when a source is
 * actually serving from cache; a working screen never shows it.
 */
function HubOfflineNote() {
  const orders = useHubBinding(ordersBinding);
  const store = useHubBinding(storeBinding);

  const stale = [orders, store].filter((snapshot) => snapshot.fromCache && snapshot.loadedAt > 0);
  if (stale.length === 0) return null;
  const oldest = stale.reduce((min, snapshot) => (snapshot.loadedAt < min.loadedAt ? snapshot : min));
  const label = asOfLabel(oldest);
  if (!label) return null;

  return (
    <View style={styles.offline}>
      <Ionicons name="cloud-offline-outline" size={14} color={hubLight.text.muted} />
      <Text style={styles.offlineText}>Showing your last update · {label}</Text>
    </View>
  );
}

/* ------------------------------------------------------------------ *
 * Cards — one connector per card, each bound to one source
 * ------------------------------------------------------------------ */

type CardShellProps = {
  section: BusinessOsSection;
  onPress: () => void;
  reducedMotion: boolean;
  fullWidth: boolean;
};

/** The retry affordance appears ONLY when this card's own source failed. */
function retryFor<T>(binding: HubBinding<T>, status: string): (() => void) | undefined {
  return status === "error" ? () => void binding.refresh() : undefined;
}

function common(props: CardShellProps, cardKey: HubCardKey) {
  return {
    cardKey,
    title: props.section.label,
    subtitle: props.section.blurb,
    icon: props.section.icon,
    onPress: props.onPress,
    reducedMotion: props.reducedMotion,
    fullWidth: props.fullWidth
  };
}

function ProfileCard(props: CardShellProps) {
  const snapshot = useHubBinding(profileBinding);
  return (
    <SectionCard
      {...common(props, "profile")}
      state={profileStateLine(snapshot.data)}
      badge={null}
      progress={profileCompletenessFraction(snapshot.data)}
      onRefresh={retryFor(profileBinding, snapshot.status)}
    />
  );
}

function StoreCard(props: CardShellProps) {
  const snapshot = useHubBinding(storeBinding);
  // `storeHealthCounts` and `deriveRows` are the Store mission's own functions,
  // called on the Store mission's own payload. The hub counts nothing itself,
  // which is what stops this line from disagreeing with the Store screen's
  // banner and tab bar.
  const counts = snapshot.data ? storeHealthCounts(deriveRows(snapshotFrom(snapshot.data))) : null;
  return (
    <SectionCard
      {...common(props, "store")}
      state={storeStateLine(counts)}
      badge={null}
      onRefresh={retryFor(storeBinding, snapshot.status)}
    />
  );
}

function MarketplaceCard(props: CardShellProps) {
  const snapshot = useHubBinding(storeBinding);
  const counts = snapshot.data ? storeHealthCounts(deriveRows(snapshotFrom(snapshot.data))) : null;
  const state = marketplaceStateLine({
    // No offers endpoint exists. Passed as null rather than 0 so the resolver's
    // offers branch is unreachable by data as well as by flag.
    openOffers: null,
    soonestExpiryHours: null,
    activeItems: counts ? counts.active : null
  });
  return (
    <SectionCard
      {...common(props, "marketplace")}
      state={state}
      badge={hubBadge("marketplace", null)}
      onRefresh={retryFor(storeBinding, snapshot.status)}
    />
  );
}

function AdvertisingCard(props: CardShellProps) {
  const snapshot = useHubBinding(adsBinding);
  return (
    <SectionCard
      {...common(props, "advertising")}
      state={advertisingStateLine({
        accounts: snapshot.data?.accounts ?? null,
        analytics: snapshot.data?.analytics ?? null
      })}
      badge={null}
      onRefresh={retryFor(adsBinding, snapshot.status)}
    />
  );
}

function OrdersCard(props: CardShellProps) {
  const snapshot = useHubBinding(ordersBinding);
  const awaiting = snapshot.data ? ordersAwaitingSeller(snapshot.data.orders) : null;
  return (
    <SectionCard
      {...common(props, "orders")}
      state={ordersStateLine(awaiting)}
      badge={hubBadge("orders", awaiting)}
      onRefresh={retryFor(ordersBinding, snapshot.status)}
    />
  );
}

/**
 * Messages reads `core/unreadCounts` directly rather than a binding, because
 * that number already has exactly one store in this app and wrapping it would
 * create a second one that could disagree with every other bell in the product.
 */
function MessagesCard(props: CardShellProps) {
  const unread = useUnreadCounts();
  const count = unread.loadedAt ? unread.messageCount : null;
  return (
    <SectionCard
      {...common(props, "messages")}
      state={messagesStateLine(count)}
      badge={hubBadge("messages", count)}
    />
  );
}

function InsightsCard(props: CardShellProps) {
  const snapshot = useHubBinding(insights7dBinding);
  return (
    <SectionCard
      {...common(props, "insights")}
      state={insightsStateLine(snapshot.data?.summary ?? null)}
      badge={null}
      onRefresh={retryFor(insights7dBinding, snapshot.status)}
    />
  );
}

/**
 * Payments shares the ads binding with Advertising — one owner, one network
 * round, two renderings. The wallet figure here is the same object the
 * Advertising card's account state came from, so the two cannot disagree.
 */
function PaymentsCard(props: CardShellProps) {
  const snapshot = useHubBinding(adsBinding);
  return (
    <SectionCard
      {...common(props, "payments")}
      state={paymentsStateLine(snapshot.data?.wallet ?? null)}
      badge={null}
      onRefresh={retryFor(adsBinding, snapshot.status)}
    />
  );
}

/**
 * Events has no binding: `api/eventsManager` exposes no loader for the seller's
 * OWN hosted events yet (`HUB_EVENTS`). The card renders its static subtitle
 * through the same path every other card uses when its source is quiet, and the
 * day a loader lands this becomes one `useHubBinding` call.
 */
function EventsCard(props: CardShellProps) {
  return <SectionCard {...common(props, "events")} state={eventsStateLine()} badge={null} />;
}

function VerificationCard(props: CardShellProps) {
  const snapshot = useHubBinding(verificationBinding);
  return (
    <SectionCard
      {...common(props, "verification")}
      state={verificationStateLine(snapshot.data)}
      badge={null}
      onRefresh={retryFor(verificationBinding, snapshot.status)}
    />
  );
}

/** Settings has no live state and needs none — it is a destination, not a queue. */
function SettingsCard(props: CardShellProps) {
  return <SectionCard {...common(props, "settings")} state={null} badge={null} />;
}

/**
 * Section key → the component that binds it. A section without an entry renders
 * as a plain card with its static subtitle, so adding a backed section to the
 * registry can never crash the hub — it simply arrives without a state line
 * until someone gives it one.
 */
const CARD_COMPONENTS: Partial<Record<string, (props: CardShellProps) => ReactElement>> = {
  profile: ProfileCard,
  store: StoreCard,
  marketplace: MarketplaceCard,
  advertising: AdvertisingCard,
  orders: OrdersCard,
  messages: MessagesCard,
  insights: InsightsCard,
  payments: PaymentsCard,
  events: EventsCard,
  verification: VerificationCard,
  settings: SettingsCard
};

function StaticCard(props: CardShellProps) {
  const key = (props.section.key in HUB_CARD_TINTS ? props.section.key : "settings") as HubCardKey;
  return <SectionCard {...common(props, key)} state={null} badge={null} />;
}

/* ------------------------------------------------------------------ *
 * Screen
 * ------------------------------------------------------------------ */

export function BusinessHubScreen({ route, navigation }: Props) {
  const reducedMotion = useLogiNexusReducedMotion();
  const insets = useSafeAreaInsets();
  const { fontScale } = useWindowDimensions();
  const [refreshing, setRefreshing] = useState(false);

  const sections = useMemo(() => businessOsHubSections(), []);
  const columns = hubGridColumns(fontScale);
  // Sized for one column, the worst case, so the largest text sizes cannot index
  // past the end of the pool when the grid reflows.
  const entrance = useStoreEntrance(SLOT.firstRow + sections.length, reducedMotion);

  /**
   * Start the bindings once. `initHubBindings` wires the owners' existing sync
   * events; `startHubBindings` hydrates from cache and fetches. Neither returns
   * a promise this screen waits on — there is no moment when "the hub is
   * loaded", and inventing one would recreate the all-or-nothing load.
   */
  useEffect(() => {
    const off = initHubBindings();
    startHubBindings();
    void refreshUnreadCounts();
    return off;
  }, []);

  /** Refresh on focus, and on pull. No new polling loop is added anywhere. */
  useEffect(() => {
    const unsubscribe = navigation.addListener?.("focus", () => {
      void refreshHubBindings();
      void refreshUnreadCounts();
    });
    return typeof unsubscribe === "function" ? unsubscribe : undefined;
  }, [navigation]);

  const onRefresh = useCallback(async () => {
    setRefreshing(true);
    try {
      await Promise.all([refreshHubBindings(), refreshUnreadCounts()]);
    } finally {
      setRefreshing(false);
    }
  }, []);

  const openSection = useCallback(
    (section: BusinessOsSection) => {
      const [target, params] = businessOsNavigationArgs(section);
      navigation.navigate(target, params);
    },
    [navigation]
  );

  /**
   * A strip cell opens the section that OWNS its number, through the same
   * registry the cards use. The cell is a link to where the seller can act, not
   * a statistic — including when its own value is "—", because the section can
   * answer the question even when the hub could not summarise it.
   */
  const openCell = useCallback(
    (cell: HubStripCell) => {
      const key = cell.key === "sales" ? "insights" : cell.key === "fulfil" ? "orders" : cell.key === "offers" ? "marketplace" : "messages";
      const section = BUSINESS_OS_SECTIONS.find((entry) => entry.key === key);
      if (section?.route) openSection(section);
    },
    [openSection]
  );

  return (
    <View style={styles.root}>
      <Animated.View style={entrance.styleFor(SLOT.header)}>
        <HubHeader
          onBack={() => navigation.goBack?.()}
          onBell={() => navigation.navigate("BusinessOsActivity")}
          reducedMotion={reducedMotion}
        />
      </Animated.View>

      <ScrollView
        style={styles.scroll}
        contentContainerStyle={[
          styles.content,
          { paddingBottom: insets.bottom + BOTTOM_NAV_CONTENT_CLEARANCE }
        ]}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} />}
      >
        <Animated.View style={entrance.styleFor(SLOT.strip)}>
          <HubStrip onOpen={openCell} reducedMotion={reducedMotion} />
        </Animated.View>

        <Animated.View style={entrance.styleFor(SLOT.banner)}>
          <HubOfflineNote />
          {/*
            The live banner belongs here, between the strip and the grid. It is
            absent rather than empty: `HUB_EVENTS` is off because nothing loads
            the seller's own hosted events, and `listScheduledLiveEvents` is
            platform-wide discovery — someone else's stream announced as "You're
            live" would be the loudest possible lie on the screen.
          */}
        </Animated.View>

        <View style={[styles.grid, columns === 1 && styles.gridSingle]}>
          {sections.map((section, index) => {
            const Card = CARD_COMPONENTS[section.key] || StaticCard;
            const slot = SLOT.firstRow + Math.floor(index / columns);
            return (
              <Animated.View
                key={section.key}
                style={[columns === 1 ? styles.cellFull : styles.cellHalf, entrance.styleFor(slot)]}
              >
                <Card
                  section={section}
                  onPress={() => openSection(section)}
                  reducedMotion={reducedMotion}
                  fullWidth={columns === 1}
                />
              </Animated.View>
            );
          })}
        </View>
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: hubLight.bg.page },
  scroll: { flex: 1 },
  content: { paddingTop: 12, gap: 12 },
  context: { fontSize: 12, fontWeight: "600", color: hubLight.text.onDarkMuted },
  offline: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    paddingHorizontal: hubLight.space.card
  },
  offlineText: { fontSize: 12, fontWeight: "600", color: hubLight.text.muted },
  grid: {
    flexDirection: "row",
    flexWrap: "wrap",
    justifyContent: "space-between",
    paddingHorizontal: hubLight.space.card
  },
  gridSingle: { flexDirection: "column" },
  cellHalf: { width: "48.5%" },
  cellFull: { width: "100%" }
});

export default BusinessHubScreen;
