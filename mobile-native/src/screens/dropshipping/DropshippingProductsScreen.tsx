/**
 * Products imported from a supplier — and only those.
 *
 * ## Why this is not "your products"
 *
 * The server builds this list by joining from the supplier mapping, so a
 * product the merchant wrote by hand cannot appear here no matter how similar
 * it looks. That separation is the whole point of the screen: the merchant
 * needs one place where "this came from a supplier and is still tied to one"
 * is true of everything on it, because those are the products whose cost,
 * stock and existence are somebody else's to change.
 *
 * ## Draft is the loud state
 *
 * Import creates drafts and nothing else. A merchant who has imported forty
 * products and seen no sales has usually not published any of them, so the
 * count of drafts is stated at the top rather than left to be inferred from a
 * column of small grey badges.
 *
 * ## Cost is on this screen and nowhere public
 *
 * Supplier cost renders here because this screen is behind the merchant's own
 * store scope. It is not on the listing, not in search, not in any buyer-facing
 * payload. The same number in a public response is a leak, which is why it
 * arrives on this route and no other.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { FlatList, Image, Pressable, RefreshControl, StyleSheet, Text, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import {
  listImportedProducts,
  stateForError,
  type DropshippingState,
  type ImportedProductRow
} from "../../api/dropshipping";
import { StoreHeader } from "../../components/store";
import {
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
  route: { params: RootStackParamList["DropshippingProducts"] };
  navigation: { navigate: (...args: any[]) => void; goBack?: () => void };
};

type Filter = "ALL" | "DRAFT" | "PUBLISHED";

const FILTERS: { key: Filter; label: string; status?: string }[] = [
  { key: "ALL", label: "All" },
  { key: "DRAFT", label: "Drafts", status: "draft" },
  { key: "PUBLISHED", label: "Published", status: "active" }
];

/**
 * What the listing status means to a merchant.
 *
 * `draft` is spelled out as "not in your store yet" because "Draft" alone reads
 * as a saved thing rather than an invisible one, and the gap between those two
 * readings is a merchant waiting for orders that cannot arrive.
 */
const STATUS_COPY: Record<string, string> = {
  draft: "Not in your store yet",
  active: "In your store",
  paused: "Paused",
  archived: "Archived",
  rejected: "Rejected"
};

/**
 * Sync states the mapping can be in. `null` is not an error — a product that
 * has never synced since import has nothing to report and says nothing.
 */
const SYNC_COPY: Record<string, string> = {
  OK: "",
  SYNCED: "",
  PENDING: "Waiting on your supplier",
  STALE: "Supplier data is out of date",
  ERROR: "Last sync from your supplier failed",
  UNAVAILABLE: "Your supplier no longer lists this product",
  DISCONNECTED: "Supplier disconnected"
};

