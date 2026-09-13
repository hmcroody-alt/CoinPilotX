/**
 * The seller's Store dashboard — the screen behind card #2 of the Business
 * "Sections" grid.
 *
 * This is a **management** surface, not the buyer storefront. Everything on it
 * answers one of two questions: is my store working right now, and what needs
 * me. That is why the KPI grid is above the listings and why the attention
 * banner appears above both when it appears at all.
 *
 * Structural decisions worth stating:
 *
 * * **`SellerStoreScreen` is untouched.** The `SellerStore` route still points
 *   there for every other mode, including `mode: "orders"` which the Orders
 *   card uses; only `mode: "dashboard"` reaches this screen. Deep links,
 *   navigation params and the listing editor all keep working exactly as they
 *   did.
 * * **The list is virtualized.** The section shows a preview of six, but the
 *   "See all" state renders the seller's full catalogue through a `FlatList`
 *   rather than a mapped array.
 * * **Nothing here formats a number itself.** Currency, counts, percentages and
 *   relative times all go through `useFormatters`, so a euro seller sees euros
 *   and a Spanish locale sees Spanish separators without this file knowing.
 *
 * Figures the reference design shows and this app has no source for — views,
 * seller rating, on-time dispatch, per-listing stars — are absent rather than
 * invented. `STORE_MOCK_DATA_GAPS` in `api/storeDashboard` lists each one and
 * the backend work it needs.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { FlatList, Pressable, RefreshControl, StyleSheet, Text, View, Animated } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import {
  deriveAttention,
  deriveKpis,
  deriveRows,
  deriveStatus,
  deriveTabs,
  filterRows,
  loadStoreDashboard,
  snapshotFrom,
  storeReadiness,
  storeReadinessEnabled,
  type StoreAttention,
  type StoreListingRow as StoreListingRowData,
  type StoreLoadResult,
  type StoreSetupActionKey,
  type StoreSetupStep,
  type StoreTabKey
} from "../api/storeDashboard";
import {
  batchMarketplaceSellerListings,
  previewMarketplaceSellerBatch,
  type MarketplacePricingRule
} from "../api/marketplace";
import { PulseApiError } from "../api/pulseApi";
import {
  StoreAttentionBanner,
  StoreBulkBar,
  StoreBulkSheet,
  StoreEmptyListings,
  StoreHeader,
  StoreKpiCard,
  StoreKpiSkeleton,
  StoreListingRow,
  StoreOfflineNote,
  StoreQuickLinkGrid,
  StoreRowSkeleton,
  StoreSectionError,
  StoreSelectionBar,
  StoreSetupChecklist,
  StoreSparkline,
  StoreStatusStrip,
  StoreTabBar,
  type StoreBulkSheetPhase
} from "../components/store";
import {
  bulkActionLabel,
  isPrecomputed,
  partition,
  reconcile,
  selectAllLabel,
  selectAllState,
  selectedRows,
  selectionSummary,
  toggle,
  toggleAll,
  type StoreBulkAction,
  type StoreSelection
} from "../marketplace/storeSelection";
import {
  beginAttempt,
  categoryPayload,
  pricePayload,
  idsToSend,
  isSameAttempt,
  outcomeOf,
  reviewFromPartition,
  reviewFromPreview,
  type StoreBulkAttempt,
  type StoreBulkOutcome,
  type StoreBulkPayload,
  type StoreBulkReview
} from "../marketplace/storeBulkRun";
import {
  EMPTY_PRICING_DRAFT,
  parsePricingRule,
  type StorePricingRuleDraft
} from "../marketplace/storeBulkPricing";
import {
  EMPTY_CATEGORY_DRAFT,
  categoriesInUse,
  parseCategoryTarget,
  type StoreCategoryDraft
} from "../marketplace/storeBulkCategory";
import { registerSyncInvalidation } from "../core/eventSync";
import { refreshUnreadCounts, useBellCount } from "../core/unreadCounts";
import { useFormatters } from "../i18n/hooks";
import { BOTTOM_NAV_CONTENT_CLEARANCE } from "../navigation/BottomNavVisibility";
import { RootStackParamList } from "../navigation/types";
import { storeLight } from "../theme/storeLight";
import { useLogiNexusReducedMotion } from "../theme/logiNexusMotion";
import { useStoreAmbient, useStoreEntrance, STORE_AMBIENT, STORE_STAGGER_MS } from "../theme/storeMotion";

/** How many listings the section previews before "See all". */
const PREVIEW_COUNT = 6;

/**
 * Banner copy per attention kind, as an exhaustive map.
 *
 * It was a two-way ternary on `kind === "out_of_stock"`, which meant every kind
 * that was not out-of-stock rendered as "running low" — so adding
 * `unknown_stock` would have told sellers to restock listings whose shelves are
 * probably full. A `Record` keyed by the union makes the compiler demand an
 * entry for each new kind instead of quietly picking the else branch.
 *
 * The headline fragment completes "N listings are …".
 */
const ATTENTION_COPY: Record<StoreAttention["kind"], { headline: string; detail: string }> = {
  out_of_stock: {
    headline: "out of stock",
    detail: "Buyers can't order these until you restock them."
  },
  unknown_stock: {
    headline: "missing a stock count",
    detail: "Buyers can't order these until you say how many you have."
  },
  low_stock: {
    headline: "running low",
    detail: "Restock before they sell out and drop off the storefront."
  }
};

/**
 * Every id the seller reviewed, changing or not.
 *
 * Read off the frozen review rather than stored beside it, so the list that goes
 * on the wire cannot drift from the list the sheet drew. Rows that will not
 * change are included deliberately — see `idsToSend` in
 * `marketplace/storeBulkRun`: the review is a snapshot, and the server re-checks
 * every row at write time.
 */
function reviewedIds(review: StoreBulkReview): number[] {
  return [...review.changing, ...review.staying].map((line) => line.id);
}

/**
 * The sentence on the error face.
 *
 * Only a `PulseApiError` carries prose worth showing: its message is the
 * server's own, which is how `BATCH_TOO_LARGE` reaches the seller as "Select up
 * to 200 listings at a time." without this screen keeping its own copy of the
 * limit. Anything else is a transport failure whose message is written for a
 * developer, so it gets the generic line instead.
 */
