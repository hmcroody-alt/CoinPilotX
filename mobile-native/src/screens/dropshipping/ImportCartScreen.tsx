/**
 * Import cart — review, price, import.
 *
 * ## The cart is not the product, and "import" now means published
 *
 * A cart row is a merchant's *intent* to import. It has no listing id, it is not
 * in the store, and deleting it deletes nothing a buyer could see.
 *
 * Import used to turn rows into drafts and stop, and this screen said so on the
 * button. It no longer does either. The server completes each listing, validates
 * it, and publishes it to the merchant's store — so the honest label is
 * "Import & publish", and the copy here is driven by the store's `autoPublish`
 * policy rather than hardcoded, because a merchant who turned auto-publish off
 * really is getting drafts and must not be told otherwise.
 *
 * ## Import Selected sends ids, and usually not a price rule
 *
 * `importSelected` takes `itemIds` and an *optional* pricing rule. It cannot take
 * a cost, and there is no field on this screen that would produce one. The
 * server re-fetches every economic fact from the provider before it writes. The
 * merchant's markup is arithmetic applied to a cost the *server* fetched, which
 * is why a rule is safe to send and a price is not.
 *
 * The rule is sent only when the merchant changed it here. That is what `override`
 * being `null` means. A screen that always sent its picker's current value would
 * send its own `useState` default on the very first import and silently outrank
 * the pricing policy the merchant saved for the store — §8's priority order
 * inverted by an initial value.
 *
 * ## Partial success is reported per item
 *
 * Nine published and one needing a fix is neither "published" nor "failed", and
 * collapsing it to either loses the one row the merchant has to do something
 * about. The result sheet lists every outcome by name, and gives the ones the
 * merchant can act on somewhere to go.
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
  getStoreImportPolicy,
  importNeedsReview,
  importSelected,
  removeImportCartItem,
  stateForError,
  type DropshippingState,
  type ImportCartItem,
  type ImportItemResult,
  type ImportOutcome,
  type ImportRunResult,
  type PricingRule,
  type StoreImportPolicy
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
import { anyFixable, publishProblemCopy } from "./publishProblems";
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
 * `Record<ImportOutcome, …>`, not `Record<string, …>`, and that is the whole
 * point of the annotation: an outcome added to `IMPORT_OUTCOMES` without copy
 * here fails the typecheck. As a `Record<string, …>` this table fell behind the
 * server by two outcomes — `PUBLISHED` and `NEEDS_ATTENTION` — and both landed in
 * the fallback below, so a merchant whose twenty products all went live would
 * have read twenty "couldn't be imported" rows.
 *
 * The unknown-outcome fallback is still a warning rather than a success, for the
 * same reason it always was: telling a merchant a product is in their store when
 * it is not sends them looking for something that does not exist.
 */
const OUTCOME_COPY: Record<ImportOutcome, { label: string; tone: "success" | "neutral" | "warning" }> = {
  // The ordinary outcome now. Worded as two facts rather than one because
  // "published" alone does not tell a merchant they can stop.
  PUBLISHED: { label: "Published — live and ready to sell", tone: "success" },
  // Imported, in the store, not live. A warning tone but not a failure: the
  // listing exists and the problems below say what to do, which is why these
  // rows are the ones that get a [Fix this].
  NEEDS_ATTENTION: { label: "Imported — needs one fix before it can go live", tone: "warning" },
  // Still reachable: a store with auto-publish turned off gets drafts on purpose.
  IMPORTED: { label: "Imported as a draft", tone: "success" },
  ALREADY_EXISTS: { label: "Already in your store", tone: "neutral" },
  PROVIDER_UNAVAILABLE: { label: "Your supplier didn't respond — try this one again", tone: "warning" },
  INVALID_PRODUCT: { label: "Your supplier's data for this product wasn't usable", tone: "warning" },
  NO_VARIANTS: { label: "No variants to sell", tone: "warning" },
  NO_MEDIA: { label: "No usable images", tone: "warning" },
  RESTRICTED: { label: "This product can't be sold here", tone: "warning" },
  // The platform's own pending moderation, which is not the merchant's to fix —
  // hence no action offered on this row, unlike NEEDS_ATTENTION.
  NEEDS_REVIEW: { label: "Imported, but needs your review before publishing", tone: "warning" }
};