export function DropshippingProductsScreen({ route, navigation }: Props) {
  const { connectionId } = route.params;
  const formatters = useFormatters();
  const reducedMotion = useLogiNexusReducedMotion();
  const insets = useSafeAreaInsets();
  const scopeStatus = useDropshippingScope();

  const [rows, setRows] = useState<ImportedProductRow[]>([]);
  const [filter, setFilter] = useState<Filter>("ALL");
  const [state, setState] = useState<DropshippingState>("LOADING");
  const [refreshing, setRefreshing] = useState(false);

  const scope = scopeStatus.status.phase === "ready" ? scopeStatus.status.scope : null;

  const load = useCallback(
    async (mode: "initial" | "refresh" = "initial") => {
      if (!scope) return;
      if (mode === "refresh") setRefreshing(true);
      else setState("LOADING");
      try {
        const status = FILTERS.find((entry) => entry.key === filter)?.status;
        const result = await listImportedProducts(scope, connectionId, { status, limit: 100 });
        setRows(result.items);
        setState(result.items.length === 0 ? "EMPTY" : "READY");
      } catch (error) {
        // Cleared, because leaving the previous filter's rows on screen under a
        // failed request shows the merchant a list that is not the list they
        // asked for.
        setRows([]);
        setState(stateForError(error));
      } finally {
        setRefreshing(false);
      }
    },
    [connectionId, filter, scope]
  );

  useEffect(() => {
    if (scopeStatus.status.phase === "ready") load().catch(() => undefined);
    else if (scopeStatus.status.phase === "failed") setState(scopeStatus.status.state);
    else if (scopeStatus.status.phase === "missing") setState("EMPTY");
    else setState("LOADING");
  }, [load, scopeStatus.status]);

  const draftCount = useMemo(
    () => rows.filter((row) => row.status.toLowerCase() === "draft").length,
    [rows]
  );

  const stateBlock = stateOwnsScreen(state) ? (
    <DropshippingStateView
      state={state}
      subject="Your imported products"
      onRetry={state === "UNAUTHORIZED" ? null : () => load("refresh")}
      onFixConnection={() => navigation.navigate("DropshippingSuppliers", { title: "Suppliers" })}
      reducedMotion={reducedMotion}
      skeletonRows={4}
      empty={{
        title:
          filter === "ALL"
            ? "You haven't imported anything yet."
            : filter === "DRAFT"
              ? "No drafts — everything you've imported is published."
              : "Nothing published yet.",
        body:
          filter === "ALL"
            ? "Browse your supplier's catalogue and add products to your import cart."
            : "Change the filter above to see the rest of your imported products."
      }}
    />
  ) : null;

  return (
    <View style={styles.root}>
      <StoreHeader
        title={route.params.title || "Dropshipping products"}
        query=""
        onQueryChange={() => undefined}
        onSubmitSearch={() => undefined}
        onBack={() => navigation.goBack?.()}
        onNotifications={() => navigation.navigate("BusinessOsActivity")}
        unreadCount={0}
        searchPlaceholder="Dropshipping products"
        reducedMotion={reducedMotion}
      />

      <FlatList
        data={stateBlock ? [] : rows}
        keyExtractor={(item) => String(item.listingId)}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => load("refresh")} />}
        contentContainerStyle={[
          styles.content,
          { paddingBottom: Math.max(insets.bottom, 16) + BOTTOM_NAV_CONTENT_CLEARANCE }
        ]}
        ListHeaderComponent={
          <View style={styles.header}>
            <View style={styles.filters}>
              {FILTERS.map((entry) => {
                const active = entry.key === filter;
                return (
                  <Pressable
                    key={entry.key}
                    style={[styles.chip, active ? styles.chipActive : null]}
                    onPress={() => setFilter(entry.key)}
                    accessibilityRole="button"
                    accessibilityState={{ selected: active }}
                    accessibilityLabel={entry.label}
                  >
                    <Text style={[styles.chipText, active ? styles.chipTextActive : null]}>
                      {entry.label}
                    </Text>
                  </Pressable>
                );
              })}
            </View>

            {/* Only when there are drafts *and* the merchant is not already
                looking at the drafts filter — repeating it there would be
                telling them what they can see. */}
            {state === "READY" && draftCount > 0 && filter !== "DRAFT" ? (
              <Text style={styles.draftNote}>
                {formatters.count(draftCount)} of these are drafts and aren't in your store yet.
                Open one to set its price and publish it.
              </Text>
            ) : null}

            {stateBlock ? <View style={styles.block}>{stateBlock}</View> : null}
          </View>
        }
        renderItem={({ item }) => (
          <ProductRow
            row={item}
            costLabel={costText(item.supplierCostCents, item.currency, formatters)}
            updatedLabel={item.updatedAt ? formatters.relative(item.updatedAt) : null}
            onOpen={() =>
              navigation.navigate("DropshippingDraft", {
                connectionId,
                listingId: item.listingId,
                title: item.title || "Imported product"
              })
            }
          />
        )}
      />
    </View>
  );
}

