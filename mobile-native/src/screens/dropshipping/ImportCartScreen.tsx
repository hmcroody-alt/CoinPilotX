/**
 * Import cart — review, price, import.
 *
 * ## The cart is not the product, and importing is not publishing
 *
 * A cart row is a merchant's *intent* to import. It has no listing id, it is not
 * in the store, and deleting it deletes nothing a buyer could see. Import turns
 * rows into DRAFT listings — and only drafts. This screen never publishes, has
 * no publish control, and says so on the button ("Import as drafts"), because a
 * merchant who thinks they just put forty untitled products on their storefront
 * behaves very differently from one who knows they have forty drafts.
 *
 * ## Import Selected sends ids
 *
 * `importSelected` takes `itemIds` and an optional pricing rule. It cannot take
 * a cost, and there is no field on this screen that would produce one. The
 * server re-fetches every economic fact from the provider before it writes. The
 * merchant's markup is arithmetic applied to a cost the *server* fetched, which
 * is why a rule is safe to send and a price is not.
 *
 * ## Partial success is reported per item
 *
 * Nine imports and one refusal is neither "imported" nor "failed", and
 * collapsing it to either loses the one row the merchant has to do something
 * about. The result sheet lists every outcome by name.
 *
 * ## Re-importing is safe
 *
 * The server is idempotent per product: a second run returns `ALREADY_EXISTS`
 * rather than creating a second listing. That is why the button stays live after
 * a partial run instead of locking the merchant out of retrying.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { FlatList, Image, Pressable, RefreshControl, StyleSheet, Text, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import {
  getImportCart,
  importNeedsReview,
  importSelected,
  removeImportCartItem,
  stateForError,
  type DropshippingState,
  type ImportCartItem,
  type ImportRunResult,
  type PricingRule
} from "../../api/dropshipping";
import { StoreHeader } from "../../components/store";
import {
  costRangeText,
  DropshippingStaleNote,
  DropshippingStateView,
  NO_VALUE,
  ProviderBadge,
  stateOwnsScreen
} from "../../components/dropshipping/DropshippingStates";
import { PricingRulePicker } from "./PricingRulePicker";
import { useDropshippingScope } from "./useDropshippingScope";
import { useFormatters } from "../../i18n/hooks";
import { BOTTOM_NAV_CONTENT_CLEARANCE } from "../../navigation/BottomNavVisibility";
import { RootStackParamList } from "../../navigation/types";
import { storeLight } from "../../theme/storeLight";
import { useLogiNexusReducedMotion } from "../../theme/logiNexusMotion";

type Props = {
  route: { params: RootStackParamList["DropshippingCart"] };
  navigation: { navigate: (...args: any[]) => void; goBack?: () => void };
};

/**
 * What each outcome means to a merchant, and whether it needs them.
 *
 * Every one of the server's eight outcomes has an entry. An outcome this app
 * does not recognise falls to a neutral "couldn't be imported" rather than to a
 * success — reporting an unknown outcome as imported would send the merchant
 * looking for a product that does not exist.
 */
const OUTCOME_COPY: Record<string, { label: string; tone: "success" | "neutral" | "warning" }> = {
  IMPORTED: { label: "Imported as a draft", tone: "success" },
  ALREADY_EXISTS: { label: "Already in your store", tone: "neutral" },
  PROVIDER_UNAVAILABLE: { label: "Your supplier didn't respond — try this one again", tone: "warning" },
  INVALID_PRODUCT: { label: "Your supplier's data for this product wasn't usable", tone: "warning" },
  NO_VARIANTS: { label: "No variants to sell", tone: "warning" },
  NO_MEDIA: { label: "No usable images", tone: "warning" },
  RESTRICTED: { label: "This product can't be sold here", tone: "warning" },
  NEEDS_REVIEW: { label: "Imported, but needs your review before publishing", tone: "warning" }
};

