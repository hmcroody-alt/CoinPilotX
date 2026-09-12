/**
 * Supplier orders — the half of an order that is not the customer's.
 *
 * ## Two orders, deliberately
 *
 * A customer order is a promise the merchant made to a buyer. A supplier order
 * is a separate purchase the merchant makes to keep that promise. They have
 * different money, different states and different failure modes: a supplier
 * order can be refused while the customer order stands, and collapsing them
 * into one row would leave the merchant with no way to see that.
 *
 * ## This screen used to show nothing, and say why
 *
 * That was the honest rendering of a real absence — but the absence was bigger
 * than the note admitted. The note said the fulfilment layer "can create and
 * read a single supplier order by id" and only lacked an enumeration. In fact
 * nothing on any surface a merchant could touch ever created one: the create
 * route had zero callers, the dispatch worker was not in the Procfile, and the
 * payment path had no reference to the supplier mapping at all. So a buyer
 * could pay for a published, bound dropship listing and the obligation to buy
 * it from the supplier existed only in the merchant's head.
 *
 * The list this screen now renders is that obligation, derived server-side by
 * joining paid orders to the supplier mapping of the listing they were placed
 * on. A row here is a sale that owes a supplier purchase, and its state says
 * whether one has been placed.
 *
 * ## Every row still says "not placed", and that is the truth
 *
 * Sending orders to suppliers is off platform-wide, and the server says so on
 * this payload rather than the screen assuming it from a build flag. So the
 * list will read `No supplier order yet` for every row until fulfilment is
 * switched on. That is the point: the merchant can now see the queue of things
 * that are waiting, which is the difference between a known backlog and a
 * silent one.
 *
 * Nothing here is invented. A row exists only because a buyer paid.
 */

