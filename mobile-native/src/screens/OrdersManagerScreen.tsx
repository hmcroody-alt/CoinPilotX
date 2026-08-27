/**
 * Orders — the two-sided order surface.
 *
 * One screen, one order model, seen from two ends and switched by the header
 * perspective toggle without a navigation push:
 *
 *   • SELLING — "Orders buyers placed with you." The seller's fulfillment queue,
 *     loaded from the live `/api/pulse/payments/seller/orders` surface. Each card
 *     shows the order timeline, a MOCK ship-by pressure line (declared, Preview-
 *     tagged), and the next fulfillment action. Packing/shipping/handoff writes
 *     are flag-gated previews today because the live surface has no such write —
 *     rather than ship a button that can only no-op, they render disabled with a
 *     reason. "View payout" is a live navigation to the existing wallet screen.
 *
 *   • BUYING — "Your orders." The same orders read from the buyer's end: tracking,
 *     arrival status, the escrow safety panel on pickup orders (only when the
 *     escrow flag is on), and a Buy-again rail.
 *
 * The hard requirement is cross-view consistency: a given order renders the SAME
 * facts from both sides, because both perspectives derive from one `UnifiedOrder`
 * produced in `api/ordersDashboard`. Neither body re-interprets status on its own.
 *
 * No money is computed here and no new payment path is created: amounts are the
 * server's formatted cents, and the only money destination is the existing
 * `BusinessOsPayments` payout screen.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Animated, ScrollView, StyleSheet, Text, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import {
  BuyerOrdersModel,
  OrderPerspective,
  SellerActionKey,
  SellerOrdersModel,
  UnifiedOrder,
  loadBuyerOrdersModel,
  loadSellerOrdersModel,
  ordersAwaitingSeller,
  ordersInTransitForBuyer
} from "../api/ordersDashboard";
import { markMarketplaceOrderCashCollected } from "../api/marketplace";
import {
  BuyAgainRail,
  OrderCard,
  OrdersEmpty,
  OrdersHeader,
  OrdersLoading,
  OrdersOffline
} from "../components/orders";
import { registerSyncInvalidation } from "../core/eventSync";
import { BOTTOM_NAV_CONTENT_CLEARANCE } from "../navigation/BottomNavVisibility";
import { RootStackParamList } from "../navigation/types";
import { ordersLight } from "../theme/ordersLight";
import { useLogiNexusReducedMotion } from "../theme/logiNexusMotion";
import { useStoreEntrance } from "../theme/storeMotion";

const SLOT = { header: 0, offline: 1, rail: 2, list: 3 } as const;
const SECTION_COUNT = Object.keys(SLOT).length;

/** A day in ms — for the Preview ship-by placeholder only. */
const DAY_MS = 24 * 60 * 60 * 1000;

type Props = {
  route?: { params?: RootStackParamList["BusinessOsOrders"] };
  navigation?: { navigate: (...args: any[]) => void; goBack?: () => void };
};

