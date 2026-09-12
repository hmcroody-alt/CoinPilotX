/**
 * Dropshipping — the hub a merchant lands on from the Store dashboard.
 *
 * ## Progressive disclosure is the whole design
 *
 * A merchant with no supplier sees one thing: what dropshipping is, and a button
 * to connect a supplier. They do not see "Import cart (0)", "Products (0)" and
 * "Orders (0)", because six zeroes is a screen that looks broken and teaches
 * nothing. The tiles appear once there is a connection for them to be about.
 *
 * That is not cosmetic sequencing — every tile below the fold needs a
 * `connectionId` to open, and a tile that cannot say which supplier it means is
 * a tile that cannot be pressed.
 *
 * ## Why the hub picks a connection
 *
 * The child screens each act on exactly one supplier connection. The hub passes
 * the first usable one; when a merchant has several, the Suppliers screen is
 * where they choose, and every tile here follows that choice. One hub-level
 * choice is what keeps the cart the merchant reviews and the catalogue they
 * searched from being two different suppliers' — the bug this shape prevents.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { RefreshControl, ScrollView, StyleSheet, Text, View, Animated } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import {
  connectionIsUsable,
  connectionNeedsAttention,
  getImportCart,
  listImportedProducts,
  listSupplierConnections,
  stateForError,
  type DropshippingScope,
  type DropshippingState,
  type SupplierConnection
} from "../../api/dropshipping";
import { StoreHeader, StoreQuickLinkGrid, StoreStatusStrip } from "../../components/store";
import { DropshippingStateView, EnvironmentBadge, ProviderBadge, stateIsUnactionable, stateOwnsScreen } from "../../components/dropshipping/DropshippingStates";
import { useDropshippingScope } from "./useDropshippingScope";
import { useFormatters } from "../../i18n/hooks";
import { BOTTOM_NAV_CONTENT_CLEARANCE } from "../../navigation/BottomNavVisibility";
import { RootStackParamList } from "../../navigation/types";
import { storeLight } from "../../theme/storeLight";
import { useLogiNexusReducedMotion } from "../../theme/logiNexusMotion";
import { useStoreEntrance, STORE_STAGGER_MS } from "../../theme/storeMotion";

const SLOT = { header: 0, strip: 1, body: 2, tiles: 3 } as const;
const SECTION_COUNT = Object.keys(SLOT).length;

type Props = {
  route?: { params?: RootStackParamList["Dropshipping"] };
  navigation: { navigate: (...args: any[]) => void; goBack?: () => void };
};

/**
 * Counts the hub shows on its tiles.
 *
 * Both are `null` until they load and stay `null` if they fail. A tile that says
 * "0 items" about a cart whose request errored is worse than one that says
 * nothing — the merchant believes their cart emptied.
 */
type HubCounts = { cart: number | null; products: number | null };