function ProductRow({
  row,
  costLabel,
  updatedLabel,
  onOpen
}: {
  row: ImportedProductRow;
  costLabel: string | null;
  updatedLabel: string | null;
  onOpen: () => void;
}) {
  const status = row.status.toLowerCase();
  const isDraft = status === "draft";
  const syncNote = row.syncState ? SYNC_COPY[row.syncState.toUpperCase()] : "";

  return (
    <Pressable
      style={styles.row}
      onPress={onOpen}
      accessibilityRole="button"
      accessibilityLabel={`${row.title || "Untitled product"}, ${STATUS_COPY[status] || row.status}`}
    >
      {/* No placeholder art. An empty tile is honest about a product whose
          supplier images were all rejected; a stock photo is not. */}
      {row.coverImageUrl ? (
        <Image source={{ uri: row.coverImageUrl }} style={styles.thumb} resizeMode="cover" />
      ) : (
        <View style={[styles.thumb, styles.thumbEmpty]} />
      )}

      <View style={styles.rowBody}>
        <Text style={styles.rowTitle} numberOfLines={2}>
          {row.title || "Untitled product"}
        </Text>

        <View style={styles.rowMetaLine}>
          <ProviderBadge provider={row.provider} />
          <View style={[styles.statusPill, isDraft ? styles.statusPillDraft : styles.statusPillLive]}>
            <Text style={[styles.statusText, isDraft ? styles.statusTextDraft : styles.statusTextLive]}>
              {STATUS_COPY[status] || row.status}
            </Text>
          </View>
        </View>

        {/* "Cost unknown" rather than a zero. A merchant reading $0.00 here
            concludes the product is free to source. */}
        <Text style={styles.rowCost}>
          {costLabel ? `Your cost ${costLabel}` : `Cost ${NO_VALUE} not available`}
        </Text>

        {syncNote ? <Text style={styles.rowWarning}>{syncNote}</Text> : null}
        {updatedLabel ? <Text style={styles.rowUpdated}>Updated {updatedLabel}</Text> : null}
      </View>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: storeLight.bg.page },
  content: { paddingBottom: storeLight.space.section, gap: storeLight.space.gutter },
  header: { paddingHorizontal: storeLight.space.card, paddingTop: storeLight.space.section, gap: 10 },
  block: { paddingTop: 4 },
  filters: { flexDirection: "row", gap: 8 },
  chip: {
    minHeight: 36,
    justifyContent: "center",
    paddingHorizontal: 14,
    borderRadius: storeLight.radius.pill,
    borderWidth: 1,
    borderColor: storeLight.border.secondaryButton
  },
  chipActive: { backgroundColor: storeLight.cta.from, borderColor: storeLight.cta.from },
  chipText: { fontSize: 12, fontWeight: "600", color: storeLight.text.primary },
  chipTextActive: { color: storeLight.cta.text },
  draftNote: { fontSize: 12, color: storeLight.text.muted, lineHeight: 17 },
  row: {
    flexDirection: "row",
    gap: 12,
    marginHorizontal: storeLight.space.card,
    padding: storeLight.space.card,
    backgroundColor: storeLight.bg.card,
    borderRadius: storeLight.radius.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: storeLight.border.hairline
  },
  thumb: { width: 64, height: 64, borderRadius: storeLight.radius.control, backgroundColor: storeLight.bg.page },
  thumbEmpty: { borderWidth: StyleSheet.hairlineWidth, borderColor: storeLight.border.hairline },
  rowBody: { flex: 1, gap: 4 },
  rowTitle: { fontSize: 14, fontWeight: "700", color: storeLight.text.primary, lineHeight: 19 },
  rowMetaLine: { flexDirection: "row", alignItems: "center", gap: 6 },
  statusPill: { paddingHorizontal: 8, paddingVertical: 2, borderRadius: storeLight.radius.pill },
  statusPillDraft: { backgroundColor: storeLight.bg.page, borderWidth: StyleSheet.hairlineWidth, borderColor: storeLight.border.hairline },
  statusPillLive: { backgroundColor: storeLight.bg.page },
  statusText: { fontSize: 10, fontWeight: "700" },
  statusTextDraft: { color: storeLight.text.muted },
  statusTextLive: { color: storeLight.status.success },
  rowCost: { fontSize: 12, color: storeLight.text.muted },
  rowWarning: { fontSize: 11, fontWeight: "600", color: storeLight.status.warning },
  rowUpdated: { fontSize: 11, color: storeLight.text.muted }
});