export function ImportCartScreen({ route, navigation }: Props) {
  const { connectionId } = route.params;
  const formatters = useFormatters();
  const reducedMotion = useLogiNexusReducedMotion();
  const insets = useSafeAreaInsets();
  const scopeStatus = useDropshippingScope();

  const [items, setItems] = useState<ImportCartItem[]>([]);
  const [staleCount, setStaleCount] = useState(0);
  const [selected, setSelected] = useState<string[]>([]);
  const [rule, setRule] = useState<PricingRule>({ type: "COST_PLUS_PERCENT", value: 60 });
  const [state, setState] = useState<DropshippingState>("LOADING");
  const [refreshing, setRefreshing] = useState(false);
  const [importing, setImporting] = useState(false);
  const [run, setRun] = useState<ImportRunResult | null>(null);
  const [runError, setRunError] = useState<string | null>(null);

  const scope = scopeStatus.status.phase === "ready" ? scopeStatus.status.scope : null;

  const load = useCallback(
    async (mode: "initial" | "refresh" = "initial") => {
      if (!scope) return;
      if (mode === "refresh") setRefreshing(true);
      else setState("LOADING");
      try {
        const cart = await getImportCart(scope, connectionId);
        setItems(cart.items);
        setStaleCount(cart.staleCount);
        // Everything in the cart is selected by default — a merchant who opened
        // the cart intends to import it. Deselection is the deliberate act.
        setSelected(cart.items.map((item) => item.itemId));
        setState(cart.items.length === 0 ? "EMPTY" : cart.staleCount > 0 ? "STALE" : "READY");
      } catch (error) {
        setItems([]);
        setState(stateForError(error));
      } finally {
        setRefreshing(false);
      }
    },
    [connectionId, scope]
  );

  useEffect(() => {
    if (scopeStatus.status.phase === "ready") load().catch(() => undefined);
    else if (scopeStatus.status.phase === "failed") setState(scopeStatus.status.state);
    else if (scopeStatus.status.phase === "missing") setState("EMPTY");
    else setState("LOADING");
  }, [load, scopeStatus.status]);

  const toggle = useCallback((itemId: string) => {
    setSelected((prev) => (prev.includes(itemId) ? prev.filter((id) => id !== itemId) : [...prev, itemId]));
  }, []);

  const remove = useCallback(
    async (itemId: string) => {
      if (!scope) return;
      try {
        await removeImportCartItem(scope, connectionId, itemId);
        setItems((prev) => prev.filter((item) => item.itemId !== itemId));
        setSelected((prev) => prev.filter((id) => id !== itemId));
      } catch {
        // The row stays. A row that vanishes from the list while still sitting
        // in the server's cart reappears on the next refresh, which reads as
        // the app resurrecting something the merchant deleted.
      }
    },
    [connectionId, scope]
  );

  const doImport = useCallback(async () => {
    if (!scope || selected.length === 0) return;
    setImporting(true);
    setRun(null);
    setRunError(null);
    try {
      const result = await importSelected(scope, connectionId, {
        itemIds: selected,
        pricingRule: rule
      });
      setRun(result);
      await load("refresh").catch(() => undefined);
    } catch (error) {
      const failure = stateForError(error);
      setRunError(
        failure === "PROVIDER_UNAVAILABLE"
          ? "Your supplier didn't respond. Nothing was imported — your cart is unchanged."
          : failure === "SUPPLIER_DISCONNECTED"
            ? "Your supplier connection needs attention. Nothing was imported."
            : "That import didn't run. Nothing was imported and your cart is unchanged."
      );
    } finally {
      setImporting(false);
    }
  }, [connectionId, load, rule, scope, selected]);

  const stateBlock = stateOwnsScreen(state) ? (
    <DropshippingStateView
      state={state}
      subject="Your import cart"
      onRetry={state === "UNAUTHORIZED" ? null : () => load("refresh")}
      onFixConnection={() => navigation.navigate("DropshippingSuppliers", { title: "Suppliers" })}
      reducedMotion={reducedMotion}
      skeletonRows={3}
      empty={{
        title: "Your import cart is empty.",
        body: "Browse your supplier's catalogue and add the products you want to sell."
      }}
    />
  ) : null;

  const importable = selected.length > 0 && !importing && state !== "LOADING";

  // The pricing preview runs against the costs of what is actually selected,
  // nulls included — an item whose cost could not be read must show up in the
  // preview as unpriceable rather than being quietly dropped from the sample.
  const selectedCosts = useMemo(
    () =>
      items
        .filter((item) => selected.includes(item.itemId))
        .map((item) => item.preview?.costLowCents ?? null),
    [items, selected]
  );
  const previewCurrency = useMemo(
    () => items.find((item) => item.preview?.currency)?.preview?.currency ?? null,
    [items]
  );

  return (
    <View style={styles.root}>
      <StoreHeader
        title={route.params.title || "Import cart"}
        query=""
        onQueryChange={() => undefined}
        onSubmitSearch={() => undefined}
        onBack={() => navigation.goBack?.()}
        onNotifications={() =>
          navigation.navigate("DropshippingProducts", { connectionId, title: "Dropshipping products" })
        }
        unreadCount={0}
        searchPlaceholder="Import cart"
        reducedMotion={reducedMotion}
      />

      <FlatList
        data={stateBlock ? [] : items}
        keyExtractor={(item) => item.itemId}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => load("refresh")} />}
        contentContainerStyle={[
          styles.content,
          { paddingBottom: Math.max(insets.bottom, 16) + BOTTOM_NAV_CONTENT_CLEARANCE }
        ]}
        ListHeaderComponent={
          <View>
            {state === "STALE" && staleCount > 0 ? (
              <DropshippingStaleNote
                text={`${staleCount} of these were added a while ago. Every price and stock figure is re-checked from your supplier when you import.`}
              />
            ) : null}
            {stateBlock ? <View style={styles.block}>{stateBlock}</View> : null}
            {!stateBlock && items.length > 0 ? (
              <Text style={styles.counter}>
                {formatters.count(selected.length)} of {formatters.count(items.length)} selected
              </Text>
            ) : null}
          </View>
        }
        renderItem={({ item }) => (
          <CartRow
            item={item}
            selected={selected.includes(item.itemId)}
            costLabel={costRangeText(
              item.preview?.costLowCents ?? null,
              item.preview?.costHighCents ?? null,
              item.preview?.currency ?? null,
              formatters
            )}
            onToggle={() => toggle(item.itemId)}
            onRemove={() => void remove(item.itemId)}
            onOpen={() =>
              navigation.navigate("DropshippingProduct", {
                connectionId,
                externalProductId: item.externalProductId,
                title: item.preview?.title || "Supplier product"
              })
            }
          />
        )}
        ListFooterComponent={
          !stateBlock && items.length > 0 ? (
            <View style={styles.footer}>
              <PricingRulePicker
                rule={rule}
                onChange={setRule}
                sampleCostCents={selectedCosts}
                currency={previewCurrency}
              />

              <Pressable
                style={[styles.primary, importable ? null : styles.primaryDisabled]}
                onPress={() => void doImport()}
                disabled={!importable}
                accessibilityRole="button"
                accessibilityState={{ disabled: !importable }}
                accessibilityLabel={`Import ${selected.length} products as drafts`}
              >
                <Text style={styles.primaryText}>
                  {importing
                    ? "Importing…"
                    : `Import ${formatters.count(selected.length)} as drafts`}
                </Text>
              </Pressable>
              {/* Said on the screen, not just in the button, because "import"
                  reads as "publish" to plenty of merchants. */}
              <Text style={styles.note}>
                Imported products are drafts. Nothing appears in your store until you publish it.
              </Text>

              {runError ? <Text style={styles.error}>{runError}</Text> : null}
              {run ? (
                <ImportResultSheet
                  result={run}
                  onOpenProducts={() =>
                    navigation.navigate("DropshippingProducts", {
                      connectionId,
                      title: "Dropshipping products"
                    })
                  }
                />
              ) : null}
            </View>
          ) : null
        }
      />
    </View>
  );
}

