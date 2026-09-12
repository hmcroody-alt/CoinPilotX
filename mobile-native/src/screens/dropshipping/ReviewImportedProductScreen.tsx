/**
 * Review an imported draft, price it, and publish it.
 *
 * ## The screen where the ownership split becomes visible
 *
 * Two kinds of field live on this product. The merchant owns title,
 * description, category, currency, media and retail price — those are inputs.
 * The supplier owns cost, stock, variant identity and availability — those are
 * text. There is no disabled input for a supplier-owned field, because a
 * greyed-out box is a promise that it becomes editable under some condition,
 * and none of these ever do.
 *
 * The server enforces this too (`drafts.EDITABLE`), and `DraftEdits` has no key
 * for a supplier-owned field, so this screen could not send one if it tried.
 * Three layers agree, which is the right number for the invariant that stops a
 * merchant editing their own cost basis.
 *
 * ## Only what changed is sent
 *
 * Every field the merchant edits becomes merchant-owned and is thereafter
 * protected from provider sync. That protection is earned by an actual edit, so
 * `dirty` tracks which keys were touched and only those are PATCHed. Sending
 * the whole form would freeze the untouched fields against future supplier
 * corrections — the merchant would stop receiving upstream title and image
 * fixes for a product they never edited.
 *
 * ## Every reason, at once
 *
 * `validation.problems` is a list and is rendered as a list. A publish gate
 * that reports one problem per attempt makes the merchant discover a
 * three-problem draft in three round trips, and the third one is usually the
 * one they cannot fix.
 *
 * ## Publishing is not appearing
 *
 * `awaitingModeration` is stated after a successful publish. A merchant told
 * "published" who then cannot find their product in the marketplace concludes
 * the marketplace is broken, when it is correctly holding an unapproved item.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Image,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View
} from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import {
  bindDraftVariant,
  getImportedProduct,
  publishImportedProduct,
  stateForError,
  updateImportedProduct,
  validateImportedProduct,
  type DraftEdits,
  type DraftVariant,
  type DropshippingState,
  type ImportedDraft,
  type PublishProblem,
  type PublishResult
} from "../../api/dropshipping";
import { StoreHeader, StoreSectionError } from "../../components/store";
import {
  DropshippingStateView,
  MarginPill,
  NO_VALUE,
  ProviderBadge,
  StockPill,
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
  route: { params: RootStackParamList["DropshippingDraft"] };
  navigation: { navigate: (...args: any[]) => void; goBack?: () => void };
};

/**
 * Every publish problem, in the merchant's words, with what to do about it.
 *
 * The ones marked `fixable: false` are not the merchant's to fix, and say so —
 * telling someone to "add a price" when their supplier has delisted the product
 * wastes their afternoon.
 *
 * Keyed by `PublishProblem`, not by `string`, and that is the load-bearing part.
 * As a `Record<string, …>` this table could fall behind `PUBLISH_PROBLEMS`
 * without anything noticing, and it did: three codes the backend had been
 * emitting for some time — `VARIANT_PRICE_SPREAD`, `PRICE_ABOVE_CHECKOUT_LIMIT`
 * and `SUPPLIER_VARIANT_UNBOUND` — had no entry, and the fallthrough below
 * renders an unknown code verbatim. A merchant whose import could not be
 * published read the words "SUPPLIER_VARIANT_UNBOUND". Total over the union, the
 * next added code fails the typecheck instead.
 */
