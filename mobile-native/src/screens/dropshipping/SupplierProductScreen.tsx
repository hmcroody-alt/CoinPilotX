/**
 * One supplier product, with its variants — the screen a merchant decides on.
 *
 * ## Variants arrive here, not on the grid
 *
 * The catalogue's search endpoint returns summaries without variants and the
 * backend deliberately does not fire fifty extra provider calls to fill a grid
 * the merchant will scroll past. This is the first screen where variant-level
 * cost and stock exist, which makes it the first screen where a merchant can
 * meaningfully choose *which* variants to import.
 *
 * ## A failed inventory read is not an out-of-stock product
 *
 * `inventoryFresh: false` means the live overlay failed and the catalogue's own
 * signal stands — including `UNKNOWN`. The screen says the stock figures may be
 * behind. It does not grey out the variants, because presenting a provider
 * outage as a sold-out product is how a merchant abandons a product that is
 * perfectly available.
 *
 * ## Selection defaults to everything sellable
 *
 * Every variant starts selected. A merchant importing a 40-variant shirt wants
 * all of them far more often than they want to tap 40 checkboxes, and the ones
 * they do not want are removed in the cart or unpriced in the draft. Variants
 * the supplier reports as out of stock start unselected — importing something
 * known-unbuyable is the one default that would be wrong.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { FlatList, Image, Pressable, RefreshControl, StyleSheet, Text, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import {
  addImportCartItem,
  getSupplierProduct,
  stateForError,
  type DropshippingState,
  type SupplierProductDetail,
  type SupplierVariant
} from "../../api/dropshipping";
import { StoreHeader } from "../../components/store";
import {
  costRangeText,
  costText,
  DropshippingStaleNote,
  DropshippingStateView,
  NO_VALUE,
  ProviderBadge,
  StockPill,
  stateOwnsScreen
} from "../../components/dropshipping/DropshippingStates";
import { useDropshippingScope } from "./useDropshippingScope";
import { useFormatters } from "../../i18n/hooks";
import { BOTTOM_NAV_CONTENT_CLEARANCE } from "../../navigation/BottomNavVisibility";
import { RootStackParamList } from "../../navigation/types";
import { storeLight } from "../../theme/storeLight";
import { useLogiNexusReducedMotion } from "../../theme/logiNexusMotion";

type Props = {
  route: { params: RootStackParamList["DropshippingProduct"] };
  navigation: { navigate: (...args: any[]) => void; goBack?: () => void };
};

/** Which variants start ticked. See the note at the top about the default. */
function defaultSelection(variants: SupplierVariant[]): string[] {
  return variants
    .filter((variant) => String(variant.stockState).toUpperCase() !== "OUT_OF_STOCK")
    .map((variant) => variant.providerVariantId)
    .filter(Boolean);
}