function bulkErrorMessage(error: unknown): string | null {
  if (error instanceof PulseApiError && error.message) return error.message;
  return null;
}

/**
 * Entrance slots, in the order the spec choreographs them. Named so a section
 * cannot silently animate out of order when one is added.
 */
const SLOT = {
  header: 0,
  status: 1,
  /**
   * The setup checklist sits directly under the strip, because it is the "why"
   * for the sentence the strip just made. It only renders behind the readiness
   * flag; when the flag is off this slot is simply unused, which costs nothing
   * because `SECTION_COUNT` is derived rather than typed.
   */
  setup: 2,
  kpis: 3,
  banner: 4,
  tabs: 5,
  list: 6,
  links: 7,
  ctas: 8
} as const;
const SECTION_COUNT = Object.keys(SLOT).length;

type Props = {
  route?: { params?: RootStackParamList["SellerStore"] };
  navigation: { navigate: (...args: any[]) => void; goBack?: () => void };
};

export function StoreDashboardScreen({ route, navigation }: Props) {
  const formatters = useFormatters();
  const reducedMotion = useLogiNexusReducedMotion();
  const insets = useSafeAreaInsets();
  const entrance = useStoreEntrance(SECTION_COUNT, reducedMotion);

  const [result, setResult] = useState<StoreLoadResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [tab, setTab] = useState<StoreTabKey>("all");
  const [query, setQuery] = useState("");
  const [expanded, setExpanded] = useState(false);

  /**
   * Selection mode — §16–§20.
   *
   * `null` means not selecting. An *empty set* means selecting with nothing
   * picked yet, which is a real state: the bar is up, the rows are checkboxes,
   * and the seller has not tapped one. Collapsing the two into "is the set
   * empty" would close selection mode under the seller the moment they
   * deselected their last row.
   */
  const [selection, setSelection] = useState<StoreSelection | null>(null);
  /** Which bulk action the row washes and the docked CTA are previewing. */
  const [pendingAction, setPendingAction] = useState<StoreBulkAction>("publish");
  /**
   * The open bulk sheet, or `null` for no sheet — §23, §31, §33.
   *
   * One object rather than four pieces of state, because "a sheet is open" and
   * "this is the work it is about" are the same fact. Split apart, there is a
   * render where the phase says `running` and the attempt is still null.
   *
   * `action` and `review` are **frozen when the sheet opens**. The list behind
   * it keeps reloading — the batch itself triggers a reload — and `reconcile` can
   * drop rows out of the selection while the seller is reading the confirm face.
   * Re-deriving the review list from live state would mean the seller taps a
   * button describing one batch and sends another.
   */
  const [bulk, setBulk] = useState<{
    phase: StoreBulkSheetPhase;
    action: StoreBulkAction;
    /** `null` only on the `rule` face, before anything has been previewed. */
    review: StoreBulkReview | null;
    /**
     * The payload the review was computed under, carried so the commit sends the
     * same one. Reading it back off `priceDraft` (or `categoryDraft`) at confirm
     * time was the alternative and is the bug: the draft is live, the review is
     * frozen, and a seller who edits the field while the confirm face is up would
     * apply a rule whose prices — or an aisle whose moves — they never saw.
     *
     * One field for both payload actions rather than one per action, matching
     * `StoreBulkAttempt.payload` and the server's single `plans` argument.
     */
    payload: StoreBulkPayload | null;
    outcome: StoreBulkOutcome | null;
    error: string | null;
  } | null>(null);
  /**
   * The pricing rule being typed. Lives on the screen rather than in `bulk` so
   * it survives closing the sheet — a seller who cancels to check a product and
   * comes back finds their number still there.
   */
  const [priceDraft, setPriceDraft] = useState<StorePricingRuleDraft>(EMPTY_PRICING_DRAFT);
  /**
   * The category being chosen. Lives beside `priceDraft` and for the same reason:
   * it survives closing the sheet, so a seller who cancels to check which aisle a
   * product is in comes back to what they had picked.
   */
  const [categoryDraft, setCategoryDraft] = useState<StoreCategoryDraft>(EMPTY_CATEGORY_DRAFT);
  /**
   * The attempt the open sheet is sending — a ref, not state, and that is the
   * whole of §23 on the client.
   *
   * Held in state it would be written by `setBulk` and read back on the next
   * render, so two presses inside one frame — which is what a double-tap is,
   * before the disabled state has flushed — would both read `null` and both mint
   * a key. Two keys is two batches: everything published twice. A ref is written
   * and read in the same tick, so the second press finds the first press's key.
   *
   * Cleared when a sheet opens, because a new sheet is new work: reusing a key
   * across sheets would have the server replay its old answer to a question the
   * seller is asking again.
   */
  const attemptRef = useRef<StoreBulkAttempt | null>(null);

  // The header bell reads the ONE shared unread store — the same number every
  // seller header and the Activity feed show. Pull the authoritative count on
  // mount; the eventSync wiring (initUnreadCountSync) keeps it fresh after that.
  const bellCount = useBellCount();
  useEffect(() => {
    void refreshUnreadCounts();
  }, []);

  const load = useCallback(async (mode: "initial" | "refresh" = "initial") => {
    if (mode === "refresh") setRefreshing(true);
    else setLoading(true);
    try {
      setResult(await loadStoreDashboard());
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    load().catch(() => undefined);
  }, [load]);

  // Same invalidation channels the existing Store screen listens on, so a
  // listing edited elsewhere refreshes here too.
  useEffect(() => {
    const refresh = () => load("refresh");
    const unregister = [
      registerSyncInvalidation("seller_inventory", refresh),
      registerSyncInvalidation("marketplace", refresh),
      registerSyncInvalidation("orders", refresh)
    ];
    return () => unregister.forEach((fn) => fn());
  }, [load]);

  const snapshot = useMemo(() => (result ? snapshotFrom(result) : { listings: [], orders: [] }), [result]);
  const kpis = useMemo(() => deriveKpis(snapshot), [snapshot]);
  const allRows = useMemo(() => deriveRows(snapshot), [snapshot]);
  /**
   * Drop selected ids the reload no longer carries — the rule
   * `marketplace/storeSelection` asks every caller for, wired to the one thing
   * that changes on every load.
   *
   * `allRows` is memoised on the snapshot, so this fires exactly when a payload
   * lands and not on unrelated re-renders. Without it a listing deleted on
   * another device stays in the set, and the bulk action posts an id that no
   * longer exists — a failure reported against a row the seller cannot see, on
   * a screen that has already refreshed past it.
   *
   * `reconcile` returns the *same reference* when nothing changed, so the
   * `setSelection` below bails out of a re-render on every poll rather than
   * re-partitioning the whole list each time the dashboard refreshes.
   */
  useEffect(() => {
    setSelection((current) => (current === null ? null : reconcile(current, allRows)));
  }, [allRows]);

  const tabs = useMemo(() => deriveTabs(allRows), [allRows]);
  const attention = useMemo(() => deriveAttention(allRows), [allRows]);
  const status = useMemo(() => deriveStatus(allRows), [allRows]);

  /**
   * Where the store actually stands, read from the listings it already loaded.
   *
   * The flag is read once per render rather than at module load so a test can
   * turn it on without re-importing the screen, matching every other flag in
   * this app. With it off, `status` above still drives the strip and this screen
   * behaves exactly as the shipped build does.
   */
  const readinessOn = storeReadinessEnabled();
  const readiness = useMemo(
    () => storeReadiness({ listings: snapshot.listings, rows: allRows }),
    [snapshot.listings, allRows]
  );

  /**
   * Search filters the seller's own catalogue in place rather than navigating,
   * so the KPIs above stay visible while they look. Title and price label are
   * the two fields a seller actually types — SKU has no field in this API.
   */
  const searched = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const scoped = filterRows(allRows, tab);
    if (!needle) return scoped;
    return scoped.filter(
      (row) =>
        row.title.toLowerCase().includes(needle) ||
        row.priceLabel.toLowerCase().includes(needle) ||
        String(row.id).includes(needle)
    );
  }, [allRows, query, tab]);

  const visible = expanded ? searched : searched.slice(0, PREVIEW_COUNT);

  /* -------------------------------------------------------------- *
   * Selection — §16–§20
   * -------------------------------------------------------------- */

  const selecting = selection !== null;

  /**
   * What the pending action would do to the current selection — computed once.
   *
   * The row washes, the docked CTA's count and the confirm sheet's blocked list
   * are three renderings of this one value. Deriving it three times is how the
   * button comes to say "4 blocked" while five rows wear the wash.
   *
   * It partitions `allRows`, not `visible`, and the difference is now load
   * bearing rather than theoretical: a selected row scrolled off by a tab filter
   * is still going into the batch, so partitioning `visible` would drop it from
   * the count on the button while leaving it in the request. The list stays
   * `allRows` because the selection does.
   *
   * `null` for `price`, and that is the point of `isPrecomputed`. There is no
   * local verdict for a reprice — what blocks one depends on the rule the seller
   * has not typed yet — so there is nothing to wash the rows with and no count to
   * put on the button. Partitioning anyway would have marked every row in the
   * store "No readiness check yet" and greyed the whole feature out.
   */
  const partitioned = useMemo(
    () =>
      selection && isPrecomputed(pendingAction)
        ? partition(selectedRows(selection, allRows), pendingAction)
        : null,
    [selection, allRows, pendingAction]
  );

  /** How many rows the payload face says it covers. */
  const selectedCount = useMemo(
    () => (selection ? selectedRows(selection, allRows).length : 0),
    [selection, allRows]
  );

  /**
   * Aisles already in use, for the category face's suggestion chips.
   *
   * From `allRows` rather than the selection — see the note at the call site.
   * Recomputed with the list, so a seller who just created an aisle in the
   * single-listing editor sees it here after the next reload without this screen
   * caching a taxonomy of its own.
   */
  const categorySuggestions = useMemo(() => categoriesInUse(allRows), [allRows]);

  /**
   * Where the selected products are filed right now, for the "already there"
   * count on the category face.
   *
   * Local and therefore a hint, not a verdict: the server decides per row when
   * the preview runs. Its whole job is to stop the next face being a surprise.
   */
  const selectedFilings = useMemo(
    () =>
      selection
        ? selectedRows(selection, allRows).map((row) => ({
            category: row.category,
            subcategory: row.subcategory
          }))
        : [],
    [selection, allRows]
  );

  /** The same partition, keyed by id, for the row the list is drawing. */
  const blockedById = useMemo(
    () =>
      partitioned
        ? new Map(partitioned.blocked.map((entry) => [entry.row.id, entry.reason]))
        : null,
    [partitioned]
  );

  const enterSelection = useCallback((id: number) => {
    // Long-press enters the mode *and* picks the row pressed. Entering with
    // nothing selected would make the gesture cost two taps to do the obvious
    // thing, and the row under the seller's finger is unambiguously the one
    // they meant.
    setSelection((current) => (current === null ? new Set([id]) : toggle(current, id)));
  }, []);

  const exitSelection = useCallback(() => setSelection(null), []);

  const toggleRow = useCallback((id: number) => {
    setSelection((current) => (current === null ? current : toggle(current, id)));
  }, []);

  const onToggleAll = useCallback(() => {
    setSelection((current) => (current === null ? current : toggleAll(current, visible)));
  }, [visible]);

  /* -------------------------------------------------------------- *
   * Bulk actions — §19, §23, §31, §33, §34
   * -------------------------------------------------------------- */

  /**
   * Send the attempt, then read the store back from the server.
   *
   * §31's chain in one function: TAP → REQUEST → BACKEND CHANGE → READ BACK →
   * UI UPDATE. Nothing here patches a row locally. The result face is built from
   * the server's `results`, and the list underneath is reloaded from the server
   * before the seller sees it, so the two cannot disagree about what happened.
   *
   * The reload is awaited rather than fired off, because the seller's next tap
   * is Done and the list they land on has to be the one the batch produced. Its
   * failure is swallowed on purpose: a refresh that could not complete does not
   * turn a batch that did into an error.
   */
  const runBulk = useCallback(
    async (action: StoreBulkAction, attempt: StoreBulkAttempt) => {
      setBulk((current) => (current ? { ...current, phase: "running", error: null } : current));
      try {
        const response = await batchMarketplaceSellerListings({
          action,
          listingIds: idsToSend(attempt),
          idempotencyKey: attempt.idempotencyKey,
          // Off the attempt, not off the live draft: the attempt is what the
          // seller agreed to, and it is the thing the idempotency key identifies.
          pricingRule: attempt.payload?.kind === "price" ? attempt.payload.rule : undefined,
          categoryTarget: attempt.payload?.kind === "category" ? attempt.payload.target : undefined
        });
        const outcome = outcomeOf(action, response);
        await load("refresh").catch(() => undefined);
        setBulk((current) =>
          current ? { ...current, phase: "result", outcome, error: null } : current
        );
      } catch (error) {
        setBulk((current) =>
          current ? { ...current, phase: "error", error: bulkErrorMessage(error) } : current
        );
      }
    },
    [load]
  );

  /**
   * The confirm button, and the retry button, are the same handler.
   *
   * That is what makes §23 hold. The key belongs to the *attempt* — one action
   * on one id set — so a second tap while the first is in flight, or a Try again
   * after a timeout, sends the key the server has already seen and gets the
   * original answer replayed instead of publishing everything twice.
   *
   * `isSameAttempt` decides whether the key in hand still describes this work. It
   * compares the id set as a set, so it is not fooled by ordering, and it is the
   * reason a mismatched key is replaced rather than reused: sending a key that
   * belongs to a different id set would have the server answer a question nobody
   * asked.
   */
  const confirmBulk = useCallback(() => {
    // No review means the rule face, where there is nothing to confirm yet. The
    // sheet does not render a confirm button there; this is the guard that makes
    // that a fact rather than a layout detail.
    if (!bulk?.review) return;
    const ids = reviewedIds(bulk.review);
    const held = attemptRef.current;
    const attempt =
      held && isSameAttempt(held, bulk.action, ids, bulk.payload)
        ? held
        : beginAttempt(bulk.action, ids, bulk.payload);
    attemptRef.current = attempt;
    void runBulk(bulk.action, attempt);
  }, [bulk, runBulk]);

  /**
   * The dry run — §34.
   *
   * Asks the server what the rule comes to and shows the answer. Writes nothing:
   * the endpoint returns above its own claim, so this cannot spend the key it
   * sends or reach the write loop. Its key is separate from the commit's for a
   * reason that is not symmetry — a preview leaves a key spendable, so reusing
   * one would work, but reusing it *across a rule change* would hand the commit
   * a key the server had already answered under the old rule.
   *
   * Failure lands on the error face rather than silently reverting to the rule
   * field, because "I tapped Preview and the sheet went back to normal" is
   * indistinguishable from a dropped tap.
   */
  const previewBulk = useCallback(async () => {
    if (!selection) return;
    const action = bulk?.action ?? pendingAction;
    // The payload is read off whichever draft this action owns, once, here — and
    // then carried on the review rather than re-read at confirm time. A preview
    // that asked about one payload and a commit that sent another is §34 with the
    // guarantee removed.
    let payload: StoreBulkPayload | null = null;
    if (action === "category") {
      const parsed = parseCategoryTarget(categoryDraft);
      if (!parsed.target) return;
      payload = categoryPayload(parsed.target);
    } else {
      const parsed = parsePricingRule(priceDraft);
      if (!parsed.rule) return;
      payload = pricePayload(parsed.rule);
    }
    const ids = selectedRows(selection, allRows).map((row) => row.id);
    if (ids.length === 0) return;
    setBulk((current) => (current ? { ...current, phase: "previewing", error: null } : current));
    try {
      const response = await previewMarketplaceSellerBatch({
        action,
        listingIds: ids,
        idempotencyKey: `preview-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`,
        pricingRule: payload.kind === "price" ? payload.rule : undefined,
        categoryTarget: payload.kind === "category" ? payload.target : undefined
      });
      const review = reviewFromPreview(
        response,
        action,
        (listingId) => allRows.find((row) => row.id === listingId)?.title || `Listing ${listingId}`
      );
      // A fresh review is new work, so the held key is dropped. Keeping it would
      // let a second payload be committed under the first payload's key, and a
      // spent key is answered by replay rather than by looking at the payload.
      attemptRef.current = null;
      setBulk((current) =>
        current ? { ...current, phase: "confirm", review, payload, error: null } : current
      );
    } catch (error) {
      setBulk((current) =>
        current ? { ...current, phase: "error", error: bulkErrorMessage(error) } : current
      );
    }
  }, [selection, allRows, priceDraft, categoryDraft, bulk?.action, pendingAction]);

  /**
   * Try again, on whichever request actually failed.
   *
   * The error face is shared, so one handler has to serve both, and pointing it
   * straight at `confirmBulk` would have been the dangerous version: a seller
   * whose *dry run* timed out would tap "Try again" and get a write. The review
   * is the discriminator — there is one only once a preview has come back — and
   * it is the honest one, because it is the same fact the confirm button is
   * gated on.
   */
  const retryBulk = useCallback(() => {
    if (bulk?.review) {
      confirmBulk();
      return;
    }
    void previewBulk();
  }, [bulk?.review, confirmBulk, previewBulk]);

  /**
   * Back to the rule, from the review or from a refusal.
   *
   * The draft survives — that is the whole value of the button, since a seller
   * going from 20% to 25% is changing one character — but the review and the
   * frozen rule do not. Keeping the review would leave the old rule's prices on
   * screen while the seller edits the number that produced them, which is §34
   * inverted: a preview that no longer previews the thing about to happen.
   * Dropping it also puts `retryBulk` back on the dry run, because there is once
   * again nothing reviewed to commit.
   *
   * The held idempotency key is deliberately NOT cleared here. If the seller got
   * to this button from a failed commit, that key may have been claimed, and
   * `previewBulk` is the place that drops it — after a new review exists, so the
   * two facts move together. Clearing it here as well would be harmless and
   * would also hide which step is responsible.
   */
  const changeRule = useCallback(() => {
    setBulk((current) =>
      current ? { ...current, phase: "rule", review: null, payload: null, error: null } : current
    );
  }, []);

  const openBulkSheet = useCallback(() => {
    if (!selection) return;
    attemptRef.current = null;
    if (!isPrecomputed(pendingAction)) {
      // A reprice opens on the rule face with no review, because there is nothing
      // to review until the server has been asked.
      setBulk({ phase: "rule", action: pendingAction, review: null, payload: null, outcome: null, error: null });
      return;
    }
    if (!partitioned) return;
    setBulk({
      phase: "confirm",
      action: pendingAction,
      review: reviewFromPartition(partitioned, pendingAction),
      payload: null,
      outcome: null,
      error: null
    });
  }, [selection, partitioned, pendingAction]);

  /**
   * Closing the sheet. After a result it also ends selection mode.
   *
   * The selection described rows in the state they were in before the write. Now
   * that they have moved, carrying it forward would leave the seller holding a
   * set whose CTA reads "Nothing to publish" — an answer about work that is
   * already done. The rows that did not move are still in the list, still
   * flagged, and are fixed one at a time from there.
   */
  const closeBulkSheet = useCallback(() => {
    const finished = bulk?.phase === "result";
    setBulk(null);
    if (finished) setSelection(null);
  }, [bulk?.phase]);

  // Note: selection is deliberately NOT cleared when `tab` or `query` changes.
  // Gathering rows across tabs is the workflow, and `selectionSummary` names the
  // part that scrolled off screen so nothing is selected invisibly.

  const sellerName = String(snapshot.listings[0]?.seller_name || "Your store");
  const listingsFailed = result?.listings.status === "error";
  const ordersFailed = result?.orders.status === "error";

  /* -------------------------------------------------------------- *
   * Navigation
   * -------------------------------------------------------------- */

  /**
   * The listing editor is a panel inside `SellerStoreScreen`, and this screen
   * takes over `mode: "dashboard"` — so Edit routes to `mode: "create"`, which
   * renders the same `listings` panel with the same editor. The editor is not
   * reimplemented here and is not orphaned by the swap. `listingId` is what
   * makes it land on the row the seller tapped instead of an empty panel.
   */
  const openListing = useCallback(
    (row: StoreListingRowData) => {
      navigation.navigate("SellerStore", { mode: "create", title: row.title, listingId: row.id });
    },
    [navigation]
  );

  /** The buyer-facing marketplace tab — the real "preview as buyer" surface. */
  const openBuyerView = useCallback(() => {
    navigation.navigate("Tabs", { screen: "Marketplace" });
  }, [navigation]);

  /**
   * The four setup actions, each mapped to something that already exists.
   *
   * Every key resolves to a destination this app ships today — the create
   * gateway, a tab of this screen's own list, or the buyer view. The ladder
   * deliberately has no fifth action, because a fifth would have had nowhere to
   * go, and a checklist button that lands nowhere is worse than no button.
   */
  const runSetupAction = useCallback(
    (key: StoreSetupActionKey) => {
      switch (key) {
        case "add_listing":
          navigation.navigate("MarketplaceCreateGateway", { title: "Create Listing" });
          return;
        case "open_drafts":
          setTab("drafts");
          setExpanded(true);
          return;
        case "open_out_of_stock":
          setTab("out");
          setExpanded(true);
          return;
        default:
          openBuyerView();
      }
    },
    [navigation, openBuyerView]
  );

  const onSetupStep = useCallback(
    (step: StoreSetupStep) => {
      if (step.action) runSetupAction(step.action.key);
    },
    [runSetupAction]
  );

  /* -------------------------------------------------------------- *
   * Formatted values
   * -------------------------------------------------------------- */

  const salesText = formatters.currency(kpis.salesTodayMinor / 100, { currency: kpis.currency });
  const salesTrend =
    kpis.salesTrend == null
      ? null
      : {
          direction: (kpis.salesTrend >= 0 ? "up" : "down") as "up" | "down",
          label: formatters.percent(Math.abs(kpis.salesTrend))
        };

  const lowCount = tabs.find((entry) => entry.key === "low")?.count ?? 0;
  const outCount = tabs.find((entry) => entry.key === "out")?.count ?? 0;

  /* -------------------------------------------------------------- *
   * Sections
   * -------------------------------------------------------------- */

  const kpiGrid = (
    <View style={styles.kpiGrid}>
      {loading ? (
        <>
          <View style={styles.kpiRow}>
            <StoreKpiSkeleton reducedMotion={reducedMotion} />
            <StoreKpiSkeleton reducedMotion={reducedMotion} />
          </View>
          <View style={styles.kpiRow}>
            <StoreKpiSkeleton reducedMotion={reducedMotion} />
            <StoreKpiSkeleton reducedMotion={reducedMotion} />
          </View>
        </>
      ) : ordersFailed ? (
        <StoreSectionError
          message="Sales and orders didn't load."
          onRetry={() => load("refresh")}
          reducedMotion={reducedMotion}
        />
      ) : (
        <>
          <View style={styles.kpiRow}>
            <StoreKpiCard
              label="Today's sales"
              value={salesText}
              trend={salesTrend}
              visual={
                <StoreSparkline values={kpis.sparkline} reducedMotion={reducedMotion} />
              }
              onPress={() => navigation.navigate("BusinessOsInsights", { title: "Store reports" })}
              destinationHint="reports"
              reducedMotion={reducedMotion}
              delay={SLOT.kpis * STORE_STAGGER_MS}
            />
            <StoreKpiCard
              label="Open orders"
              value={formatters.count(kpis.openOrders)}
              // MOCK-DATA: `shippingToday` needs order.ship_by, so the
              // "N ship today" caption is absent rather than guessed.
              caption={kpis.shippingToday == null ? null : `${kpis.shippingToday} ship today`}
              onPress={() => navigation.navigate("SellerStore", { mode: "orders" })}
              destinationHint="your orders"
              reducedMotion={reducedMotion}
              delay={SLOT.kpis * STORE_STAGGER_MS}
            />
          </View>
          <View style={styles.kpiRow}>
            <StoreKpiCard
              label="Listings live"
              value={formatters.count(
                allRows.filter((row) => row.health === "in_stock" || row.health === "low_stock").length
              )}
              caption={outCount > 0 ? `${outCount} not buyable` : null}
              onPress={() => {
                setTab("all");
                setExpanded(true);
              }}
              destinationHint="all listings"
              reducedMotion={reducedMotion}
              delay={SLOT.kpis * STORE_STAGGER_MS}
            />
            <StoreKpiCard
              label="Sold · 7 days"
              value={formatters.count(allRows.reduce((sum, row) => sum + row.unitsSold7d, 0))}
              onPress={() => navigation.navigate("BusinessOsInsights", { title: "Store reports" })}
              destinationHint="reports"
              reducedMotion={reducedMotion}
              delay={SLOT.kpis * STORE_STAGGER_MS}
            />
          </View>
        </>
      )}
    </View>
  );

  /**
   * This screen's hand-composed two-tile rows were the pattern the other
   * surfaces got wrong, so the pattern moved into `StoreQuickLinkGrid` and this
   * screen now consumes it like everyone else. Behaviour is unchanged — three
   * rows of two, in the same order — but the row count is derived rather than
   * typed, which is what stops the next screen from typing four.
   */
  const quickLinks = (
    <View style={styles.linkGrid}>
      <StoreQuickLinkGrid
        reducedMotion={reducedMotion}
        items={[
          {
            icon: "cube-outline",
            label: "Inventory",
            subtitle: loading
              ? "Checking stock…"
              : allRows.length === 0
                ? // "0 items · all stocked" claimed a stocked shelf that does not
                  // exist. An empty catalogue has no stock state at all.
                  "No inventory yet"
                : lowCount > 0
                  ? `${allRows.length} items · ${lowCount} low`
                  : `${allRows.length} items · all stocked`,
            onPress: () => {
              setTab(lowCount > 0 ? "low" : "all");
              setExpanded(true);
            },
            reducedMotion
          },
          {
            icon: "pricetags-outline",
            // The tile counts distinct listing categories, so it is named for
            // what it counts. It becomes "Collections" only when a real
            // collections feature exists to back the word (mission Phase 5) —
            // relabelling the same category count would be the dishonest fix.
            label: "Categories",
            subtitle: (() => {
              const count = new Set(
                snapshot.listings.map((item) => item.category).filter(Boolean)
              ).size;
              return count === 0 ? "No categories yet" : `${count} categories`;
            })(),
            onPress: () => {
              setTab("all");
              setExpanded(true);
            },
            reducedMotion
          },
          {
            icon: "bar-chart-outline",
            label: "Reports",
            subtitle: "Sales, orders and trends",
            onPress: () => navigation.navigate("BusinessOsInsights", { title: "Store reports" }),
            reducedMotion
          },
          {
            icon: "storefront-outline",
            label: "Storefront",
            subtitle: "See your store the way buyers do",
            onPress: openBuyerView,
            reducedMotion
          },
          {
            icon: "swap-horizontal-outline",
            label: "Dropshipping",
            // Deliberately not a count. This screen knows nothing about
            // supplier connections, and loading them here to fill in a subtitle
            // would put a supplier request on the store dashboard's critical
            // path for a line of text. The hub behind it counts honestly.
            subtitle: "Import products from suppliers",
            onPress: () => navigation.navigate("Dropshipping", { title: "Dropshipping" }),
            reducedMotion
          },
          // Shipping settings and a returns policy have no screen in this app,
          // and neither has a backend to point at. Marked unavailable with an
          // honest subtitle rather than wired to something unrelated — a tile
          // that opens the wrong screen is worse than one that says "not yet".
          {
            icon: "airplane-outline",
            label: "Shipping",
            subtitle: "Not available in the app yet",
            disabled: true,
            reducedMotion
          },
          {
            icon: "return-down-back-outline",
            label: "Returns",
            subtitle: "Not available in the app yet",
            disabled: true,
            reducedMotion
          }
        ]}
      />
    </View>
  );

  const listingsSection = (() => {
    if (loading) {
      return (
        <View>
          {Array.from({ length: 4 }, (_, index) => (
            <StoreRowSkeleton key={index} reducedMotion={reducedMotion} />
          ))}
        </View>
      );
    }
    if (listingsFailed) {
      return (
        <StoreSectionError
          message="Your listings didn't load."
          onRetry={() => load("refresh")}
          reducedMotion={reducedMotion}
        />
      );
    }
    if (allRows.length === 0) {
      return (
        <StoreEmptyListings
          onAddListing={() => navigation.navigate("MarketplaceCreateGateway", { title: "Create Listing" })}
          reducedMotion={reducedMotion}
        />
      );
    }
    if (visible.length === 0) {
      return (
        <View style={styles.noMatches}>
          <Text style={styles.noMatchesText}>
            {query ? `No listings match “${query.trim()}”.` : "Nothing in this tab right now."}
          </Text>
        </View>
      );
    }
    return null;
  })();

  const listData = listingsSection ? [] : visible;

  return (
    <View style={styles.root}>
      <Animated.View style={entrance.styleFor(SLOT.header)}>
        <StoreHeader
          title={route?.params?.title || "Store"}
          query={query}
          onQueryChange={setQuery}
          onSubmitSearch={() => setExpanded(true)}
          onBack={() => navigation.goBack?.()}
          onNotifications={() => navigation.navigate("BusinessOsActivity")}
          // The bell number and its destination are now the shared Activity feed
          // and its unread store — not open-orders, which double-counted with the
          // orders card and diverged from every other header.
          unreadCount={bellCount}
          searchPlaceholder="Search your listings and orders"
          reducedMotion={reducedMotion}
        />
      </Animated.View>

      <Animated.View style={entrance.styleFor(SLOT.status)}>
        {/* The strip says where the store stands and nothing more. Under the
            readiness flag the sentence comes from the ladder, which read the
            listings; without it the old two-way open/paused split is kept
            byte-for-byte so an un-flagged build is unchanged. */}
        <StoreStatusStrip
          text={
            readinessOn
              ? `${sellerName} · ${readiness.statusLabel}`
              : status.open
                ? `${sellerName} · Open for orders`
                : `${sellerName} · Paused — buyers can't order`
          }
          open={readinessOn ? readiness.openForOrders : status.open}
          actionLabel={readinessOn ? readiness.action.label : status.open ? "Manage" : "Reopen"}
          onAction={() => {
            if (readinessOn) {
              runSetupAction(readiness.action.key);
              return;
            }
            setTab(status.open ? "all" : "out");
            setExpanded(true);
          }}
          reducedMotion={reducedMotion}
        />
      </Animated.View>

      <FlatList
        data={listData}
        keyExtractor={(row) => String(row.id)}
        refreshControl={
          <RefreshControl refreshing={refreshing} onRefresh={() => load("refresh")} />
        }
        contentContainerStyle={[
          styles.content,
          { paddingBottom: Math.max(insets.bottom, 16) + BOTTOM_NAV_CONTENT_CLEARANCE }
        ]}
        ListHeaderComponent={
          <View style={styles.headerBlock}>
            {result?.offline && result.cachedAt ? (
              <StoreOfflineNote
                text={`Offline — showing your store as of ${formatters.relative(result.cachedAt)}.`}
              />
            ) : null}

            {/* Shown only while something is outstanding. A checklist with every
                row ticked is a screen that has stopped telling the seller
                anything, so at `remaining === 0` it disappears and the strip
                carries the state on its own. The loading guard keeps it from
                claiming "not set up" about a store that simply hasn't answered
                yet. */}
            {readinessOn && !loading && !listingsFailed && readiness.remaining > 0 ? (
              <Animated.View style={[styles.checklistWrap, entrance.styleFor(SLOT.setup)]}>
                <StoreSetupChecklist
                  headline={readiness.headline}
                  steps={readiness.steps}
                  remaining={readiness.remaining}
                  onStepAction={onSetupStep}
                  reducedMotion={reducedMotion}
                />
              </Animated.View>
            ) : null}

            <Animated.View style={entrance.styleFor(SLOT.kpis)}>{kpiGrid}</Animated.View>

            {attention ? (
              <Animated.View style={[styles.bannerWrap, entrance.styleFor(SLOT.banner)]}>
                <StoreAttentionBanner
                  headline={`${formatters.count(attention.count)} ${
                    attention.count === 1 ? "listing is" : "listings are"
                  } ${ATTENTION_COPY[attention.kind].headline}`}
                  detail={ATTENTION_COPY[attention.kind].detail}
                  onPress={() => {
                    setTab(attention.target);
                    setExpanded(true);
                  }}
                  reducedMotion={reducedMotion}
                />
              </Animated.View>
            ) : null}

            <Animated.View style={[styles.sectionHead, entrance.styleFor(SLOT.tabs)]}>
              <Text style={styles.sectionTitle}>Listings</Text>
              <Pressable
                onPress={() => {
                  setTab("all");
                  setExpanded(true);
                }}
                hitSlop={8}
                accessibilityRole="link"
                accessibilityLabel={`Manage all listings, ${allRows.length}`}
              >
                <Text style={styles.sectionLink}>Manage all ({formatters.count(allRows.length)})</Text>
              </Pressable>
            </Animated.View>

            {allRows.length > 0 ? (
              <Animated.View style={entrance.styleFor(SLOT.tabs)}>
                <StoreTabBar tabs={tabs} active={tab} onChange={setTab} reducedMotion={reducedMotion} />
              </Animated.View>
            ) : null}

            {/* Below the tabs, not instead of them: Select All is scoped to the
                rows the active tab is showing, so the seller has to be able to
                see which tab that is while they tap it. */}
            {selection ? (
              <StoreSelectionBar
                selectAllState={selectAllState(selection, visible)}
                selectAllLabel={selectAllLabel(selection, visible)}
                onToggleAll={onToggleAll}
                summary={selectionSummary(selection, allRows, visible)}
                onDone={exitSelection}
                reducedMotion={reducedMotion}
              />
            ) : null}

            {listingsSection ? (
              <Animated.View style={entrance.styleFor(SLOT.list)}>{listingsSection}</Animated.View>
            ) : null}
          </View>
        }
        renderItem={({ item }) => (
          <StoreListingRow
            row={item}
            priceText={item.priceLabel}
            soldText={
              item.unitsSold7d > 0 ? `${formatters.count(item.unitsSold7d)} sold · 7d` : null
            }
            onPress={() => openListing(item)}
            onEdit={() => openListing(item)}
            onAction={() => openListing(item)}
            onLongPress={() => enterSelection(item.id)}
            selection={
              selection
                ? {
                    selected: selection.has(item.id),
                    onToggle: () => toggleRow(item.id),
                    // Only for rows actually in the selection. An unselected row
                    // showing "1 thing left" would be previewing a bulk action
                    // it is not part of, which reads as a warning about the row
                    // rather than about the batch.
                    blockedReason: blockedById?.get(item.id) ?? null
                  }
                : null
            }
            reducedMotion={reducedMotion}
          />
        )}
        ListFooterComponent={
          <View style={styles.footerBlock}>
            {!expanded && searched.length > PREVIEW_COUNT ? (
              <Pressable
                style={styles.seeAll}
                onPress={() => setExpanded(true)}
                accessibilityRole="link"
                accessibilityLabel={`See all ${searched.length} listings`}
              >
                <Text style={styles.sectionLink}>
                  See all {formatters.count(searched.length)} listings ›
                </Text>
              </Pressable>
            ) : null}

            <Animated.View style={entrance.styleFor(SLOT.links)}>
              <Text style={[styles.sectionTitle, styles.linkHeading]}>Manage your store</Text>
              {quickLinks}
            </Animated.View>

            <Animated.View style={entrance.styleFor(SLOT.ctas)}>
              <StoreFooterCtas
                onAdd={() => navigation.navigate("MarketplaceCreateGateway", { title: "Create Listing" })}
                onPreview={openBuyerView}
                reducedMotion={reducedMotion}
              />
            </Animated.View>
          </View>
        }
      />

      {/* Docked, outside the list, because it must stay reachable while the
          seller scrolls the rows it is about. `StoreSelectionBar` above the list
          answers "what have I picked"; this answers "what will happen to it". */}
      {selection ? (
        <View style={[styles.bulkDock, { paddingBottom: Math.max(insets.bottom, 8) }]}>
          <StoreBulkBar
            action={pendingAction}
            onChangeAction={setPendingAction}
            // Empty for `price`, where there is no partition and therefore no
            // count. The bar knows to say "Edit pricing" instead rather than
            // being handed a sentence assembled from nothing.
            ctaLabel={
              partitioned && isPrecomputed(pendingAction)
                ? bulkActionLabel(partitioned, pendingAction)
                : ""
            }
            eligibleCount={partitioned?.eligible.length ?? 0}
            onPress={openBulkSheet}
            busy={bulk?.phase === "running"}
            reducedMotion={reducedMotion}
          />
        </View>
      ) : null}

      {bulk ? (
        <StoreBulkSheet
          visible
          phase={bulk.phase}
          action={bulk.action}
          review={bulk.review}
          outcome={bulk.outcome}
          errorMessage={bulk.error}
          priceDraft={priceDraft}
          onChangePriceDraft={setPriceDraft}
          categoryDraft={categoryDraft}
          onChangeCategoryDraft={setCategoryDraft}
          // The aisles suggested are drawn from the whole store, not from the
          // selection: a seller moving products *out* of one aisle is most often
          // moving them into another they already keep, and a selection-scoped
          // list would only ever suggest where these products already are.
          categorySuggestions={categorySuggestions}
          selectedFilings={selectedFilings}
          selectedCount={selectedCount}
          onPreview={() => void previewBulk()}
          onConfirm={confirmBulk}
          onRetry={retryBulk}
          // Only a payload action has settings to go back to. Passing this for
          // publish or hide would put a button on their sheet that landed the
          // seller on a face those actions never had.
          onChangeRule={isPrecomputed(bulk.action) ? undefined : changeRule}
          onClose={closeBulkSheet}
          // The server names most result rows; this fills in the ones it did
          // not, from the list the seller is already looking at. A result line
          // with no title would be a row they cannot identify.
          titleFor={(listingId) =>
            allRows.find((row) => row.id === listingId)?.title || `Listing ${listingId}`
          }
        />
      ) : null}
    </View>
  );
}