const PROBLEM_COPY: Record<PublishProblem, { text: string; fixable: boolean }> = {
  MISSING_TITLE: { text: "Give this product a title.", fixable: true },
  MISSING_CATEGORY: { text: "Choose a category so buyers can find it.", fixable: true },
  NO_VALID_MEDIA: {
    text: "This product has no usable images. Your supplier's images couldn't be used.",
    fixable: true
  },
  NO_VARIANTS_SELECTED: { text: "No variants are set up to sell.", fixable: true },
  MISSING_PRICE: { text: "Set a price for every variant you want to sell.", fixable: true },
  NEGATIVE_MARGIN: {
    text: "At least one variant costs more than you're charging for it.",
    fixable: true
  },
  UNKNOWN_INVENTORY: {
    text: "We couldn't read stock levels from your supplier. Check the connection and try again.",
    fixable: false
  },
  SUPPLIER_DISCONNECTED: {
    text: "Your supplier connection needs attention before this can go live.",
    fixable: false
  },
  PROVIDER_PRODUCT_UNAVAILABLE: {
    text: "Your supplier no longer offers this product.",
    fixable: false
  },
  RESTRICTED_PRODUCT: { text: "This product can't be sold on PulseSoc.", fixable: false },
  // Checkout charges one price for the whole listing and shows buyers no variant
  // picker, so two different prices cannot both be honoured. Named as a pricing
  // problem rather than a checkout limitation because the merchant's action is
  // the same either way: make them match.
  VARIANT_PRICE_SPREAD: {
    text: "Your variants have different prices. Checkout charges one price per product, so set them all to the same amount.",
    fixable: true
  },
  PRICE_ABOVE_CHECKOUT_LIMIT: {
    text: "That price is above what checkout can charge. Lower it to $999,999.99 or less.",
    fixable: true
  },
  // Fixable, and fixable *here* — see the variant chooser below. Before that
  // existed this was the one problem in this table with no remedy anywhere in
  // the app, which is why it is worded as a question rather than an error.
  SUPPLIER_VARIANT_UNBOUND: {
    text: "Choose which variant you're selling, below. A product sells one variant, and orders go to your supplier for that one.",
    fixable: true
  }
};

/** Merchant-editable keys, mirroring `DraftEdits`. Nothing else is a field. */
type EditableKey = "title" | "description" | "category" | "currency";

type Form = Record<EditableKey, string>;

function formFromDraft(draft: ImportedDraft): Form {
  return {
    title: draft.title || "",
    description: draft.description || "",
    category: draft.category || "",
    currency: draft.currency || ""
  };
}

/**
 * Retail price as the merchant types it, keyed by variant.
 *
 * An empty string is not zero — it is "unpriced", and it PATCHes as `null`.
 * Collapsing those two is how a merchant who cleared a price ends up giving a
 * product away.
 */
function pricesFromDraft(draft: ImportedDraft): Record<string, string> {
  const prices: Record<string, string> = {};
  for (const variant of draft.variants) {
    const key = variantKey(variant);
    if (!key) continue;
    prices[key] = variant.retailCents === null ? "" : (variant.retailCents / 100).toFixed(2);
  }
  return prices;
}

function variantKey(variant: DraftVariant): string {
  return variant.variantId === null ? variant.providerVariantId || "" : String(variant.variantId);
}

function priceToCents(raw: string): number | null {
  const cleaned = raw.replace(/,/g, ".").trim();
  if (!cleaned) return null;
  const parsed = Number(cleaned);
  if (!Number.isFinite(parsed) || parsed < 0) return null;
  return Math.round(parsed * 100);
}

function variantLabel(variant: DraftVariant): string {
  const options = Object.values(variant.options).filter(Boolean);
  if (options.length > 0) return options.join(" · ");
  return variant.sku || variant.providerVariantId || "Variant";
}

