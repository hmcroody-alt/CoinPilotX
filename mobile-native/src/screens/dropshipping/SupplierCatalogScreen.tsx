/**
 * Find products — browsing a supplier's catalogue.
 *
 * ## Nothing on this screen is trusted later
 *
 * Every figure here is a preview. The import re-fetches cost, stock, title and
 * variant identity from the provider before it writes anything, which is why it
 * is safe to serve this from the server's cache and why a stale price on a card
 * cannot become a wrong price in the store. The screen says so rather than
 * hiding it: `cached` results get the stale note.
 *
 * ## Adding to the cart sends an id and a preview, never economics
 *
 * `addImportCartItem` carries the card's summary so the cart can draw something
 * before it re-fetches. The server keeps that as a *cache* on the cart row and
 * throws it away at import time. There is no path from this screen to a stored
 * cost.
 *
 * ## Paging, not one enormous page
 *
 * The server caps `size` at 50 and this screen asks for 20. A merchant scrolling
 * is served more pages; an unbounded page size is a provider-side amplification
 * lever, and the backend refuses one anyway.
 *
 * ## Unknown cost renders as unknown
 *
 * A card whose `costLowCents` is `null` shows an em dash, not "$0.00" and not
 * "Free". The supplier did not tell us the price; saying zero would be the
 * single most expensive lie this screen could tell.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { FlatList, Image, Pressable, RefreshControl, StyleSheet, Text, TextInput, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import {
  addImportCartItem,
  searchSupplierProducts,
  stateForError,
  type DropshippingScope,
  type DropshippingState,
  type SupplierProductCard
} from "../../api/dropshipping";
import { StoreHeader } from "../../components/store";
import {
  costRangeText,
  DropshippingStaleNote,
  DropshippingStateView,
  NO_VALUE,
  stateOwnsScreen
} from "../../components/dropshipping/DropshippingStates";
import { useDropshippingScope } from "./useDropshippingScope";
import { useFormatters } from "../../i18n/hooks";
import { BOTTOM_NAV_CONTENT_CLEARANCE } from "../../navigation/BottomNavVisibility";
import { RootStackParamList } from "../../navigation/types";
import { storeLight } from "../../theme/storeLight";
import { useLogiNexusReducedMotion } from "../../theme/logiNexusMotion";

const PAGE_SIZE = 20;

type Props = {
  route: { params: RootStackParamList["DropshippingCatalog"] };
  navigation: { navigate: (...args: any[]) => void; goBack?: () => void };
};

export function SupplierCatalogScreen({ route, navigation }: Props) {
  const { connectionId } = route.params;
  const formatters = useFormatters();
  const reducedMotion = useLogiNexusReducedMotion();
  const insets = useSafeAreaInsets();
  const scopeStatus = useDropshippingScope();

  const [query, setQuery] = useState("");
  const [products, setProducts] = useState<SupplierProductCard[]>([]);
  const [page, setPage] = useState(1);
  const [hasMore, setHasMore] = useState(false);
  const [total, setTotal] = useState<number | null>(null);
  const [cached, setCached] = useState(false);
  const [state, setState] = useState<DropshippingState>("LOADING");
  const [refreshing, setRefreshing] = useState(false);
  const [adding, setAdding] = useState<string | null>(null);
  const [added, setAdded] = useState<string[]>([]);
  /**
   * The term `products` is the answer to — not the term in the box.
   *
   * Searching runs on submit, so between typing and submitting the box holds a
   * word nothing has been asked about yet. Captioning the empty state from
   * `query` told the merchant "Nothing matched 'phone'" while the only search
   * ever run was the blank one, which is a claim about a result we did not
   * have. Written when a search lands, so it can only ever name a term the
   * provider actually answered.
   */
  const [resultQuery, setResultQuery] = useState("");

  const scope = scopeStatus.status.phase === "ready" ? scopeStatus.status.scope : null;

  /**
   * Which search the in-flight response belongs to.
   *
   * A merchant who types, waits, then types again can have two searches running.
   * Without this the slower one wins whenever it lands second and the grid shows
   * results for a query that is no longer in the box.
   */
  const requestId = useRef(0);

  const run = useCallback(
    async (nextPage: number, mode: "replace" | "append" | "refresh") => {
      if (!scope) return;
      const ticket = ++requestId.current;
      // Read once, here: the box can change while this request is in flight,
      // and the results belong to the term that was sent, not the later one.
      const term = query.trim();
      if (mode === "refresh") setRefreshing(true);
      else if (mode === "replace") setState("LOADING");
      try {
        const result = await searchSupplierProducts(scope, connectionId, {
          filters: term ? { keyword: term } : {},
          page: nextPage,
          size: PAGE_SIZE
        });
        if (ticket !== requestId.current) return;
        const next = mode === "append" ? [...products, ...result.products] : result.products;
        setProducts(next);
        setResultQuery(term);
        setPage(result.page);
        setHasMore(result.hasMore);
        setTotal(result.total);
        setCached(result.cached);
        setState(next.length === 0 ? "EMPTY" : result.cached ? "STALE" : "READY");
      } catch (error) {
        if (ticket !== requestId.current) return;
        // A failed page never empties the grid the merchant is already reading.
        if (mode !== "append") setProducts([]);
        setState(stateForError(error));
      } finally {
        if (ticket === requestId.current) setRefreshing(false);
      }
    },
    [connectionId, products, query, scope]
  );

  useEffect(() => {
    if (scopeStatus.status.phase === "ready") run(1, "replace").catch(() => undefined);
    else if (scopeStatus.status.phase === "failed") setState(scopeStatus.status.state);
    else if (scopeStatus.status.phase === "missing") setState("EMPTY");
    else setState("LOADING");
    // `run` closes over `products` for the append case, so depending on it here
    // would re-search on every result. The search inputs are what should
    // re-trigger it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connectionId, scopeStatus.status.phase]);

  const submitSearch = useCallback(() => {
    setProducts([]);
    run(1, "replace").catch(() => undefined);
  }, [run]);

  const addToCart = useCallback(
    async (card: SupplierProductCard) => {
      if (!scope) return;
      setAdding(card.externalProductId);
      try {
        await addImportCartItem(scope, connectionId, {
          externalProductId: card.externalProductId,
          provider: card.provider || undefined,
          // The card's own summary, so the cart can draw a row immediately. The
          // server caches it and re-fetches everything at import time.
          preview: {
            title: card.title,
            cover_image_url: card.coverImageUrl,
            category: card.category,
            origin: card.origin,
            currency: card.currency,
            variant_count: card.variantCount,
            cost_low_cents: card.costLowCents,
            cost_high_cents: card.costHighCents
          }
        });
        setAdded((prev) => (prev.includes(card.externalProductId) ? prev : [...prev, card.externalProductId]));
      } catch {
        // Left off the added list, so the button returns to "Add" rather than
        // claiming a row exists in a cart that never received it.
      } finally {
        setAdding(null);
      }
    },
    [connectionId, scope]
  );

  const stateBlock = stateOwnsScreen(state) ? (
    <DropshippingStateView
      state={state}
      subject="Products"
      onRetry={state === "UNAUTHORIZED" ? null : () => run(1, "replace")}
      onFixConnection={() => navigation.navigate("DropshippingSuppliers", { title: "Suppliers" })}
      reducedMotion={reducedMotion}
      empty={{
        title: resultQuery ? `Nothing matched “${resultQuery}”.` : "This supplier has no products to show.",
        body: resultQuery
          ? "Try a shorter phrase, or a product type rather than a brand."
          : "Try searching for a product type to see what your supplier stocks."
      }}
    />
  ) : null;

  return (
    <View style={styles.root}>
      <StoreHeader
        title={route.params.title || "Find products"}
        query={query}
        onQueryChange={setQuery}
        onSubmitSearch={submitSearch}
        onBack={() => navigation.goBack?.()}
        onNotifications={() =>
          navigation.navigate("DropshippingCart", { connectionId, title: "Import cart" })
        }
        unreadCount={added.length}
        searchPlaceholder="Search your supplier's catalogue"
        reducedMotion={reducedMotion}
      />

      <FlatList
        data={stateBlock ? [] : products}
        keyExtractor={(item) => `${item.provider}:${item.externalProductId}`}
        numColumns={2}
        columnWrapperStyle={products.length > 0 ? styles.column : undefined}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => run(1, "refresh")} />}
        contentContainerStyle={[
          styles.content,
          { paddingBottom: Math.max(insets.bottom, 16) + BOTTOM_NAV_CONTENT_CLEARANCE }
        ]}
        ListHeaderComponent={
          <View>
            {/* STALE is a note above real results, never instead of them. */}
            {state === "STALE" && cached ? (
              <DropshippingStaleNote text="Showing your supplier's cached catalogue. Prices are re-checked when you import." />
            ) : null}
            {state === "READY" && total !== null ? (
              <Text style={styles.total}>{formatters.count(total)} products</Text>
            ) : null}
            {stateBlock ? <View style={styles.block}>{stateBlock}</View> : null}
          </View>
        }
        renderItem={({ item }) => (
          <CatalogCard
            card={item}
            costText={costRangeText(item.costLowCents, item.costHighCents, item.currency, formatters)}
            adding={adding === item.externalProductId}
            added={added.includes(item.externalProductId)}
            onOpen={() =>
              navigation.navigate("DropshippingProduct", {
                connectionId,
                externalProductId: item.externalProductId,
                title: item.title
              })
            }
            onAdd={() => void addToCart(item)}
          />
        )}
        onEndReachedThreshold={0.5}
        onEndReached={() => {
          if (hasMore && !refreshing && state !== "LOADING") run(page + 1, "append").catch(() => undefined);
        }}
        ListFooterComponent={
          added.length > 0 ? (
            <Pressable
              style={styles.cartCta}
              onPress={() => navigation.navigate("DropshippingCart", { connectionId, title: "Import cart" })}
              accessibilityRole="button"
              accessibilityLabel={`Review import cart, ${added.length} added`}
            >
              <Text style={styles.cartCtaText}>
                Review import cart ({formatters.count(added.length)})
              </Text>
            </Pressable>
          ) : null
        }
      />
    </View>
  );
}