export function DropshippingHubScreen({ route, navigation }: Props) {
  const formatters = useFormatters();
  const reducedMotion = useLogiNexusReducedMotion();
  const insets = useSafeAreaInsets();
  const entrance = useStoreEntrance(SECTION_COUNT, reducedMotion);
  const scopeStatus = useDropshippingScope();

  const [connections, setConnections] = useState<SupplierConnection[] | null>(null);
  const [counts, setCounts] = useState<HubCounts>({ cart: null, products: null });
  const [state, setState] = useState<DropshippingState>("LOADING");
  const [refreshing, setRefreshing] = useState(false);

  const scope: DropshippingScope | null =
    scopeStatus.status.phase === "ready" ? scopeStatus.status.scope : null;

  const active = useMemo(() => {
    const rows = connections || [];
    return rows.find(connectionIsUsable) || rows[0] || null;
  }, [connections]);

  const load = useCallback(
    async (mode: "initial" | "refresh" = "initial") => {
      if (!scope) return;
      if (mode === "refresh") setRefreshing(true);
      else setState("LOADING");
      try {
        const rows = await listSupplierConnections(scope);
        setConnections(rows);
        const usable = rows.find(connectionIsUsable) || null;
        if (!usable) {
          // No usable connection is EMPTY, not an error: the merchant has not
          // started yet. The counts stay null because there is nothing to count.
          setCounts({ cart: null, products: null });
          setState(rows.length === 0 ? "EMPTY" : "SUPPLIER_DISCONNECTED");
          return;
        }
        // Settled, not all: a failed cart count must not blank the products
        // count, and neither should take the screen down. Each stays null on
        // its own failure.
        const [cart, products] = await Promise.allSettled([
          getImportCart(scope, usable.id),
          listImportedProducts(scope, usable.id, { limit: 1 })
        ]);
        setCounts({
          cart: cart.status === "fulfilled" ? cart.value.count : null,
          products: products.status === "fulfilled" ? products.value.count : null
        });
        setState("READY");
      } catch (error) {
        setState(stateForError(error));
      } finally {
        setRefreshing(false);
      }
    },
    [scope]
  );

  useEffect(() => {
    if (scopeStatus.status.phase === "ready") {
      load().catch(() => undefined);
    } else if (scopeStatus.status.phase === "failed") {
      setState(scopeStatus.status.state);
    } else if (scopeStatus.status.phase === "missing") {
      setState("EMPTY");
    } else {
      setState("LOADING");
    }
  }, [load, scopeStatus.status]);

  const refresh = useCallback(() => {
    if (scopeStatus.status.phase === "ready") load("refresh").catch(() => undefined);
    else scopeStatus.reload();
  }, [load, scopeStatus]);

  /* -------------------------------------------------------------- *
   * Navigation
   * -------------------------------------------------------------- */

  const openSuppliers = useCallback(
    () => navigation.navigate("DropshippingSuppliers", { title: "Suppliers" }),
    [navigation]
  );

  const openConnect = useCallback(
    () => navigation.navigate("DropshippingConnect", { title: "Connect a supplier" }),
    [navigation]
  );

  const openStorefrontSetup = useCallback(
    () => navigation.navigate("BusinessOs", { title: "Business OS" }),
    [navigation]
  );

  const openStoreSetup = useCallback(
    () => navigation.navigate("MerchantApply", { title: "Set up your store" }),
    [navigation]
  );

  const withConnection = useCallback(
    (routeName: string, title: string) => () => {
      if (!active) {
        openSuppliers();
        return;
      }
      navigation.navigate(routeName, { connectionId: active.id, title });
    },
    [active, navigation, openSuppliers]
  );

  /* -------------------------------------------------------------- *
   * Body
   * -------------------------------------------------------------- */

  const missingScope = scopeStatus.status.phase === "missing" ? scopeStatus.status.gap : null;

  const stateBlock = stateOwnsScreen(state) ? (
    <DropshippingStateView
      state={state}
      subject="Dropshipping"
      onRetry={state === "UNAUTHORIZED" ? null : refresh}
      onFixConnection={connections && connections.length > 0 ? openSuppliers : null}
      reducedMotion={reducedMotion}
      skeletonRows={3}
      empty={
        missingScope === "NO_STORE"
          ? {
              title: "You need a store before you can import products.",
              body: "Set your store up on PulseSoc, then come back here to connect a supplier."
            }
          : missingScope === "STORE_PENDING_REVIEW"
            ? {
                title: "Your store is still being reviewed.",
                body: "You can connect a supplier as soon as your store is approved to sell."
              }
            : missingScope === "NO_STOREFRONT"
              ? {
                  title: "Your business doesn't have a store yet.",
                  body: "Create your storefront, then connect a supplier to import products into it."
                }
              : {
                  title: "Sell products you don't have to stock.",
                  body:
                    "Connect a supplier, browse their catalogue, and import products as drafts. " +
                    "You set your own price and nothing goes live until you publish it."
                }
      }
    />
  ) : null;

  /**
   * The tiles. Rendered only with a usable connection — see the note at the top
   * about why six zeroes is not a useful screen.
   */
  const tiles = active ? (
    <StoreQuickLinkGrid
      reducedMotion={reducedMotion}
      items={[
        {
          icon: "search-outline",
          label: "Find products",
          subtitle: "Browse your supplier's catalogue",
          onPress: withConnection("DropshippingCatalog", "Find products"),
          reducedMotion
        },
        {
          icon: "cart-outline",
          label: "Import cart",
          subtitle:
            counts.cart === null
              ? "Ready when you are"
              : counts.cart === 0
                ? "Nothing selected yet"
                : `${formatters.count(counts.cart)} ready to import`,
          onPress: withConnection("DropshippingCart", "Import cart"),
          reducedMotion
        },
        {
          icon: "cube-outline",
          label: "Products",
          subtitle:
            counts.products === null
              ? "Everything you've imported"
              : counts.products === 0
                ? "Nothing imported yet"
                : `${formatters.count(counts.products)} imported`,
          onPress: withConnection("DropshippingProducts", "Dropshipping products"),
          reducedMotion
        },
        {
          icon: "people-outline",
          label: "Suppliers",
          subtitle: `${formatters.count((connections || []).length)} connected`,
          onPress: openSuppliers,
          reducedMotion
        },
        {
          icon: "receipt-outline",
          label: "Supplier orders",
          // Not "Orders sent to your supplier". Nothing is sent — fulfilment is
          // off platform-wide — and that subtitle told a merchant their sales
          // were already on their way. What the screen actually lists is the
          // sales that still owe a supplier purchase.
          subtitle: "Sales waiting on a supplier purchase",
          // Connection-scoped now, like every other entry here: the obligations
          // list is per supplier connection, so this must go through
          // `withConnection` or it lands on a screen that can only say EMPTY.
          onPress: withConnection("DropshippingOrders", "Supplier orders"),
          reducedMotion
        },
        {
          icon: "sync-outline",
          label: "Sync & issues",
          subtitle: "What needs your attention",
          onPress: withConnection("DropshippingSync", "Sync & issues"),
          reducedMotion
        }
      ]}
    />
  ) : null;

  /**
   * The strip names the blocker the merchant actually has. Saying "No supplier
   * connected" while the body says a store is missing sends them to a key form
   * that cannot succeed — the supplier is not the thing standing in the way.
   */
  const stripText = (() => {
    if (scopeStatus.status.phase === "ready" && active) {
      return `${scopeStatus.status.storeName || "Your store"} · ${
        connectionIsUsable(active) ? "Supplier connected" : "Supplier needs attention"
      }`;
    }
    if (missingScope === "NO_STORE") return "Dropshipping · No store yet";
    if (missingScope === "STORE_PENDING_REVIEW") return "Dropshipping · Store in review";
    if (missingScope === "NO_STOREFRONT") return "Dropshipping · No storefront yet";
    // "No supplier connected" is a claim about the merchant's account, and it can
    // only be made once the scope actually came back. Saying it while the check is
    // in flight or after it failed puts an absence next to a body that is still
    // loading or reporting an error.
    if (scopeStatus.status.phase === "loading") return "Dropshipping · Checking your store";
    if (scopeStatus.status.phase === "failed") {
      // The server answering "this deployment has suppliers switched off" is an
      // answer, not a failure to get one. Calling it "couldn't check your store"
      // sends the merchant looking for a fault in a store that is fine.
      if (scopeStatus.status.state === "SUPPLIER_DISABLED") return "Dropshipping · Not enabled here yet";
      if (scopeStatus.status.state === "PROVIDER_NETWORK_DISABLED") return "Dropshipping · Supplier network off";
      if (scopeStatus.status.state === "STORE_NOT_APPROVED") return "Dropshipping · Store not approved to sell";
      if (scopeStatus.status.state === "STORE_ACCESS_REVOKED") return "Dropshipping · Store can no longer sell";
      if (scopeStatus.status.state === "STORE_NOT_FOUND") return "Dropshipping · Store not matched";
      if (scopeStatus.status.state === "STORE_MAPPING_MISSING") return "Dropshipping · Store not linked yet";
      if (scopeStatus.status.state === "SUPPLIER_CONNECTION_FORBIDDEN") return "Dropshipping · No supplier access";
      if (scopeStatus.status.state === "SESSION_EXPIRED") return "Dropshipping · Sign in again";
      return "Dropshipping · Couldn't check your store";
    }
    return "Dropshipping · No supplier connected";
  })();

  /**
   * A merchant waiting on review has nothing to set up, so the action re-checks
   * instead of sending them to a form they already submitted.
   */
  const unactionable =
    scopeStatus.status.phase === "failed" && stateIsUnactionable(scopeStatus.status.state);

  const stripAction: { label: string; onPress: () => void } | null = active
    ? { label: "Suppliers", onPress: openSuppliers }
    : missingScope === "NO_STORE"
      ? { label: "Set up", onPress: openStoreSetup }
      : missingScope === "STORE_PENDING_REVIEW"
        ? { label: "Refresh", onPress: refresh }
        : missingScope === "NO_STOREFRONT"
          ? { label: "Set up", onPress: openStorefrontSetup }
          : unactionable
            ? null
            : scopeStatus.status.phase === "loading" || scopeStatus.status.phase === "failed"
              ? { label: "Try again", onPress: refresh }
              : { label: "Connect", onPress: openConnect };

  return (
    <View style={styles.root}>
      <Animated.View style={entrance.styleFor(SLOT.header)}>
        <StoreHeader
          title={route?.params?.title || "Dropshipping"}
          query=""
          onQueryChange={() => undefined}
          onSubmitSearch={withConnection("DropshippingCatalog", "Find products")}
          onBack={() => navigation.goBack?.()}
          onNotifications={() => navigation.navigate("BusinessOsActivity")}
          unreadCount={0}
          searchPlaceholder="Search supplier products"
          reducedMotion={reducedMotion}
        />
      </Animated.View>

      <Animated.View style={entrance.styleFor(SLOT.strip)}>
        <StoreStatusStrip
          text={stripText}
          open={Boolean(active && connectionIsUsable(active))}
          actionLabel={stripAction?.label}
          onAction={stripAction?.onPress}
          reducedMotion={reducedMotion}
        />
      </Animated.View>

      <ScrollView
        contentContainerStyle={[
          styles.content,
          { paddingBottom: Math.max(insets.bottom, 16) + BOTTOM_NAV_CONTENT_CLEARANCE }
        ]}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={refresh} />}
      >
        <Animated.View style={[styles.block, entrance.styleFor(SLOT.body)]}>
          {stateBlock}
          {/* The connect CTA belongs with the empty state and nowhere else: once
              a supplier exists, "Connect a supplier" is a Suppliers-screen
              action, not the hub's headline. */}
          {state === "EMPTY" && !missingScope ? (
            <ConnectCta onPress={openConnect} />
          ) : null}
        </Animated.View>

        {active ? (
          <Animated.View style={[styles.block, entrance.styleFor(SLOT.tiles)]}>
            <View style={styles.supplierRow}>
              <ProviderBadge provider={active.provider} />
              <EnvironmentBadge environment={active.environment} />
              {connectionNeedsAttention(active) ? (
                <Text style={styles.attention}>Needs attention</Text>
              ) : null}
            </View>
            <Text style={styles.sectionTitle}>Your dropshipping workflow</Text>
            {tiles}
          </Animated.View>
        ) : null}

        {/* Stated on the hub rather than only in the report, because a merchant
            who believes a sandbox order shipped is a merchant with an angry
            customer. */}
        {active && String(active.environment).toUpperCase() !== "PRODUCTION" ? (
          <View style={styles.sandboxNote}>
            <Text style={styles.sandboxNoteText}>
              This supplier is connected in sandbox. Products import normally, but no order is
              really placed and nothing ships.
            </Text>
          </View>
        ) : null}
      </ScrollView>
    </View>
  );
}