export function ReviewImportedProductScreen({ route, navigation }: Props) {
  const { connectionId, listingId } = route.params;
  const formatters = useFormatters();
  const reducedMotion = useLogiNexusReducedMotion();
  const insets = useSafeAreaInsets();
  const scopeStatus = useDropshippingScope();

  const [draft, setDraft] = useState<ImportedDraft | null>(null);
  const [form, setForm] = useState<Form | null>(null);
  const [prices, setPrices] = useState<Record<string, string>>({});
  const [state, setState] = useState<DropshippingState>("LOADING");
  const [refreshing, setRefreshing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [publishing, setPublishing] = useState(false);
  const [published, setPublished] = useState<PublishResult | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  // The variant the merchant has picked but not yet committed. Binding is close
  // to one-way — the server accepts nothing→one and refuses one→another — so
  // this is a two-step choice on purpose. A single tap that immediately bound
  // would be a permanent decision made by a mis-tap.
  const [pendingVariantId, setPendingVariantId] = useState<string | null>(null);
  const [binding, setBinding] = useState(false);

  // Which keys the merchant actually touched. Ownership follows edits, so this
  // set is the difference between protecting a field and freezing it. It is
  // state rather than a ref because the Save button's enabled-ness is derived
  // from it, and a ref would leave the button dead after the first edit.
  const [dirty, setDirty] = useState<ReadonlySet<EditableKey | "prices">>(new Set());

  const scope = scopeStatus.status.phase === "ready" ? scopeStatus.status.scope : null;

  const adopt = useCallback((next: ImportedDraft) => {
    setDraft(next);
    setForm(formFromDraft(next));
    setPrices(pricesFromDraft(next));
    setDirty(new Set());
  }, []);

  const load = useCallback(
    async (mode: "initial" | "refresh" = "initial") => {
      if (!scope) return;
      if (mode === "refresh") setRefreshing(true);
      else setState("LOADING");
      try {
        adopt(await getImportedProduct(scope, connectionId, listingId));
        setState("READY");
      } catch (error) {
        setDraft(null);
        setState(stateForError(error));
      } finally {
        setRefreshing(false);
      }
    },
    [adopt, connectionId, listingId, scope]
  );

  useEffect(() => {
    if (scopeStatus.status.phase === "ready") load().catch(() => undefined);
    else if (scopeStatus.status.phase === "failed") setState(scopeStatus.status.state);
    else if (scopeStatus.status.phase === "missing") setState("EMPTY");
    else setState("LOADING");
  }, [load, scopeStatus.status]);

  const editField = useCallback((key: EditableKey, value: string) => {
    setDirty((current) => new Set(current).add(key));
    setForm((current) => (current ? { ...current, [key]: value } : current));
  }, []);

  const editPrice = useCallback((key: string, value: string) => {
    setDirty((current) => new Set(current).add("prices"));
    setPrices((current) => ({ ...current, [key]: value }));
  }, []);

  const save = useCallback(async () => {
    if (!scope || !form || dirty.size === 0) return;
    setSaving(true);
    setActionError(null);
    try {
      const edits: DraftEdits = {};
      if (dirty.has("title")) edits.title = form.title.trim();
      if (dirty.has("description")) edits.description = form.description.trim();
      if (dirty.has("category")) edits.category = form.category.trim();
      if (dirty.has("currency")) edits.currency = form.currency.trim().toUpperCase();
      if (dirty.has("prices")) {
        const map: Record<string, number | null> = {};
        for (const [key, raw] of Object.entries(prices)) map[key] = priceToCents(raw);
        edits.prices = map;
      }
      // The response is the new truth, including a re-run validation — so the
      // problem list below updates from the save rather than from a guess about
      // what the save did.
      adopt(await updateImportedProduct(scope, connectionId, listingId, edits));
    } catch (error) {
      const failed = stateForError(error);
      setActionError(
        failed === "UNAUTHORIZED"
          ? "You're not signed in to this store any more."
          : "Those changes couldn't be saved. Nothing was changed."
      );
    } finally {
      setSaving(false);
    }
  }, [adopt, connectionId, dirty, form, listingId, prices, scope]);

  /**
   * Commit the merchant's variant choice, then re-read the parts the server
   * owns — *without* `adopt`.
   *
   * `adopt` resets `form`, `prices` and `dirty` from the response, which is
   * right after a save and wrong here: a merchant who typed a new title, did not
   * save it, and then chose a variant would watch their typing vanish. So the
   * supplier block, the variants and the verdict are taken from the server, and
   * the merchant's unsaved words are left alone.
   *
   * The verdict is re-read rather than assumed. Binding succeeding is not the
   * same claim as `SUPPLIER_VARIANT_UNBOUND` having cleared — only the evaluator
   * can make that one.
   */
  const confirmVariant = useCallback(async () => {
    const pid = draft?.supplier.providerProductId;
    if (!scope || !draft || !pendingVariantId || !pid) return;
    setBinding(true);
    setActionError(null);
    try {
      await bindDraftVariant(scope, connectionId, {
        listingId,
        providerProductId: pid,
        providerVariantId: pendingVariantId
      });
      const fresh = await getImportedProduct(scope, connectionId, listingId);
      setDraft((current) =>
        current
          ? { ...current, supplier: fresh.supplier, variants: fresh.variants, validation: fresh.validation }
          : fresh
      );
      setPendingVariantId(null);
    } catch (error) {
      const failed = stateForError(error);
      setActionError(
        failed === "UNAUTHORIZED"
          ? "You're not signed in to this store any more."
          : "That variant couldn't be set. Nothing was changed — try again, or pick a different one."
      );
    } finally {
      setBinding(false);
    }
  }, [connectionId, draft, listingId, pendingVariantId, scope]);

  const revalidate = useCallback(async () => {
    if (!scope || !draft) return;
    try {
      const validation = await validateImportedProduct(scope, connectionId, listingId);
      setDraft((current) => (current ? { ...current, validation } : current));
    } catch {
      // A failed dry-run tells the merchant nothing they can act on, and the
      // real publish below reports the same problems authoritatively.
    }
  }, [connectionId, draft, listingId, scope]);

  const publish = useCallback(async () => {
    if (!scope) return;
    setPublishing(true);
    setActionError(null);
    try {
      setPublished(await publishImportedProduct(scope, connectionId, listingId));
      await load("refresh");
    } catch (error) {
      const failed = stateForError(error);
      setActionError(
        failed === "UNAUTHORIZED"
          ? "You're not signed in to this store any more."
          : "This product couldn't be published. It's still a draft."
      );
      // The refusal usually carries reasons; re-reading them is the useful part.
      await revalidate();
    } finally {
      setPublishing(false);
    }
  }, [connectionId, listingId, load, revalidate, scope]);

  const hasEdits = dirty.size > 0;
  const problems = draft?.validation.problems || [];

  // Which supplier variant this listing sells, and whether that is still open.
  //
  // Only `DROPSHIP` sources have the question: a `STOCKED` listing is fulfilled
  // out of the merchant's own shelves, places no supplier order, and has nothing
  // to bind — so showing them a chooser would be inventing a decision.
  const isDropship = (draft?.supplier.fulfillmentMode || "").toUpperCase() === "DROPSHIP";
  const boundVariantId = draft?.supplier.providerVariantId || null;
  const bindableVariants = useMemo(
    () => (draft?.variants || []).filter((variant) => Boolean(variant.providerVariantId)),
    [draft]
  );
  const boundVariant = useMemo(
    () => bindableVariants.find((variant) => variant.providerVariantId === boundVariantId) || null,
    [bindableVariants, boundVariantId]
  );
  // Shown for an unbound dropship draft with something to choose between. Not
  // shown once bound: the binding is not editable from here, and a control that
  // cannot change anything is a control that lies about being one.
  const showVariantChooser = isDropship && !boundVariantId && bindableVariants.length > 0;
  const merchantOwned = useMemo(
    () => new Set((draft?.supplier.merchantOwnedFields || []).map((field) => field.toLowerCase())),
    [draft]
  );

  const stateBlock = stateOwnsScreen(state) ? (
    <DropshippingStateView
      state={state}
      subject="This product"
      onRetry={state === "UNAUTHORIZED" ? null : () => load("refresh")}
      onFixConnection={() => navigation.navigate("DropshippingSuppliers", { title: "Suppliers" })}
      reducedMotion={reducedMotion}
      skeletonRows={4}
      empty={{
        title: "This product isn't here any more.",
        body: "It may have been deleted from your store."
      }}
    />
  ) : null;

  return (
    <View style={styles.root}>
      <StoreHeader
        title={route.params.title || "Review product"}
        query=""
        onQueryChange={() => undefined}
        onSubmitSearch={() => undefined}
        onBack={() => navigation.goBack?.()}
        onNotifications={() => navigation.navigate("BusinessOsActivity")}
        unreadCount={0}
        searchPlaceholder="Review product"
        reducedMotion={reducedMotion}
      />

      <KeyboardAvoidingView style={styles.flex} behavior={Platform.OS === "ios" ? "padding" : undefined}>
        <ScrollView
          contentContainerStyle={[
            styles.content,
            { paddingBottom: Math.max(insets.bottom, 16) + BOTTOM_NAV_CONTENT_CLEARANCE }
          ]}
          keyboardShouldPersistTaps="handled"
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => load("refresh")} />}
        >
          {stateBlock}

          {!stateBlock && draft && form ? (
            <>
              <View style={styles.card}>
                <View style={styles.badgeRow}>
                  <ProviderBadge provider={draft.supplier.provider} />
                  <View style={styles.spacer} />
                  <Text style={draft.published ? styles.live : styles.draftBadge}>
                    {draft.published ? "In your store" : "Draft"}
                  </Text>
                </View>
                {draft.coverImageUrl ? (
                  <Image source={{ uri: draft.coverImageUrl }} style={styles.cover} resizeMode="cover" />
                ) : (
                  <View style={[styles.cover, styles.coverEmpty]}>
                    <Text style={styles.coverEmptyText}>No usable images from your supplier</Text>
                  </View>
                )}
                <Text style={styles.mediaCount}>
                  {draft.media.length === 0
                    ? "No images"
                    : `${formatters.count(draft.media.length)} ${draft.media.length === 1 ? "image" : "images"}`}
                </Text>
              </View>

              {problems.length > 0 ? (
                <View style={styles.card}>
                  <Text style={styles.cardTitle}>Before this can go live</Text>
                  {problems.map((problem) => {
                    // Still guarded at runtime even though the table is total
                    // over `PublishProblem`: the union is this app's copy of a
                    // list the server owns, and a server ahead of this build can
                    // name a code the union has never heard of. The typecheck
                    // stops the table drifting; this stops a blank line.
                    const copy = PROBLEM_COPY[String(problem).toUpperCase() as PublishProblem];
                    return (
                      <Text
                        key={String(problem)}
                        style={copy?.fixable === false ? styles.problemBlocked : styles.problem}
                      >
                        {copy ? copy.text : String(problem)}
                      </Text>
                    );
                  })}
                </View>
              ) : null}

              {/* The answer to SUPPLIER_VARIANT_UNBOUND. A dropship listing
                  sells one supplier variant, because the buyer's checkout has
                  no variant picker and charges one listing price. The import
                  screen pre-selects every in-stock variant, so this is the
                  ordinary state of a multi-variant import — not an edge case. */}
              {showVariantChooser ? (
                <View style={styles.card}>
                  <Text style={styles.cardTitle}>Which variant are you selling?</Text>
                  <Text style={styles.cardBody}>
                    A product sells one variant. When someone buys it, the order goes to your
                    supplier for the one you pick here — so this can't be changed afterwards.
                  </Text>

                  {bindableVariants.map((variant) => {
                    const id = variant.providerVariantId as string;
                    const chosen = pendingVariantId === id;
                    return (
                      <Pressable
                        key={id}
                        onPress={() => setPendingVariantId(id)}
                        disabled={binding}
                        style={[styles.bindOption, chosen ? styles.bindOptionChosen : null]}
                        accessibilityRole="radio"
                        accessibilityState={{ checked: chosen, disabled: binding }}
                        accessibilityLabel={`Sell ${variantLabel(variant)}`}
                      >
                        <Text style={styles.bindOptionName}>{variantLabel(variant)}</Text>
                        <View style={styles.variantMeta}>
                          <StockPill state={variant.stockState} quantity={variant.stockQuantity} />
                        </View>
                        <Text style={styles.variantCost}>
                          {costText(variant.costCents, variant.currency || draft.currency, formatters)
                            ? `Your cost ${costText(variant.costCents, variant.currency || draft.currency, formatters)}`
                            : `Cost ${NO_VALUE} your supplier didn't give one`}
                        </Text>
                      </Pressable>
                    );
                  })}

                  <Pressable
                    onPress={() => confirmVariant().catch(() => undefined)}
                    disabled={binding || !pendingVariantId}
                    style={[
                      styles.bindConfirm,
                      binding || !pendingVariantId ? styles.bindConfirmDisabled : null
                    ]}
                    accessibilityRole="button"
                    // No explicit `accessibilityState` here, on purpose.
                    // `Pressable` derives one from `disabled` and overrides
                    // whatever it is handed, so a second copy of this condition
                    // could only ever be the copy that loses — editing it would
                    // change the source text and nothing a merchant or a screen
                    // reader can observe. The mutation battery found exactly
                    // that. The variant rows above do pass one, because
                    // `checked` has no prop to be derived from.
                    accessibilityLabel="Confirm the variant this product sells"
                  >
                    <Text style={styles.bindConfirmText}>
                      {binding ? "Setting…" : "This is the one I'm selling"}
                    </Text>
                  </Pressable>
                </View>
              ) : null}

              {/* Bound, and therefore stated rather than offered. The merchant
                  needs to know which of several variants a buyer receives; a
                  chooser here would imply it were still open, and the server
                  answers `binding_conflict` to a second choice. */}
              {isDropship && boundVariant ? (
                <View style={styles.card}>
                  <Text style={styles.cardTitle}>What this product sells</Text>
                  <Text style={styles.cardBody}>
                    Orders go to your supplier for {variantLabel(boundVariant)}. The other variants
                    are what your supplier offers, not what this product sells.
                  </Text>
                </View>
              ) : null}

              <View style={styles.card}>
                <Text style={styles.cardTitle}>Your storefront details</Text>
                <Text style={styles.cardBody}>
                  These are yours. Once you edit one, your supplier's updates won't overwrite it.
                </Text>

                <Field
                  label="Title"
                  value={form.title}
                  owned={merchantOwned.has("title")}
                  onChange={(value) => editField("title", value)}
                />
                <Field
                  label="Category"
                  value={form.category}
                  owned={merchantOwned.has("category")}
                  onChange={(value) => editField("category", value)}
                />
                <Field
                  label="Description"
                  value={form.description}
                  owned={merchantOwned.has("description")}
                  multiline
                  onChange={(value) => editField("description", value)}
                />
                <Field
                  label="Currency"
                  value={form.currency}
                  owned={merchantOwned.has("currency")}
                  autoCapitalize="characters"
                  onChange={(value) => editField("currency", value)}
                />
              </View>

              <View style={styles.card}>
                <Text style={styles.cardTitle}>Variants and pricing</Text>
                <Text style={styles.cardBody}>
                  Cost and stock come from your supplier and can't be edited here. Price is yours.
                </Text>

                {draft.variants.map((variant) => {
                  const key = variantKey(variant);
                  return (
                    <View key={key || variantLabel(variant)} style={styles.variant}>
                      <Text style={styles.variantName}>{variantLabel(variant)}</Text>

                      <View style={styles.variantMeta}>
                        <StockPill state={variant.stockState} quantity={variant.stockQuantity} />
                        <MarginPill state={variant.marginState} percent={variant.marginPercent} />
                      </View>

                      {/* Supplier-owned, rendered as text. Unknown cost says so
                          rather than showing a zero the merchant would price
                          against. */}
                      <Text style={styles.variantCost}>
                        {costText(variant.costCents, variant.currency || draft.currency, formatters)
                          ? `Your cost ${costText(variant.costCents, variant.currency || draft.currency, formatters)}`
                          : `Cost ${NO_VALUE} your supplier didn't give one`}
                      </Text>

                      <View style={styles.priceRow}>
                        <Text style={styles.priceLabel}>Your price</Text>
                        <TextInput
                          style={styles.priceInput}
                          value={prices[key] ?? ""}
                          onChangeText={(value) => editPrice(key, value)}
                          keyboardType="decimal-pad"
                          placeholder="—"
                          placeholderTextColor={storeLight.text.muted}
                          accessibilityLabel={`Price for ${variantLabel(variant)}`}
                        />
                      </View>

                      {variant.proposedRetailCents !== null && !prices[key] ? (
                        <Pressable
                          onPress={() =>
                            editPrice(key, (variant.proposedRetailCents! / 100).toFixed(2))
                          }
                          accessibilityRole="button"
                          accessibilityLabel={`Use suggested price for ${variantLabel(variant)}`}
                        >
                          <Text style={styles.suggest}>
                            Use suggested{" "}
                            {costText(
                              variant.proposedRetailCents,
                              variant.currency || draft.currency,
                              formatters
                            )}
                          </Text>
                        </Pressable>
                      ) : null}
                    </View>
                  );
                })}
              </View>

              <View style={styles.actions}>
                <Pressable
                  style={[styles.secondary, hasEdits && !saving ? null : styles.disabled]}
                  onPress={() => void save()}
                  disabled={!hasEdits || saving}
                  accessibilityRole="button"
                  accessibilityState={{ disabled: !hasEdits || saving }}
                  accessibilityLabel="Save changes"
                >
                  <Text style={styles.secondaryText}>{saving ? "Saving…" : "Save changes"}</Text>
                </Pressable>

                <Pressable
                  style={[styles.primary, draft.validation.publishable && !publishing ? null : styles.disabled]}
                  onPress={() => void publish()}
                  disabled={!draft.validation.publishable || publishing}
                  accessibilityRole="button"
                  accessibilityState={{ disabled: !draft.validation.publishable || publishing }}
                  accessibilityLabel="Publish to your store"
                >
                  <Text style={styles.primaryText}>
                    {publishing ? "Publishing…" : draft.published ? "Published" : "Publish"}
                  </Text>
                </Pressable>
              </View>

              {hasEdits ? (
                <Text style={styles.note}>
                  You have unsaved changes. Save them before publishing.
                </Text>
              ) : null}

              {published ? (
                <View style={styles.card}>
                  <Text style={styles.cardTitle}>Published</Text>
                  <Text style={styles.cardBody}>
                    {published.awaitingModeration
                      ? "This product is published and waiting for review. It appears in the marketplace once it's approved."
                      : "This product is live in your store."}
                  </Text>
                  <Text style={styles.cardBody}>
                    {formatters.count(published.sellableVariants)}{" "}
                    {published.sellableVariants === 1 ? "variant is" : "variants are"} on sale.
                  </Text>
                </View>
              ) : null}

              {actionError ? (
                <StoreSectionError message={actionError} onRetry={null} reducedMotion={reducedMotion} />
              ) : null}

              {/* Stated on every draft, because this is where a merchant decides
                  whether the platform will actually ship for them. */}
              <Text style={styles.note}>
                Supplier fulfilment runs in sandbox. Publishing lists the product for buyers; no order
                is sent to your supplier yet.
              </Text>
            </>
          ) : null}
        </ScrollView>
      </KeyboardAvoidingView>
    </View>
  );
}