import { useCallback, useEffect, useState } from "react";
import { FlatList, RefreshControl, StyleSheet, Text, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import {
  DROPSHIPPING_DATA_GAPS,
  listSupplierObligations,
  stateForError,
  supplierObligationBlockerCopy,
  supplierOrderStateCopy,
  type DropshippingState,
  type SupplierObligation
} from "../../api/dropshipping";
import { StoreHeader } from "../../components/store";
import {
  DropshippingGapNote,
  DropshippingStateView,
  NO_VALUE,
  ProviderBadge,
  costText,
  stateOwnsScreen
} from "../../components/dropshipping/DropshippingStates";
import { useDropshippingScope } from "./useDropshippingScope";
import { useFormatters } from "../../i18n/hooks";
import { BOTTOM_NAV_CONTENT_CLEARANCE } from "../../navigation/BottomNavVisibility";
import { RootStackParamList } from "../../navigation/types";
import { storeLight } from "../../theme/storeLight";
import { useLogiNexusReducedMotion } from "../../theme/logiNexusMotion";

type Props = {
  route?: { params?: RootStackParamList["DropshippingOrders"] };
  navigation: { navigate: (...args: any[]) => void; goBack?: () => void };
};

export function DropshippingOrdersScreen({ route, navigation }: Props) {
  const connectionId = route?.params?.connectionId;
  const formatters = useFormatters();
  const reducedMotion = useLogiNexusReducedMotion();
  const insets = useSafeAreaInsets();
  const scopeStatus = useDropshippingScope();

  const [rows, setRows] = useState<SupplierObligation[]>([]);
  const [state, setState] = useState<DropshippingState>("LOADING");
  const [refreshing, setRefreshing] = useState(false);
  // What the *server* says about sandbox mode, not what this build assumes.
  // `null` until a response arrives, so the screen never promises "nothing is
  // sent to your supplier" on its own authority.
  const [isSandbox, setIsSandbox] = useState<boolean | null>(null);

  const scope = scopeStatus.status.phase === "ready" ? scopeStatus.status.scope : null;

  const load = useCallback(
    async (mode: "initial" | "refresh" = "initial") => {
      if (!scope || !connectionId) return;
      if (mode === "refresh") setRefreshing(true);
      else setState("LOADING");
      try {
        const result = await listSupplierObligations(scope, connectionId, { limit: 100 });
        setRows(result.obligations);
        setIsSandbox(result.isSandbox);
        setState(result.obligations.length === 0 ? "EMPTY" : "READY");
      } catch (error) {
        // Cleared on failure. A stale list of supplier obligations under a
        // failed request is the error-and-empty shape in its most expensive
        // form: the merchant reads a backlog that may already be handled, or
        // misses one that is not.
        setRows([]);
        setIsSandbox(null);
        setState(stateForError(error));
      } finally {
        setRefreshing(false);
      }
    },
    [connectionId, scope]
  );

  useEffect(() => {
    // No connection in the route params means this screen was reached before a
    // supplier was chosen. That is EMPTY with its own copy, not an error.
    //
    // Checked first so the answer does not depend on another module. Moved
    // after the phase checks, a ready scope with no connection calls `load`,
    // which returns before clearing LOADING — a skeleton for ever with no
    // error and no retry. That is unreachable *today*, and only by accident:
    // `useDropshippingScope` always starts in `loading` and reaches `ready` a
    // microtask later, so this branch has already run by then. The day that
    // hook answers synchronously from its cache — the optimisation its own
    // docstring argues for — the order stops being cosmetic. No test can catch
    // the reordering while the hook is asynchronous, which is why this is a
    // comment and not an assertion.
    if (!connectionId) setState("EMPTY");
    else if (scopeStatus.status.phase === "ready") load().catch(() => undefined);
    else if (scopeStatus.status.phase === "failed") setState(scopeStatus.status.state);
    else if (scopeStatus.status.phase === "missing") setState("EMPTY");
    else setState("LOADING");
  }, [connectionId, load, scopeStatus.status]);

  const awaiting = rows.filter((row) => !row.supplierOrderPlaced).length;
  // Counted off the server's own verdict rather than `blockers.length`, so this
  // number cannot disagree with the per-row reasons underneath it.
  const blocked = rows.filter((row) => !row.supplierOrderPlaced && !row.canPlaceSupplierOrder).length;

  const stateBlock = stateOwnsScreen(state) ? (
    <DropshippingStateView
      state={state}
      subject="Your supplier orders"
      onRetry={state === "UNAUTHORIZED" || !connectionId ? null : () => load("refresh")}
      onFixConnection={() => navigation.navigate("DropshippingSuppliers", { title: "Suppliers" })}
      reducedMotion={reducedMotion}
      skeletonRows={3}
      empty={
        connectionId
          ? {
              title: "No sales to fulfil yet.",
              body:
                "When a customer buys one of your dropshipped products, it appears here with the supplier purchase it needs."
            }
          : {
              title: "Connect a supplier first.",
              body: "Supplier orders appear here once you've connected a supplier and sold something."
            }
      }
    />
  ) : null;

  return (
    <View style={styles.root}>
      <StoreHeader
        title={route?.params?.title || "Supplier orders"}
        query=""
        onQueryChange={() => undefined}
        onSubmitSearch={() => undefined}
        onBack={() => navigation.goBack?.()}
        onNotifications={() => navigation.navigate("BusinessOsActivity")}
        unreadCount={0}
        searchPlaceholder="Supplier orders"
        reducedMotion={reducedMotion}
      />

      <FlatList
        data={stateBlock ? [] : rows}
        keyExtractor={(item) => String(item.orderId)}
        refreshControl={
          <RefreshControl
            refreshing={refreshing}
            onRefresh={() => load("refresh")}
            enabled={Boolean(connectionId)}
          />
        }
        contentContainerStyle={[
          styles.content,
          { paddingBottom: Math.max(insets.bottom, 16) + BOTTOM_NAV_CONTENT_CLEARANCE }
        ]}
        ListHeaderComponent={
          <View style={styles.header}>
            <View style={styles.card}>
              <Text style={styles.cardTitle}>Your orders and your supplier's are separate</Text>
              <Text style={styles.cardBody}>
                When a customer buys one of your dropshipped products, that's your order with them.
                A second order — yours with your supplier — is what actually gets the item shipped.
                PulseSoc keeps them apart so you can always see which one is stuck.
              </Text>
            </View>

            {/* Only when the server said so. Rendered from the response rather
                than from a constant, so this promise cannot outlive the switch
                it describes. */}
            {isSandbox === true ? (
              <View style={styles.card}>
                <Text style={styles.cardTitle}>Sandbox fulfilment</Text>
                <Text style={styles.cardBody}>
                  Sending real orders to suppliers is switched off platform-wide. The sales below
                  are real and your customers have paid — but no purchase has been placed with your
                  supplier and nothing ships yet.
                </Text>
              </View>
            ) : null}

            {state === "READY" && awaiting > 0 ? (
              <Text style={styles.awaitingNote}>
                {formatters.count(awaiting)} of these have no supplier order yet.
              </Text>
            ) : null}

            {/* Separate from the count above, because "waiting" and "cannot go"
                are different problems. A sale with no supplier order is normal
                while fulfilment is off; a sale that could not be ordered even
                once it is on needs the merchant to change something, and the
                rows say what. */}
            {state === "READY" && blocked > 0 ? (
              <Text style={styles.awaitingNote}>
                {formatters.count(blocked)} could not be ordered as things stand — each row says why.
              </Text>
            ) : null}

            {/* Still rendered from the exported list, so the screen cannot claim
                a gap has closed while the list says it is open — and cannot keep
                claiming one after it closes. Two entries were removed here when
                the list above started loading; the one that remains is real. */}
            {DROPSHIPPING_DATA_GAPS.map((gap) => (
              <DropshippingGapNote
                key={gap.surface}
                title={`${gap.surface} isn't available yet`}
                body={gap.needs}
              />
            ))}

            {stateBlock ? <View style={styles.block}>{stateBlock}</View> : null}
          </View>
        }
        renderItem={({ item }) => (
          <ObligationRow
            row={item}
            costLabel={costText(item.supplierCostCents, item.supplierCostCurrency, formatters)}
            paidLabel={item.paidAt ? formatters.relative(item.paidAt) : null}
          />
        )}
      />
    </View>
  );
}

function ObligationRow({
  row,
  costLabel,
  paidLabel
}: {
  row: SupplierObligation;
  costLabel: string | null;
  paidLabel: string | null;
}) {
  const stateLabel = supplierOrderStateCopy(row.state);
  // Suppressed once an order has been placed. The only blocker an already-placed
  // row carries is that it is already placed, which the pill above says better,
  // and re-stating it as a problem would read as one where there is none.
  const blockers = row.supplierOrderPlaced ? [] : row.blockers;

  return (
    <View
      style={styles.row}
      accessibilityLabel={`${row.title || "Untitled product"}, ${stateLabel}`}
    >
      <Text style={styles.rowTitle} numberOfLines={2}>
        {row.title || "Untitled product"}
      </Text>

      <View style={styles.rowMetaLine}>
        <ProviderBadge provider={row.provider} />
        <View style={[styles.statePill, row.supplierOrderPlaced ? styles.statePillPlaced : null]}>
          <Text
            style={[styles.stateText, row.supplierOrderPlaced ? styles.stateTextPlaced : null]}
          >
            {stateLabel}
          </Text>
        </View>
      </View>

      {/* The bound variant's supplier SKU first, then its id: this is the line
          the merchant would read out to their supplier, and the SKU is the field
          the supplier order is actually matched on. Falls back rather than
          showing a blank, because a variant can be bound before its SKU is
          known — which is what `SUPPLIER_SKU_MISSING` below says. */}
      <Text style={styles.rowMeta}>
        Order #{row.orderId} · {row.quantity} ×{" "}
        {row.supplierSku || row.providerVariantId || row.providerProductId || NO_VALUE}
      </Text>

      {/* "not available" rather than a zero: a merchant reading $0.00 here
          concludes the supplier purchase is free. */}
      <Text style={styles.rowCost}>
        {costLabel ? `Your supplier cost ${costLabel}` : `Supplier cost ${NO_VALUE} not available`}
      </Text>

      {/* Every reason this sale cannot be turned into a supplier purchase, one
          line each rather than only the first. They are independent conditions
          and a merchant who fixes the one we chose to show would come back to
          find another — which is the shape of problem this repo keeps making. */}
      {blockers.map((blocker) => (
        <Text key={blocker} style={styles.rowWarning}>
          {supplierObligationBlockerCopy(blocker)}
        </Text>
      ))}

      {/* The supplier's own refusal text, when there is one. Shown verbatim
          rather than summarised — a merchant chasing a blocked order needs the
          words their supplier used. */}
      {row.lastError ? <Text style={styles.rowWarning}>{row.lastError}</Text> : null}
      {row.providerOrderId ? (
        <Text style={styles.rowMeta}>Supplier order {row.providerOrderId}</Text>
      ) : null}
      {paidLabel ? <Text style={styles.rowMeta}>Paid {paidLabel}</Text> : null}
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: storeLight.bg.page },
  content: { paddingBottom: storeLight.space.section, gap: storeLight.space.gutter },
  header: { paddingHorizontal: storeLight.space.card, paddingTop: storeLight.space.section, gap: storeLight.space.gutter },
  block: { paddingTop: 4 },
  card: {
    padding: storeLight.space.card,
    gap: 8,
    backgroundColor: storeLight.bg.card,
    borderRadius: storeLight.radius.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: storeLight.border.hairline
  },
  cardTitle: { fontSize: 15, fontWeight: "700", color: storeLight.text.primary },
  cardBody: { fontSize: 13, color: storeLight.text.muted, lineHeight: 18 },
  awaitingNote: { fontSize: 12, color: storeLight.text.muted, lineHeight: 17 },
  row: {
    gap: 4,
    marginHorizontal: storeLight.space.card,
    padding: storeLight.space.card,
    backgroundColor: storeLight.bg.card,
    borderRadius: storeLight.radius.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: storeLight.border.hairline
  },
  rowTitle: { fontSize: 14, fontWeight: "700", color: storeLight.text.primary, lineHeight: 19 },
  rowMetaLine: { flexDirection: "row", alignItems: "center", gap: 6, paddingVertical: 2 },
  statePill: {
    paddingHorizontal: 8,
    paddingVertical: 2,
    borderRadius: storeLight.radius.pill,
    backgroundColor: storeLight.bg.page,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: storeLight.border.hairline
  },
  statePillPlaced: { borderColor: storeLight.status.success },
  stateText: { fontSize: 10, fontWeight: "700", color: storeLight.text.muted },
  stateTextPlaced: { color: storeLight.status.success },
  rowMeta: { fontSize: 11, color: storeLight.text.muted },
  rowCost: { fontSize: 12, color: storeLight.text.muted },
  rowWarning: { fontSize: 11, fontWeight: "600", color: storeLight.status.warning }
});