export function SupplierProductScreen({ route, navigation }: Props) {
  const { connectionId, externalProductId } = route.params;
  const formatters = useFormatters();
  const reducedMotion = useLogiNexusReducedMotion();
  const insets = useSafeAreaInsets();
  const scopeStatus = useDropshippingScope();

  const [product, setProduct] = useState<SupplierProductDetail | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [state, setState] = useState<DropshippingState>("LOADING");
  const [refreshing, setRefreshing] = useState(false);
  const [adding, setAdding] = useState(false);
  const [added, setAdded] = useState(false);
  const [addFailed, setAddFailed] = useState(false);

  const scope = scopeStatus.status.phase === "ready" ? scopeStatus.status.scope : null;

  const load = useCallback(
    async (mode: "initial" | "refresh" = "initial") => {
      if (!scope) return;
      if (mode === "refresh") setRefreshing(true);
      else setState("LOADING");
      try {
        const detail = await getSupplierProduct(scope, connectionId, externalProductId);
        setProduct(detail);
        setSelected(defaultSelection(detail.variants));
        setState(detail.cached ? "STALE" : "READY");
      } catch (error) {
        setProduct(null);
        setState(stateForError(error));
      } finally {
        setRefreshing(false);
      }
    },
    [connectionId, externalProductId, scope]
  );

  useEffect(() => {
    if (scopeStatus.status.phase === "ready") load().catch(() => undefined);
    else if (scopeStatus.status.phase === "failed") setState(scopeStatus.status.state);
    else if (scopeStatus.status.phase === "missing") setState("EMPTY");
    else setState("LOADING");
  }, [load, scopeStatus.status]);

  const toggle = useCallback((variantId: string) => {
    setSelected((prev) =>
      prev.includes(variantId) ? prev.filter((id) => id !== variantId) : [...prev, variantId]
    );
  }, []);

  const addToCart = useCallback(async () => {
    if (!scope || !product) return;
    setAdding(true);
    setAddFailed(false);
    try {
      await addImportCartItem(scope, connectionId, {
        externalProductId: product.externalProductId,
        provider: product.provider || undefined,
        selectedVariantIds: selected,
        preview: {
          title: product.title,
          cover_image_url: product.coverImageUrl,
          category: product.category,
          origin: product.origin,
          currency: product.currency,
          variant_count: product.variants.length,
          cost_low_cents: product.costLowCents,
          cost_high_cents: product.costHighCents
        }
      });
      setAdded(true);
    } catch {
      setAddFailed(true);
    } finally {
      setAdding(false);
    }
  }, [connectionId, product, scope, selected]);

  const range = useMemo(
    () =>
      product
        ? costRangeText(product.costLowCents, product.costHighCents, product.currency, formatters)
        : null,
    [formatters, product]
  );

  const stateBlock = stateOwnsScreen(state) ? (
    <DropshippingStateView
      state={state}
      subject="This product"
      onRetry={state === "UNAUTHORIZED" ? null : () => load("refresh")}
      onFixConnection={() => navigation.navigate("DropshippingSuppliers", { title: "Suppliers" })}
      reducedMotion={reducedMotion}
      empty={{
        title: "Your supplier no longer lists this product.",
        body: "It may have been discontinued. Nothing in your store has changed."
      }}
    />
  ) : null;

  return (
    <View style={styles.root}>
      <StoreHeader
        title={route.params.title || "Supplier product"}
        query=""
        onQueryChange={() => undefined}
        onSubmitSearch={() => undefined}
        onBack={() => navigation.goBack?.()}
        onNotifications={() =>
          navigation.navigate("DropshippingCart", { connectionId, title: "Import cart" })
        }
        unreadCount={0}
        searchPlaceholder="Supplier product"
        reducedMotion={reducedMotion}
      />

      <FlatList
        data={stateBlock || !product ? [] : product.variants}
        keyExtractor={(item, index) => item.providerVariantId || item.variantKey || String(index)}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => load("refresh")} />}
        contentContainerStyle={[
          styles.content,
          { paddingBottom: Math.max(insets.bottom, 16) + BOTTOM_NAV_CONTENT_CLEARANCE }
        ]}
        ListHeaderComponent={
          <View style={styles.header}>
            {stateBlock ? (
              stateBlock
            ) : product ? (
              <>
                {state === "STALE" ? (
                  <DropshippingStaleNote text="These details come from your supplier's cache. Everything is re-checked when you import." />
                ) : null}

                {product.coverImageUrl ? (
                  <Image source={{ uri: product.coverImageUrl }} style={styles.hero} resizeMode="cover" />
                ) : null}

                <View style={styles.titleRow}>
                  <ProviderBadge provider={product.provider} />
                  {product.origin ? <Text style={styles.origin}>Ships from {product.origin}</Text> : null}
                </View>

                <Text style={styles.title}>{product.title}</Text>
                <Text style={styles.cost}>
                  {range === null ? `Supplier cost ${NO_VALUE} unknown` : `Supplier cost ${range}`}
                </Text>
                {product.category ? <Text style={styles.meta}>{product.category}</Text> : null}

                {/* Stated, not implied by greyed rows. See the note at the top. */}
                {!product.inventoryFresh ? (
                  <Text style={styles.warning}>
                    Live stock didn't load, so these figures may be behind. Nothing is assumed to be
                    out of stock.
                  </Text>
                ) : null}

                {product.description ? (
                  <Text style={styles.description} numberOfLines={6}>
                    {product.description}
                  </Text>
                ) : null}

                <Text style={styles.sectionTitle}>
                  Variants ({formatters.count(product.variants.length)})
                </Text>
                {product.variants.length === 0 ? (
                  <Text style={styles.meta}>
                    Your supplier listed no variants for this product, so there is nothing to import
                    yet.
                  </Text>
                ) : (
                  <Text style={styles.meta}>
                    {formatters.count(selected.length)} selected. Unticked variants aren't imported.
                  </Text>
                )}
              </>
            ) : null}
          </View>
        }
        renderItem={({ item }) => (
          <VariantRow
            variant={item}
            selected={selected.includes(item.providerVariantId)}
            costLabel={costText(item.costCents, item.currency || product?.currency || null, formatters)}
            onToggle={() => toggle(item.providerVariantId)}
          />
        )}
        ListFooterComponent={
          product && product.variants.length > 0 ? (
            <View style={styles.footer}>
              {addFailed ? (
                <Text style={styles.warning}>
                  That didn't reach your cart. Nothing was added — try again.
                </Text>
              ) : null}
              <Pressable
                style={[styles.primary, selected.length === 0 || added ? styles.primaryDisabled : null]}
                onPress={() => void addToCart()}
                disabled={adding || added || selected.length === 0}
                accessibilityRole="button"
                accessibilityState={{ disabled: adding || added || selected.length === 0 }}
                accessibilityLabel={
                  added
                    ? "This product is in your import cart"
                    : `Add ${selected.length} variants to your import cart`
                }
              >
                <Text style={styles.primaryText}>
                  {added
                    ? "In your import cart"
                    : adding
                      ? "Adding…"
                      : `Add to import cart (${formatters.count(selected.length)})`}
                </Text>
              </Pressable>
              {added ? (
                <Pressable
                  style={styles.secondary}
                  onPress={() =>
                    navigation.navigate("DropshippingCart", { connectionId, title: "Import cart" })
                  }
                  accessibilityRole="button"
                  accessibilityLabel="Review your import cart"
                >
                  <Text style={styles.secondaryText}>Review import cart</Text>
                </Pressable>
              ) : null}
            </View>
          ) : null
        }
      />
    </View>
  );
}