/**
 * What the picker shows before the store's saved policy has been read.
 *
 * The platform's own default (45% target margin), so the number on screen is the
 * number an unconfigured store would really price at. It is display only — it is
 * never sent, because `override` starts `null`.
 */
const PLATFORM_FALLBACK_RULE: PricingRule = { type: "TARGET_MARGIN", value: 45 };

export function ImportCartScreen({ route, navigation }: Props) {
  const { connectionId } = route.params;
  const formatters = useFormatters();
  const reducedMotion = useLogiNexusReducedMotion();
  const insets = useSafeAreaInsets();
  const scopeStatus = useDropshippingScope();

  const [items, setItems] = useState<ImportCartItem[]>([]);
  const [staleCount, setStaleCount] = useState(0);
  const [selected, setSelected] = useState<string[]>([]);
  // Null means "price this the way my store prices things". Only a deliberate
  // change on the picker makes it non-null, and only a non-null value is sent —
  // see the module docstring, and `importSelected`'s own note about why an
  // always-sent rule inverts §8.
  const [override, setOverride] = useState<PricingRule | null>(null);
  const [policy, setPolicy] = useState<StoreImportPolicy | null>(null);
  // Separate from `policy` being null, because "not read yet" and "could not be
  // read" are different things to say to a merchant about their own pricing.
  const [policyRead, setPolicyRead] = useState(false);
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

  // Read separately from the cart, and never allowed to fail the screen. The
  // policy decides what the picker *displays*; the server resolves it again for
  // itself at import time. So a failed read costs the merchant an accurate
  // preview, not the ability to import — and it is said out loud rather than
  // papered over with a plausible-looking number.
  useEffect(() => {
    if (!scope) return;
    let live = true;
    getStoreImportPolicy(scope)
      .then((result) => {
        if (live) setPolicy(result);
      })
      .catch(() => undefined)
      .finally(() => {
        if (live) setPolicyRead(true);
      });
    return () => {
      live = false;
    };
  }, [scope]);

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
        // `override` and not the effective rule. Sending the resolved store rule
        // back would record every import as a per-request price, so a later edit
        // to the store's policy would stop reaching this screen.
        itemIds: selected,
        pricingRule: override
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
  }, [connectionId, load, override, scope, selected]);

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

  // Rows the merchant ticked whose supplier cost we do not have. No pricing rule
  // can turn an unknown cost into a sale price, so offering "Import & publish"
  // over one of these promises something the server will refuse: it imports,
  // fails the MISSING_PRICE check in `drafts._validate`, and lands as a
  // price-required draft while the button said it was going live.
  const unpriced = useMemo(
    () =>
      items.filter(
        (item) => selected.includes(item.itemId) && (item.preview?.costLowCents ?? null) === null
      ),
    [items, selected]
  );

  const importable = selected.length > 0 && !importing && state !== "LOADING" && unpriced.length === 0;

  // What this import will actually price at, in priority order: what the merchant
  // changed here, then their store's saved rule, then the platform's. The same
  // order the server resolves in, so the preview and the outcome agree.
  const effectiveRule = override ?? policy?.pricingRule ?? PLATFORM_FALLBACK_RULE;
  // Assumed on until told otherwise, matching the server's own default. Assuming
  // off would label the button "Import as drafts" for the one second before the
  // policy arrives, on a store that publishes.
  const autoPublish = policy?.autoPublish !== false;

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
              {/* Held back until the store's policy has been read, rather than
                  rendered with a placeholder rule and corrected a moment later.
                  The picker seeds its own text field from `rule` on mount, so a
                  rule that arrives afterwards would leave the merchant looking at
                  a number that is not the one in effect. */}
              {policyRead ? (
                <View style={styles.pricingBlock}>
                  <PricingRulePicker
                    rule={effectiveRule}
                    onChange={setOverride}
                    sampleCostCents={selectedCosts}
                    currency={previewCurrency}
                  />
                  <Text style={styles.note}>
                    {override
                      ? "Just for this import. Your store's saved pricing is unchanged."
                      : policy === null
                        ? "We couldn't read your store's pricing rule, so this preview uses the PulseSoc default. Your store's saved rule still applies when you import."
                        : policy.configured
                          ? "This is your store's pricing rule. Change it here to price only this import differently."
                          : "PulseSoc's default pricing, because your store hasn't set its own."}
                  </Text>
                </View>
              ) : (
                <Text style={styles.note}>Reading your store's pricing…</Text>
              )}

              <Pressable
                style={[styles.primary, importable ? null : styles.primaryDisabled]}
                onPress={() => void doImport()}
                disabled={!importable}
                accessibilityRole="button"
                accessibilityState={{ disabled: !importable }}
                accessibilityLabel={
                  unpriced.length > 0
                    ? `Resolve pricing issues on ${unpriced.length} products before importing`
                    : autoPublish
                      ? `Import and publish ${selected.length} products to your store`
                      : `Import ${selected.length} products as drafts`
                }
              >
                <Text style={styles.primaryText}>
                  {importing
                    ? `Importing ${formatters.count(selected.length)}…`
                    : unpriced.length > 0
                      ? "Resolve pricing issues"
                      : autoPublish
                        ? `Import & publish ${formatters.count(selected.length)}`
                        : `Import ${formatters.count(selected.length)} as drafts`}
                </Text>
              </Pressable>
              {/* Named, not counted. "1 product has no cost" leaves the merchant
                  hunting a list; the title is what they tap to deselect. Re-adding
                  from the catalogue is what re-asks the supplier, because the cart
                  caches a read rather than performing one. */}
              {unpriced.length > 0 ? (
                <Text style={styles.note}>
                  {`We couldn't read a supplier cost for ${unpriced
                    .map((item) => item.preview?.title || "an untitled product")
                    .join(", ")}, so we can't work out what to charge. Untick ${
                    unpriced.length === 1 ? "it" : "them"
                  } to import the rest, or add ${
                    unpriced.length === 1 ? "it" : "them"
                  } again from the catalogue to re-check with your supplier.`}
                </Text>
              ) : null}
              {/* Said on the screen, not just in the button, and conditional on
                  the policy rather than fixed: this line claimed drafts for years,
                  and under auto-publish that is now the false half. */}
              <Text style={styles.note}>
                {autoPublish
                  ? `These go live in your store, priced and ready to sell.${
                      policy?.marketplaceAutolist
                        ? " They're also offered across the PulseSoc Marketplace."
                        : " They stay in your store — turn on Marketplace listing to offer them PulseSoc-wide."
                    } Anything PulseSoc can't publish safely stays a draft and tells you why.`
                  : "Imported products are drafts. Nothing appears in your store until you publish it."}
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
                  onViewInStore={(listingId) =>
                    navigation.navigate("SellerStore", { mode: "product", listingId })
                  }
                  onFixItem={(listingId) =>
                    navigation.navigate("DropshippingDraft", {
                      connectionId,
                      listingId,
                      title: "Finish this product"
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
 * The honest summary of one run: how many are selling, how many need the merchant.
 *
 * §30's rule is that a batch reports itself truthfully — "17 published, 2 need
 * attention, 1 couldn't be imported" — rather than rounding to whichever number
 * flatters the run. Both directions of rounding are wrong: a success banner hides
 * the two rows that need work, and a failure banner hides seventeen products the
 * merchant could be selling this afternoon.
 *
 * Built from `publishedCount` / `needsAttention` and the result rows, not from
 * `imported`, which counts listings created and so counts a draft as a win.
 */
function runSummary(result: ImportRunResult): string {
  const parts: string[] = [];
  if (result.publishedCount > 0) parts.push(`${result.publishedCount} published`);
  if (result.needsAttention > 0) parts.push(`${result.needsAttention} need attention`);
  // Created but neither live nor flagged: an auto-publish-off store's drafts, and
  // anything already in the store. Counted from the rows because the server sends
  // no single number for it, and inferring it by subtraction would go negative the
  // first time a new outcome appears.
  const drafted = result.results.filter(
    (item) => item.outcome === "IMPORTED" || item.outcome === "ALREADY_EXISTS"
  ).length;
  if (drafted > 0) parts.push(`${drafted} saved as drafts`);
  const failed = result.requested - result.publishedCount - result.needsAttention - drafted;
  if (failed > 0) parts.push(`${failed} couldn't be imported`);
  if (parts.length === 0) return `Nothing was imported.`;
  if (parts.length === 1 && result.publishedCount === result.requested) {
    return result.requested === 1
      ? "Published and ready to sell."
      : `All ${result.requested} are published and ready to sell.`;
  }
  return `${parts.join(", ")}.`;
}

/**
 * The per-item outcome of one import run.
 *
 * Lists every item, not just the failures — a merchant needs to see that eight
 * worked as much as they need to see that two did not, and a sheet that only
 * appears on failure teaches them that no news is a silent success.
 *
 * The rows that can be acted on get somewhere to go. That is the §27 half this
 * sheet was missing: before auto-publish every row here was a draft the merchant
 * was going to open anyway, so naming the outcome was enough. Now most rows are
 * finished and a handful are not, and the handful is what the sheet is for.
 */
function ImportResultSheet({
  result,
  onOpenProducts,
  onViewInStore,
  onFixItem
}: {
  result: ImportRunResult;
  onOpenProducts: () => void;
  onViewInStore: (listingId: number) => void;
  onFixItem: (listingId: number) => void;
}) {
  const needsReview = useMemo(() => importNeedsReview(result), [result]);
  return (
    <View style={styles.sheet}>
      <Text style={styles.sheetTitle}>{runSummary(result)}</Text>

      {result.results.map((item) => (
        <ImportResultRow
          key={item.itemId || item.externalProductId}
          item={item}
          onViewInStore={onViewInStore}
          onFixItem={onFixItem}
        />
      ))}

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

function ImportResultRow({
  item,
  onViewInStore,
  onFixItem
}: {
  item: ImportItemResult;
  onViewInStore: (listingId: number) => void;
  onFixItem: (listingId: number) => void;
}) {
  const copy = OUTCOME_COPY[item.outcome as ImportOutcome] || {
    label: "This one couldn't be imported",
    tone: "warning" as const
  };
  // Read from the server's field, never from the outcome string, for the reason
  // `ImportItemResult.published` documents: an outcome this build cannot read
  // must not be able to make a draft look live.
  const live = item.published;
  // A row only gets [Fix this] when there is a listing to open *and* at least one
  // of its problems is the merchant's to solve. A delisted supplier product and a
  // pending moderation both have a listing and neither has a fix, and a button
  // that opens an editor where nothing can be changed is worse than no button.
  const fixable = item.listingId !== null && anyFixable(item.problems);
  return (
    <View style={styles.sheetItem}>
      <View style={styles.sheetRow}>
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
          {live && item.priceLabel ? ` · ${item.priceLabel}` : ""}
          {item.variantCount ? ` · ${item.variantCount} variants` : ""}
        </Text>
      </View>

      {/* The reason, in words, on the row it belongs to. A code the merchant
          cannot decode is rendered raw rather than swallowed — they can quote it
          to support, which is more than a generic apology gives them. */}
      {item.problems.map((problem) => {
        const problemCopy = publishProblemCopy(String(problem));
        return (
          <Text key={String(problem)} style={styles.sheetProblem}>
            {problemCopy ? problemCopy.text : String(problem)}
          </Text>
        );
      })}

      {fixable && item.listingId !== null ? (
        <Pressable
          onPress={() => onFixItem(item.listingId as number)}
          hitSlop={8}
          style={styles.sheetAction}
          accessibilityRole="button"
          accessibilityLabel="Fix this product so it can go live"
        >
          <Text style={styles.sheetActionText}>Fix this</Text>
        </Pressable>
      ) : live && item.listingId !== null ? (
        <Pressable
          onPress={() => onViewInStore(item.listingId as number)}
          hitSlop={8}
          style={styles.sheetAction}
          accessibilityRole="button"
          accessibilityLabel="View this product in your store"
        >
          <Text style={styles.sheetActionText}>View in store</Text>
        </Pressable>
      ) : null}
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
  sheetItem: { gap: 4 },
  sheetRow: { flexDirection: "row", alignItems: "center", gap: 8 },
  sheetDot: { width: 8, height: 8, borderRadius: 4 },
  sheetText: { flex: 1, fontSize: 12, color: storeLight.text.primary, lineHeight: 17 },
  // Indented to the width of the dot and its gap, so a problem reads as belonging
  // to the row above it rather than as another outcome.
  sheetProblem: { fontSize: 12, color: storeLight.text.muted, lineHeight: 17, paddingLeft: 16 },
  sheetAction: { minHeight: storeLight.size.tapTarget, justifyContent: "center", paddingLeft: 16 },
  sheetActionText: { fontSize: 12, fontWeight: "700", color: storeLight.text.link },
  pricingBlock: { gap: 8 },
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