export function OrdersManagerScreen({ route, navigation }: Props) {
  const reducedMotion = useLogiNexusReducedMotion();
  const insets = useSafeAreaInsets();
  const entrance = useStoreEntrance(SECTION_COUNT, reducedMotion);

  const initialPerspective: OrderPerspective =
    route?.params?.perspective === "buyer" ? "buyer" : "seller";
  const [perspective, setPerspective] = useState<OrderPerspective>(initialPerspective);

  const [sellerModel, setSellerModel] = useState<SellerOrdersModel | null>(null);
  const [buyerModel, setBuyerModel] = useState<BuyerOrdersModel | null>(null);
  const [sellerLoading, setSellerLoading] = useState(true);
  const [buyerLoading, setBuyerLoading] = useState(true);
  const [message, setMessage] = useState("");

  const load = useCallback(async (kind: "initial" | "refresh" = "initial") => {
    if (kind === "initial") {
      setSellerLoading(true);
      setBuyerLoading(true);
    }
    const [seller, buyer] = await Promise.allSettled([
      loadSellerOrdersModel(),
      loadBuyerOrdersModel()
    ]);
    if (seller.status === "fulfilled") setSellerModel(seller.value);
    setSellerLoading(false);
    if (buyer.status === "fulfilled") setBuyerModel(buyer.value);
    setBuyerLoading(false);
  }, []);

  useEffect(() => {
    load().catch(() => undefined);
  }, [load]);

  useEffect(() => {
    const refresh = () => load("refresh").catch(() => undefined);
    // A settled order, a new purchase, or a payout all land in the same order
    // ledger both perspectives read, so one subsystem invalidation refreshes both.
    const unregister = [
      registerSyncInvalidation("orders", refresh),
      registerSyncInvalidation("marketplace", refresh)
    ];
    return () => unregister.forEach((fn) => fn());
  }, [load]);

  const changePerspective = useCallback((next: OrderPerspective) => {
    setPerspective(next);
    setMessage("");
  }, []);

  /* -------------------------------------------------------------- *
   * Navigation — every money destination is an existing screen.
   * -------------------------------------------------------------- */

  const openPayout = useCallback(() => {
    navigation?.navigate("BusinessOsPayments", { title: "Payouts" });
  }, [navigation]);

  const openTracking = useCallback(
    (order: UnifiedOrder) => {
      // Never open an external tracking URL directly. The existing buyer order
      // detail screen owns the tracking presentation; route there by id.
      navigation?.navigate("BuyerOrderDetail", { orderId: order.id, title: order.title });
    },
    [navigation]
  );

  const openBuyAgain = useCallback(
    (order: UnifiedOrder) => {
      // Buy-again availability is MOCK (declared gap). Route to Marketplace rather
      // than assert the item is still purchasable or re-charge anything.
      navigation?.navigate("Marketplace", { title: order.title });
    },
    [navigation]
  );

  const onSellerAction = useCallback(
    async (key: SellerActionKey, order: UnifiedOrder) => {
      if (key === "view_payout") {
        openPayout();
        return;
      }
      if (key === "collect_cash") {
        setMessage("Confirming cash...");
        try {
          const result = await markMarketplaceOrderCashCollected(order.id);
          setMessage(
            result.already_settled
              ? "This order was already settled."
              : "Cash collected. The order is marked paid and the buyer was notified."
          );
          await load("refresh");
        } catch (error) {
          setMessage(error instanceof Error ? error.message : "The order could not be settled.");
        }
        return;
      }
      // Pack / ship / handoff reach here only when the fulfillment flag is on and
      // the control is enabled. The live surface has no such write today, so this
      // is reachable only in a flag-on build; state the honest result rather than
      // silently succeed.
      setMessage("Fulfillment actions run against the order service, which isn't enabled in this build.");
    },
    [load, openPayout]
  );

  /* -------------------------------------------------------------- *
   * Derived
   * -------------------------------------------------------------- */

  const sellerOrders = sellerModel?.orders || [];
  const buyerOrders = buyerModel?.orders || [];

  // Both counts come from `api/ordersDashboard` rather than being filtered here,
  // so the Business Hub's Orders card and this strip cannot drift apart.
  const sellerUrgency = useMemo(() => ordersAwaitingSeller(sellerOrders), [sellerOrders]);
  const buyerUrgency = useMemo(() => ordersInTransitForBuyer(buyerOrders), [buyerOrders]);

  /* -------------------------------------------------------------- *
   * Bodies — both mounted, inactive one display:none, so each keeps
   * its own scroll position across a perspective swap.
   * -------------------------------------------------------------- */

  const sellerBody = (
    <ScrollView
      style={perspective === "seller" ? undefined : styles.hidden}
      contentContainerStyle={[styles.content, { paddingBottom: bottomPad(insets.bottom) }]}
      showsVerticalScrollIndicator={false}
    >
      {sellerModel?.offline ? (
        <Animated.View style={entrance.styleFor(SLOT.offline)}>
          <OrdersOffline message={sellerModel.error} />
        </Animated.View>
      ) : null}

      <Animated.View style={[styles.stack, entrance.styleFor(SLOT.list)]}>
        {sellerLoading ? (
          <OrdersLoading />
        ) : sellerOrders.length === 0 ? (
          <OrdersEmpty perspective="seller" />
        ) : (
          sellerOrders.map((order) => (
            <OrderCard
              key={`s-${order.id}`}
              order={order}
              perspective="seller"
              deadline={previewShipBy(order)}
              onAction={onSellerAction}
            />
          ))
        )}
      </Animated.View>

      {message && perspective === "seller" ? (
        <Text style={styles.message} accessibilityLiveRegion="polite">
          {message}
        </Text>
      ) : null}
    </ScrollView>
  );

  const buyerBody = (
    <ScrollView
      style={perspective === "buyer" ? undefined : styles.hidden}
      contentContainerStyle={[styles.content, { paddingBottom: bottomPad(insets.bottom) }]}
      showsVerticalScrollIndicator={false}
    >
      {buyerModel?.offline ? (
        <Animated.View style={entrance.styleFor(SLOT.offline)}>
          <OrdersOffline message={buyerModel.error} />
        </Animated.View>
      ) : null}

      {!buyerLoading && buyerOrders.length ? (
        <Animated.View style={entrance.styleFor(SLOT.rail)}>
          <BuyAgainRail orders={buyerOrders} onPressItem={openBuyAgain} />
        </Animated.View>
      ) : null}

      <Animated.View style={[styles.stack, entrance.styleFor(SLOT.list)]}>
        {buyerLoading ? (
          <OrdersLoading />
        ) : buyerOrders.length === 0 ? (
          <OrdersEmpty perspective="buyer" />
        ) : (
          buyerOrders.map((order) => (
            <OrderCard
              key={`b-${order.id}`}
              order={order}
              perspective="buyer"
              onOpenTracking={openTracking}
              onBuyAgain={openBuyAgain}
            />
          ))
        )}
      </Animated.View>
    </ScrollView>
  );

  return (
    <View style={styles.root}>
      <Animated.View style={entrance.styleFor(SLOT.header)}>
        <OrdersHeader
          title={route?.params?.title || "Orders"}
          perspective={perspective}
          onChangePerspective={changePerspective}
          onBack={() => navigation?.goBack?.()}
          urgencyCount={perspective === "seller" ? sellerUrgency : buyerUrgency}
          urgencyLabel={
            perspective === "seller"
              ? "{n} to fulfill — ship soon to stay on time"
              : "{n} on the way"
          }
        />
      </Animated.View>

      {sellerBody}
      {buyerBody}
    </View>
  );
}