function VariantRow({
  variant,
  selected,
  costLabel,
  onToggle
}: {
  variant: SupplierVariant;
  selected: boolean;
  costLabel: string | null;
  onToggle: () => void;
}) {
  const optionText = Object.entries(variant.options)
    .map(([key, value]) => `${key}: ${value}`)
    .join(" · ");

  return (
    <Pressable
      style={styles.variant}
      onPress={onToggle}
      accessibilityRole="checkbox"
      accessibilityState={{ checked: selected }}
      accessibilityLabel={`${optionText || variant.sku || "Variant"}, ${
        costLabel === null ? "cost unknown" : `cost ${costLabel}`
      }`}
    >
      <View style={[styles.checkbox, selected ? styles.checkboxOn : null]}>
        {selected ? <Text style={styles.checkmark}>✓</Text> : null}
      </View>
      <View style={styles.variantBody}>
        <Text style={styles.variantTitle} numberOfLines={2}>
          {optionText || variant.sku || "Variant"}
        </Text>
        <View style={styles.variantMetaRow}>
          <Text style={styles.variantCost}>
            {costLabel === null ? `${NO_VALUE} cost unknown` : costLabel}
          </Text>
          <StockPill state={String(variant.stockState)} quantity={variant.stockQuantity} />
        </View>
      </View>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: storeLight.bg.page },
  content: { paddingBottom: 24 },
  header: { padding: storeLight.space.card, gap: 8, backgroundColor: storeLight.bg.card },
  hero: { width: "100%", height: 220, borderRadius: storeLight.radius.card, backgroundColor: storeLight.bg.skeleton },
  titleRow: { flexDirection: "row", alignItems: "center", gap: 8 },
  origin: { fontSize: 11, color: storeLight.text.muted },
  title: { fontSize: 18, fontWeight: "700", color: storeLight.text.primary, lineHeight: 24 },
  cost: { fontSize: 15, fontWeight: "700", color: storeLight.text.primary },
  meta: { fontSize: 12, color: storeLight.text.muted },
  warning: { fontSize: 12, color: storeLight.status.warning, lineHeight: 17 },
  description: { fontSize: 13, color: storeLight.text.muted, lineHeight: 19 },
  sectionTitle: { fontSize: 15, fontWeight: "700", color: storeLight.text.primary, marginTop: 8 },
  variant: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    minHeight: storeLight.size.tapTarget + 16,
    paddingHorizontal: storeLight.space.card,
    paddingVertical: 10,
    backgroundColor: storeLight.bg.card,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: storeLight.border.hairline
  },
  checkbox: {
    width: 22,
    height: 22,
    borderRadius: 4,
    borderWidth: 1.5,
    borderColor: storeLight.border.secondaryButton,
    alignItems: "center",
    justifyContent: "center"
  },
  checkboxOn: { backgroundColor: storeLight.cta.from, borderColor: storeLight.cta.from },
  checkmark: { fontSize: 14, fontWeight: "900", color: storeLight.cta.text },
  variantBody: { flex: 1, gap: 4 },
  variantTitle: { fontSize: 13, fontWeight: "600", color: storeLight.text.primary },
  variantMetaRow: { flexDirection: "row", alignItems: "center", gap: 8 },
  variantCost: { fontSize: 12, fontWeight: "700", color: storeLight.text.primary },
  footer: { padding: storeLight.space.card, gap: 10 },
  primary: {
    minHeight: storeLight.size.tapTarget,
    alignItems: "center",
    justifyContent: "center",
    borderRadius: storeLight.radius.pill,
    backgroundColor: storeLight.cta.from
  },
  primaryDisabled: { opacity: 0.5 },
  primaryText: { fontSize: 14, fontWeight: "800", color: storeLight.cta.text },
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