function CartRow({
  item,
  selected,
  costLabel,
  onToggle,
  onRemove,
  onOpen
}: {
  item: ImportCartItem;
  selected: boolean;
  costLabel: string | null;
  onToggle: () => void;
  onRemove: () => void;
  onOpen: () => void;
}) {
  const title = item.preview?.title || `Product ${item.externalProductId}`;
  return (
    <View style={styles.row}>
      <Pressable
        style={[styles.checkbox, selected ? styles.checkboxOn : null]}
        onPress={onToggle}
        accessibilityRole="checkbox"
        accessibilityState={{ checked: selected }}
        accessibilityLabel={`${title}, ${selected ? "selected" : "not selected"} for import`}
      >
        {selected ? <Text style={styles.checkmark}>✓</Text> : null}
      </Pressable>

      <Pressable style={styles.rowBody} onPress={onOpen} accessibilityRole="button" accessibilityLabel={title}>
        {item.preview?.coverImageUrl ? (
          <Image source={{ uri: item.preview.coverImageUrl }} style={styles.thumb} resizeMode="cover" />
        ) : (
          <View style={[styles.thumb, styles.thumbEmpty]} />
        )}
        <View style={styles.rowText}>
          <Text style={styles.rowTitle} numberOfLines={2}>
            {title}
          </Text>
          <View style={styles.rowMetaRow}>
            <ProviderBadge provider={item.provider} />
            {item.stale ? <Text style={styles.staleTag}>Re-checked on import</Text> : null}
          </View>
          <Text style={styles.rowCost}>
            {costLabel === null ? `${NO_VALUE} cost unknown` : `${costLabel} cost`}
          </Text>
          {item.selectedVariantIds.length > 0 ? (
            <Text style={styles.rowMeta}>{item.selectedVariantIds.length} variants selected</Text>
          ) : item.preview?.variantCount ? (
            <Text style={styles.rowMeta}>All {item.preview.variantCount} variants</Text>
          ) : null}
        </View>
      </Pressable>

      <Pressable
        onPress={onRemove}
        hitSlop={10}
        style={styles.remove}
        accessibilityRole="button"
        accessibilityLabel={`Remove ${title} from your import cart`}
      >
        <Text style={styles.removeText}>Remove</Text>
      </Pressable>
    </View>
  );
}