function Field({
  label,
  value,
  owned,
  multiline,
  autoCapitalize,
  onChange
}: {
  label: string;
  value: string;
  owned: boolean;
  multiline?: boolean;
  autoCapitalize?: "none" | "characters" | "words" | "sentences";
  onChange: (value: string) => void;
}) {
  return (
    <View style={styles.field}>
      <View style={styles.fieldHead}>
        <Text style={styles.fieldLabel}>{label}</Text>
        {/* Shown only once true. A badge on every field would make the state
            meaningless, and this state is the merchant's protection from a
            sync reverting their work. */}
        {owned ? <Text style={styles.ownedBadge}>Yours — sync won't change it</Text> : null}
      </View>
      <TextInput
        style={[styles.input, multiline ? styles.inputMultiline : null]}
        value={value}
        onChangeText={onChange}
        multiline={multiline}
        autoCapitalize={autoCapitalize}
        autoCorrect={autoCapitalize !== "characters"}
        accessibilityLabel={label}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: storeLight.bg.page },
  flex: { flex: 1 },
  content: { padding: storeLight.space.card, gap: storeLight.space.gutter },
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
  badgeRow: { flexDirection: "row", alignItems: "center", gap: 8 },
  spacer: { flex: 1 },
  draftBadge: { fontSize: 11, fontWeight: "700", color: storeLight.text.muted },
  live: { fontSize: 11, fontWeight: "700", color: storeLight.status.success },
  cover: { width: "100%", height: 200, borderRadius: storeLight.radius.control, backgroundColor: storeLight.bg.page },
  coverEmpty: {
    alignItems: "center",
    justifyContent: "center",
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: storeLight.border.hairline
  },
  coverEmptyText: { fontSize: 12, color: storeLight.text.muted },
  mediaCount: { fontSize: 11, color: storeLight.text.muted },
  problem: { fontSize: 13, color: storeLight.status.warning, fontWeight: "600", lineHeight: 18 },
  problemBlocked: { fontSize: 13, color: storeLight.text.muted, lineHeight: 18 },
  field: { gap: 4 },
  fieldHead: { flexDirection: "row", alignItems: "center", gap: 8 },
  fieldLabel: { fontSize: 12, fontWeight: "700", color: storeLight.text.primary },
  ownedBadge: { fontSize: 10, color: storeLight.text.muted },
  input: {
    minHeight: storeLight.size.tapTarget,
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderRadius: storeLight.radius.control,
    borderWidth: 1,
    borderColor: storeLight.border.secondaryButton,
    color: storeLight.text.primary,
    fontSize: 14
  },
  inputMultiline: { minHeight: 96, textAlignVertical: "top" },
  variant: {
    gap: 6,
    paddingTop: 10,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: storeLight.border.hairline
  },
  variantName: { fontSize: 14, fontWeight: "700", color: storeLight.text.primary },
  variantMeta: { flexDirection: "row", flexWrap: "wrap", gap: 6 },
  variantCost: { fontSize: 12, color: storeLight.text.muted },
  priceRow: { flexDirection: "row", alignItems: "center", gap: 10 },
  priceLabel: { fontSize: 12, fontWeight: "600", color: storeLight.text.primary },
  priceInput: {
    flex: 1,
    minHeight: storeLight.size.tapTarget,
    paddingHorizontal: 12,
    borderRadius: storeLight.radius.control,
    borderWidth: 1,
    borderColor: storeLight.border.secondaryButton,
    color: storeLight.text.primary,
    fontSize: 15,
    fontWeight: "700"
  },
  suggest: { fontSize: 12, fontWeight: "700", color: storeLight.text.link },
  // A bordered box rather than a hairline-separated row, unlike `variant`: this
  // is a choice being made, and a tappable option needs to look tappable and to
  // meet the tap-target floor.
  bindOption: {
    gap: 6,
    padding: 12,
    minHeight: storeLight.size.tapTarget,
    borderRadius: storeLight.radius.card,
    borderWidth: 1,
    borderColor: storeLight.border.hairline
  },
  bindOptionChosen: {
    borderColor: storeLight.cta.from,
    borderWidth: 2
  },
  bindOptionName: { fontSize: 14, fontWeight: "700", color: storeLight.text.primary },
  bindConfirm: {
    minHeight: storeLight.size.tapTarget,
    alignItems: "center",
    justifyContent: "center",
    borderRadius: storeLight.radius.pill,
    backgroundColor: storeLight.cta.from
  },
  bindConfirmDisabled: { opacity: 0.5 },
  bindConfirmText: { fontSize: 13, fontWeight: "800", color: storeLight.cta.text },
  actions: { flexDirection: "row", gap: 10 },
  disabled: { opacity: 0.5 },
  secondary: {
    flex: 1,
    minHeight: storeLight.size.tapTarget,
    alignItems: "center",
    justifyContent: "center",
    borderRadius: storeLight.radius.pill,
    borderWidth: 1,
    borderColor: storeLight.border.secondaryButton
  },
  secondaryText: { fontSize: 13, fontWeight: "700", color: storeLight.text.primary },
  primary: {
    flex: 1,
    minHeight: storeLight.size.tapTarget,
    alignItems: "center",
    justifyContent: "center",
    borderRadius: storeLight.radius.pill,
    backgroundColor: storeLight.cta.from
  },
  primaryText: { fontSize: 13, fontWeight: "800", color: storeLight.cta.text },
  note: { fontSize: 12, color: storeLight.text.muted, lineHeight: 17 }
});