function ConnectCta({ onPress }: { onPress: () => void }) {
  return (
    <View style={styles.ctaWrap}>
      <Text
        style={styles.cta}
        accessibilityRole="button"
        accessibilityLabel="Connect a supplier"
        onPress={onPress}
      >
        Connect a supplier
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: storeLight.bg.page },
  content: { paddingTop: storeLight.space.section, gap: storeLight.space.section },
  block: { paddingHorizontal: storeLight.space.card, gap: storeLight.space.gutter },
  sectionTitle: { fontSize: 16, fontWeight: "700", color: storeLight.text.primary },
  supplierRow: { flexDirection: "row", alignItems: "center", gap: 8 },
  attention: { fontSize: 11, fontWeight: "700", color: storeLight.status.warning },
  ctaWrap: { alignItems: "flex-start" },
  cta: {
    minHeight: storeLight.size.tapTarget,
    lineHeight: storeLight.size.tapTarget,
    paddingHorizontal: 20,
    borderRadius: storeLight.radius.pill,
    backgroundColor: storeLight.cta.from,
    color: storeLight.cta.text,
    fontSize: 14,
    fontWeight: "800",
    overflow: "hidden"
  },
  sandboxNote: {
    marginHorizontal: storeLight.space.card,
    padding: storeLight.space.card,
    borderRadius: storeLight.radius.card,
    backgroundColor: storeLight.bg.warning,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: storeLight.border.warning
  },
  sandboxNoteText: { fontSize: 12, color: storeLight.text.primary, lineHeight: 17 }
});