function CatalogCard({
  card,
  costText,
  adding,
  added,
  onOpen,
  onAdd
}: {
  card: SupplierProductCard;
  costText: string | null;
  adding: boolean;
  added: boolean;
  onOpen: () => void;
  onAdd: () => void;
}) {
  return (
    <View style={styles.card}>
      <Pressable onPress={onOpen} accessibilityRole="button" accessibilityLabel={card.title}>
        {card.coverImageUrl ? (
          <Image source={{ uri: card.coverImageUrl }} style={styles.thumb} resizeMode="cover" />
        ) : (
          // A rejected or absent media URL renders as a blank tile, not as a
          // placeholder product image that a merchant could mistake for the item.
          <View style={[styles.thumb, styles.thumbEmpty]} />
        )}
        <Text style={styles.cardTitle} numberOfLines={2}>
          {card.title}
        </Text>
        <Text style={styles.cardCost}>
          {/* Supplier cost. Merchant-private, and `—` when unknown. */}
          {costText === null ? `${NO_VALUE} cost unknown` : `${costText} cost`}
        </Text>
        {card.variantCount ? (
          <Text style={styles.cardMeta}>{card.variantCount} variants</Text>
        ) : null}
      </Pressable>

      <Pressable
        style={[styles.addButton, added ? styles.addButtonDone : null]}
        onPress={onAdd}
        disabled={adding || added}
        accessibilityRole="button"
        accessibilityState={{ disabled: adding || added }}
        accessibilityLabel={added ? `${card.title} is in your import cart` : `Add ${card.title} to import cart`}
      >
        <Text style={[styles.addText, added ? styles.addTextDone : null]}>
          {added ? "In cart" : adding ? "Adding…" : "Add to cart"}
        </Text>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: storeLight.bg.page },
  content: { paddingTop: storeLight.space.gutter, paddingHorizontal: storeLight.space.card, gap: storeLight.space.gutter },
  block: { paddingVertical: storeLight.space.gutter },
  column: { gap: storeLight.space.gutter },
  total: { fontSize: 12, color: storeLight.text.muted, paddingBottom: 8 },
  card: {
    flex: 1,
    padding: 10,
    gap: 6,
    backgroundColor: storeLight.bg.card,
    borderRadius: storeLight.radius.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: storeLight.border.hairline
  },
  thumb: { width: "100%", height: 120, borderRadius: storeLight.radius.thumb, backgroundColor: storeLight.bg.skeleton },
  thumbEmpty: { borderWidth: StyleSheet.hairlineWidth, borderColor: storeLight.border.hairline },
  cardTitle: { fontSize: 13, fontWeight: "600", color: storeLight.text.primary, lineHeight: 17 },
  cardCost: { fontSize: 12, fontWeight: "700", color: storeLight.text.primary },
  cardMeta: { fontSize: 11, color: storeLight.text.muted },
  addButton: {
    minHeight: storeLight.size.tapTarget - 8,
    alignItems: "center",
    justifyContent: "center",
    borderRadius: storeLight.radius.pill,
    borderWidth: 1,
    borderColor: storeLight.border.secondaryButton
  },
  addButtonDone: { borderColor: storeLight.status.success },
  addText: { fontSize: 12, fontWeight: "700", color: storeLight.text.primary },
  addTextDone: { color: storeLight.status.success },
  cartCta: {
    marginTop: storeLight.space.section,
    minHeight: storeLight.size.tapTarget,
    alignItems: "center",
    justifyContent: "center",
    borderRadius: storeLight.radius.pill,
    backgroundColor: storeLight.cta.from
  },
  cartCtaText: { fontSize: 14, fontWeight: "800", color: storeLight.cta.text }
});
