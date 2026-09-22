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
  getImportCart,
  getSupplierStatus,
  stateForError,
  type DropshippingScope,
  type DropshippingState,
  type StoreSupplierStatus,
  type SupplierStatus
} from "../../api/dropshipping";
import { StoreHeader, StoreQuickLinkGrid, StoreStatusStrip } from "../../components/store";
import { DropshippingStateView, EnvironmentBadge, ProviderBadge, stateIsUnactionable, stateOwnsScreen } from "../../components/dropshipping/DropshippingStates";
import { AttentionBadge, OperatingModeBanner } from "../../components/dropshipping/SupplierHealth";
import { NEXT_ACTION_COPY, ordersCopy, syncCopy } from "./supplierStatusCopy";
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
 * A connection the hub can act on.
 *
 * `CONNECTED` is the server's word, read rather than re-derived. This screen
 * used to call `connectionIsUsable` on a connections-list row and the Suppliers
 * screen read a status string, which is how one screen showed a green dot over
 * the connection the other was asking the merchant to reconnect.
 */
function usable(supplier: SupplierStatus): boolean {
  return supplier.connectionState === "CONNECTED";
}

export function DropshippingHubScreen({ route, navigation }: Props) {
  const formatters = useFormatters();
  const reducedMotion = useLogiNexusReducedMotion();
  const insets = useSafeAreaInsets();
  const entrance = useStoreEntrance(SECTION_COUNT, reducedMotion);
  const scopeStatus = useDropshippingScope();

  const [status, setStatus] = useState<StoreSupplierStatus | null>(null);
  /**
   * The one count `/supplier-status` does not carry. Everything else on these
   * tiles now comes from that one payload; the cart is a separate resource with
   * its own screen, so it stays a separate request — and stays `null` when it
   * fails, because a tile saying "Nothing selected yet" about a cart whose
   * request errored tells the merchant their cart emptied.
   */
  const [cart, setCart] = useState<number | null>(null);
  const [state, setState] = useState<DropshippingState>("LOADING");
  const [refreshing, setRefreshing] = useState(false);

  const scope: DropshippingScope | null =
    scopeStatus.status.phase === "ready" ? scopeStatus.status.scope : null;

  const suppliers = useMemo(() => status?.suppliers || [], [status]);

  const active = useMemo(
    () => suppliers.find(usable) || suppliers[0] || null,
    [suppliers]
  );

  const load = useCallback(
    async (mode: "initial" | "refresh" = "initial") => {
      if (!scope) return;
      if (mode === "refresh") setRefreshing(true);
      else setState("LOADING");
      try {
        const next = await getSupplierStatus(scope);
        setStatus(next);
        const connected = next.suppliers.find(usable) || null;
        if (!connected) {
          // No usable connection is EMPTY, not an error: the merchant has not
          // started yet. The cart stays null because there is nothing to count.
          setCart(null);
          setState(next.suppliers.length === 0 ? "EMPTY" : "SUPPLIER_DISCONNECTED");
          return;
        }
        try {
          setCart((await getImportCart(scope, connected.connectionId)).count);
        } catch {
          // A cart this hub could not read must not take down the six tiles that
          // do not depend on it.
          setCart(null);
        }
        setState("READY");
      } catch (error) {
        // Cleared, not kept. A stale payload behind an error screen is how a
        // sandbox banner survives a failed read and describes a state nobody
        // checked — the same rule the Suppliers screen holds.
        setStatus(null);
        setCart(null);
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
      navigation.navigate(routeName, { connectionId: active.connectionId, title });
    },
    [active, navigation, openSuppliers]
  );

  /* -------------------------------------------------------------- *
   * Body
   * -------------------------------------------------------------- */

  const missingScope = scopeStatus.status.phase === "missing" ? scopeStatus.status.gap : null;

  const connectedCount = suppliers.filter(usable).length;
  const sync = syncCopy(active?.syncState ?? null);
  const orders = ordersCopy(active?.orders ?? null);

  const stateBlock = stateOwnsScreen(state) ? (
    <DropshippingStateView
      state={state}
      subject="Dropshipping"
      onRetry={state === "UNAUTHORIZED" ? null : refresh}
      onFixConnection={suppliers.length > 0 ? openSuppliers : null}
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
                    "Connect a supplier, browse their catalogue, and import what you want to sell. " +
                    "PulseSoc prices it by your rule and publishes it to your store — anything it " +
                    "can't publish safely stays a draft and tells you why."
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
          attention: active.nextAction === "IMPORT_FIRST_PRODUCT",
          reducedMotion
        },
        {
          icon: "cart-outline",
          label: "Import cart",
          subtitle:
            cart === null
              ? "Ready when you are"
              : cart === 0
                ? "Nothing selected yet"
                : `${formatters.count(cart)} ready to import`,
          onPress: withConnection("DropshippingCart", "Import cart"),
          reducedMotion
        },
        {
          icon: "cube-outline",
          label: "Products",
          // Imported and live, not imported alone. A merchant who imported
          // twelve products and published none has a store with nothing in it,
          // and "12 imported" is the sentence that hid that.
          subtitle:
            active.products.imported === 0
              ? "Nothing imported yet"
              : `${formatters.count(active.products.imported)} imported · ${formatters.count(
                  active.products.published
                )} live`,
          onPress: withConnection("DropshippingProducts", "Dropshipping products"),
          attention: active.nextAction === "REVIEW_DRAFTS",
          reducedMotion
        },
        {
          icon: "people-outline",
          label: "Suppliers",
          // Connected out of total, because the difference is the whole point:
          // "2 connected" over one working supplier and one revoked credential
          // is the count that sends a merchant to look somewhere else for the
          // reason nothing imports.
          subtitle:
            suppliers.length === connectedCount
              ? `${formatters.count(connectedCount)} connected`
              : `${formatters.count(connectedCount)} of ${formatters.count(
                  suppliers.length
                )} working`,
          onPress: openSuppliers,
          attention: suppliers.length !== connectedCount,
          reducedMotion
        },
        {
          icon: "receipt-outline",
          label: "Supplier orders",
          // Counted through `/supplier-status`, which counts them through the
          // same derivation the orders screen lists. Falls back to describing
          // the screen when the server could not read them — never to "0", which
          // would be a claim about sales a buyer has already paid for.
          subtitle: orders.label,
          // Connection-scoped, like every other entry here: the obligations
          // list is per supplier connection, so this must go through
          // `withConnection` or it lands on a screen that can only say EMPTY.
          onPress: withConnection("DropshippingOrders", "Supplier orders"),
          attention: orders.attention,
          reducedMotion
        },
        {
          icon: "sync-outline",
          label: "Sync & issues",
          subtitle:
            active.issues.products > 0
              ? `${formatters.count(active.issues.products)} need${
                  active.issues.products === 1 ? "s" : ""
                } a decision`
              : sync.label,
          onPress: withConnection("DropshippingSync", "Sync & issues"),
          attention: active.issues.products > 0 || sync.tone === "warn",
          reducedMotion
        },
        {
          icon: "options-outline",
          // Three nouns and an ampersand fitted the tile in the mock and not on
          // a phone: "Pricing, publishing, Marketplace" rendered as "Pricing,
          // publishing, Mark…". Two lines of subtitle are available, so the
          // string is written to fit them rather than to list every setting.
          label: "Import settings",
          subtitle: "Your pricing and publishing rules",
          // Not `withConnection`: the policy belongs to the storefront, so this is
          // the one tile here that is reachable and useful with no supplier chosen.
          onPress: () =>
            navigation.navigate("DropshippingImportPolicy", { title: "Import settings" }),
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
        usable(active) ? "Supplier connected" : "Supplier needs attention"
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
          open={Boolean(active && usable(active))}
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

        {active && status ? (
          <Animated.View style={[styles.block, entrance.styleFor(SLOT.tiles)]}>
            {/* Above the tiles, not below them. A merchant who believes a sandbox
                order shipped is a merchant with an angry customer, and the note
                that used to say so sat under seven tiles they had to scroll
                past. Always rendered — see `OperatingModeBanner`. */}
            <OperatingModeBanner status={status} testID="hub-operating-mode" />
            <View style={styles.supplierRow}>
              <ProviderBadge provider={active.provider} />
              <EnvironmentBadge environment={active.environment} />
              <AttentionBadge
                needsAttention={active.needsAttention}
                testID="hub-supplier-attention"
              />
            </View>
            {/* The one thing to do next, chosen by the server and said in full.
                Without it the merchant reads seven tiles and picks; with it the
                tile that is flagged also has a sentence explaining why. */}
            {active.nextAction ? (
              <Text style={styles.nextStep} testID="hub-next-step">
                {NEXT_ACTION_COPY[active.nextAction].body}
              </Text>
            ) : null}
            <Text style={styles.sectionTitle}>Your dropshipping workflow</Text>
            {tiles}
          </Animated.View>
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
  supplierRow: { flexDirection: "row", alignItems: "center", gap: 8, flexWrap: "wrap" },
  nextStep: { fontSize: 13, color: storeLight.text.primary, lineHeight: 19 },
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
  }
});