/**
 * The per-item outcome of one import run.
 *
 * Lists every item, not just the failures — a merchant needs to see that eight
 * worked as much as they need to see that two did not, and a sheet that only
 * appears on failure teaches them that no news is a silent success.
 */
function ImportResultSheet({
  result,
  onOpenProducts
}: {
  result: ImportRunResult;
  onOpenProducts: () => void;
}) {
  const needsReview = useMemo(() => importNeedsReview(result), [result]);
  return (
    <View style={styles.sheet}>
      <Text style={styles.sheetTitle}>
        {result.imported === result.requested
          ? `All ${result.requested} imported as drafts.`
          : `${result.imported} of ${result.requested} imported as drafts.`}
      </Text>

      {result.results.map((item) => {
        const copy = OUTCOME_COPY[String(item.outcome)] || {
          label: "This one couldn't be imported",
          tone: "warning" as const
        };
        return (
          <View key={item.itemId || item.externalProductId} style={styles.sheetRow}>
            <View
              style={[
                styles.sheetDot,
                {
                  backgroundColor:
                    copy.tone === "success"
                      ? storeLight.status.success
                      : copy.tone === "warning"
                        ? storeLight.status.warning
                        : storeLight.status.neutral
                }
              ]}
            />
            <Text style={styles.sheetText}>
              {copy.label}
              {item.variantCount ? ` · ${item.variantCount} variants` : ""}
            </Text>
          </View>
        );
      })}

      {needsReview ? (
        <Text style={styles.sheetNote}>
          You can run this again — anything already imported won't be duplicated.
        </Text>
      ) : null}

      <Pressable
        style={styles.secondary}
        onPress={onOpenProducts}
        accessibilityRole="button"
        accessibilityLabel="Open your imported products"
      >
        <Text style={styles.secondaryText}>Open imported products</Text>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: storeLight.bg.page },
  content: { paddingBottom: 24 },
  block: { padding: storeLight.space.card },
  counter: { fontSize: 12, color: storeLight.text.muted, paddingHorizontal: storeLight.space.card, paddingVertical: 8 },
  row: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    padding: storeLight.space.card,
    backgroundColor: storeLight.bg.card,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: storeLight.border.hairline
  },
  checkbox: {
    width: 24,
    height: 24,
    borderRadius: 4,
    borderWidth: 1.5,
    borderColor: storeLight.border.secondaryButton,
    alignItems: "center",
    justifyContent: "center"
  },
  checkboxOn: { backgroundColor: storeLight.cta.from, borderColor: storeLight.cta.from },
  checkmark: { fontSize: 14, fontWeight: "900", color: storeLight.cta.text },
  rowBody: { flex: 1, flexDirection: "row", gap: 10, alignItems: "center" },
  thumb: {
    width: storeLight.size.thumb,
    height: storeLight.size.thumb,
    borderRadius: storeLight.radius.thumb,
    backgroundColor: storeLight.bg.skeleton
  },
  thumbEmpty: { borderWidth: StyleSheet.hairlineWidth, borderColor: storeLight.border.hairline },
  rowText: { flex: 1, gap: 3 },
  rowTitle: { fontSize: 13, fontWeight: "600", color: storeLight.text.primary, lineHeight: 17 },
  rowMetaRow: { flexDirection: "row", alignItems: "center", gap: 6 },
  staleTag: { fontSize: 10, color: storeLight.text.muted },
  rowCost: { fontSize: 12, fontWeight: "700", color: storeLight.text.primary },
  rowMeta: { fontSize: 11, color: storeLight.text.muted },
  remove: { minHeight: storeLight.size.tapTarget, justifyContent: "center", paddingLeft: 4 },
  removeText: { fontSize: 12, fontWeight: "600", color: storeLight.text.link },
  footer: { padding: storeLight.space.card, gap: storeLight.space.gutter },
  primary: {
    minHeight: storeLight.size.tapTarget,
    alignItems: "center",
    justifyContent: "center",
    borderRadius: storeLight.radius.pill,
    backgroundColor: storeLight.cta.from
  },
  primaryDisabled: { opacity: 0.5 },
  primaryText: { fontSize: 14, fontWeight: "800", color: storeLight.cta.text },
  note: { fontSize: 12, color: storeLight.text.muted, lineHeight: 17 },
  error: { fontSize: 13, fontWeight: "600", color: storeLight.status.error, lineHeight: 18 },
  sheet: {
    padding: storeLight.space.card,
    gap: 8,
    backgroundColor: storeLight.bg.card,
    borderRadius: storeLight.radius.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: storeLight.border.hairline
  },
  sheetTitle: { fontSize: 14, fontWeight: "700", color: storeLight.text.primary },
  sheetRow: { flexDirection: "row", alignItems: "center", gap: 8 },
  sheetDot: { width: 8, height: 8, borderRadius: 4 },
  sheetText: { flex: 1, fontSize: 12, color: storeLight.text.primary, lineHeight: 17 },
  sheetNote: { fontSize: 12, color: storeLight.text.muted, lineHeight: 17 },
  secondary: {
    minHeight: storeLight.size.tapTarget,
    alignItems: "center",
    justifyContent: "center",
    borderRadius: storeLight.radius.pill,
    borderWidth: 1,
    borderColor: storeLight.border.secondaryButton
  },
  secondaryText: { fontSize: 13, fontWeight: "600", color: storeLight.text.primary }
});