/**
 * A Preview ship-by for a seller order still awaiting shipment. The live surface
 * carries no fulfillment SLA (declared in ORDERS_MOCK_DATA_GAPS), so this is an
 * openly-provisional placeholder — the DeadlineLine tags it "Preview" — not a
 * real commitment. Orders already shipped/delivered or in a terminal overlay get
 * no line at all rather than a fabricated one.
 */
function previewShipBy(order: UnifiedOrder): string | undefined {
  if (order.variant !== "shipping") return undefined;
  if (order.overlay !== "none") return undefined;
  if (order.status === "shipped" || order.status === "delivered") return undefined;
  const base = order.createdAt ? Date.parse(order.createdAt) : Date.now();
  if (Number.isNaN(base)) return undefined;
  return new Date(base + 3 * DAY_MS).toISOString();
}

function bottomPad(inset: number) {
  return Math.max(inset, 16) + BOTTOM_NAV_CONTENT_CLEARANCE;
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: ordersLight.bg.page },
  hidden: { display: "none" },
  content: { paddingTop: 12, gap: 14 },
  stack: { gap: 12, paddingHorizontal: ordersLight.space.card },
  message: {
    paddingHorizontal: ordersLight.space.card,
    fontSize: 12,
    color: ordersLight.text.primary,
    lineHeight: 17
  }
});

export default OrdersManagerScreen;