/**
 * The two footer buttons. Primary is filled with the CTA token; secondary is a
 * hairline outline, because two filled pills side by side makes neither read as
 * the main action.
 *
 * The primary's fill comes from `storeLight.cta`, which is a single swappable
 * constant — see the trade-dress note in `theme/storeLight.ts`.
 */
function StoreFooterCtas({
  onAdd,
  onPreview,
  reducedMotion
}: {
  onAdd: () => void;
  onPreview: () => void;
  reducedMotion: boolean;
}) {
  const gleam = useStoreAmbient(STORE_AMBIENT.ctaGleam, reducedMotion, { resetTo: 0 });

  return (
    <View style={styles.ctas}>
      <Pressable
        style={styles.primaryCta}
        onPress={onAdd}
        accessibilityRole="button"
        accessibilityLabel="Add a listing"
      >
        <Animated.View
          pointerEvents="none"
          style={[
            styles.gleam,
            {
              opacity: gleam.interpolate({
                inputRange: [0, 0.45, 0.5, 0.55, 1],
                outputRange: [0, 0, 0.35, 0, 0]
              }),
              transform: [
                { translateX: gleam.interpolate({ inputRange: [0, 1], outputRange: [-200, 260] }) },
                { rotate: "18deg" }
              ]
            }
          ]}
        />
        <Text style={styles.primaryCtaText}>＋ Add a listing</Text>
      </Pressable>

      <Pressable
        style={styles.secondaryCta}
        onPress={onPreview}
        accessibilityRole="button"
        accessibilityLabel="Preview your storefront as a buyer"
      >
        <Text style={styles.secondaryCtaText}>Preview storefront as buyer</Text>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: storeLight.bg.page },
  content: { paddingBottom: 24 },
  headerBlock: { gap: storeLight.space.section },
  kpiGrid: { gap: storeLight.space.gutter, paddingHorizontal: storeLight.space.card, paddingTop: storeLight.space.section },
  kpiRow: { flexDirection: "row", gap: storeLight.space.gutter },
  bannerWrap: { paddingHorizontal: storeLight.space.card },
  // The card carries its own horizontal margin, so this only adds the gap above.
  checklistWrap: { paddingTop: storeLight.space.section },
  sectionHead: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: storeLight.space.card
  },
  sectionTitle: { fontSize: 16, fontWeight: "700", color: storeLight.text.primary },
  sectionLink: { fontSize: 13, fontWeight: "600", color: storeLight.text.link },
  // Only the safe-area padding lives here; the bar draws its own hairline and
  // fill, so this wrapper matches its background to avoid a stripe of page
  // colour under the home indicator.
  bulkDock: { backgroundColor: storeLight.bg.card },
  noMatches: { padding: 24, backgroundColor: storeLight.bg.card, alignItems: "center" },
  noMatchesText: { fontSize: 13, color: storeLight.text.muted },
  footerBlock: { gap: storeLight.space.section, paddingTop: storeLight.space.section },
  seeAll: {
    minHeight: storeLight.size.tapTarget,
    justifyContent: "center",
    paddingHorizontal: storeLight.space.card,
    backgroundColor: storeLight.bg.card,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: storeLight.border.hairline
  },
  linkHeading: { paddingHorizontal: storeLight.space.card },
  /**
   * Page inset only. The gap between tiles and between rows now belongs to
   * `StoreQuickLinkGrid`, so it is not repeated here — two owners of the same
   * spacing is how the rows drifted out of alignment with each other in the
   * first place. `linkRow` is gone for the same reason: the row is no longer
   * something a screen composes.
   */
  linkGrid: { paddingHorizontal: storeLight.space.card, marginTop: 8 },
  ctas: { gap: 10, paddingHorizontal: storeLight.space.card },
  primaryCta: {
    minHeight: storeLight.size.tapTarget + 4,
    alignItems: "center",
    justifyContent: "center",
    borderRadius: storeLight.radius.pill,
    backgroundColor: storeLight.cta.from,
    overflow: "hidden"
  },
  gleam: { position: "absolute", top: -40, bottom: -40, width: 40, backgroundColor: "#FFFFFF" },
  primaryCtaText: { fontSize: 15, fontWeight: "800", color: storeLight.cta.text },
  secondaryCta: {
    minHeight: storeLight.size.tapTarget,
    alignItems: "center",
    justifyContent: "center",
    borderRadius: storeLight.radius.pill,
    borderWidth: 1,
    borderColor: storeLight.border.secondaryButton,
    backgroundColor: storeLight.bg.card
  },
  secondaryCtaText: { fontSize: 14, fontWeight: "600", color: storeLight.text.primary }
});
