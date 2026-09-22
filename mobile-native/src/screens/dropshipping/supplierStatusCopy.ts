/**
 * Merchant-readable words for the server's supplier status, and nothing else.
 *
 * Every function here takes a value the server sent and returns a sentence. None
 * of them decides anything: there is deliberately no `isHealthy(supplier)` in
 * this file, because the moment a screen can compute health locally, two screens
 * will compute it differently — which is the defect `/supplier-status` was built
 * to end.
 *
 * ## Unknown is a word, not a blank
 *
 * A sync state this app has not seen, an action it does not recognise, a count
 * the server omitted: all of them render as something the merchant can read,
 * never as silence and never as good news. Silence over a broken connection is
 * indistinguishable from a healthy one, and that is the exact mistake that had a
 * merchant browsing a catalogue every search then refused.
 *
 * ## Provider is a value
 *
 * `operatingMode` takes the provider name off the row and puts it in the
 * sentence. Nothing here contains the string "CJ" — connecting a second supplier
 * must change what these functions are *given*, not what they say.
 */

import type {
  StoreSupplierStatus,
  SupplierNextAction,
  SupplierOrderCounts,
  SupplierStatus
} from "../../api/dropshipping";

/**
 * The one thing to do next, said twice: once as a sentence explaining why, and
 * once as the words on the button.
 *
 * Both, because a button reading "Reconnect" beside a row reading "Connected"
 * is the state a merchant reports as a bug. The `body` is what makes the button
 * make sense, so they are defined together and rendered together.
 */
export const NEXT_ACTION_COPY: Record<SupplierNextAction, { body: string; button: string }> = {
  RECONNECT_SUPPLIER: {
    body: "This supplier can't be reached with the credential you saved. Nothing imports or syncs until it's reconnected.",
    button: "Reconnect supplier"
  },
  CHOOSE_FULFILLMENT_SHOP: {
    body: "Importing and publishing work. Orders can't be sent until you pick the shop they go to.",
    button: "Choose fulfilment shop"
  },
  RETRY_SYNC: {
    body: "The last sync didn't finish, so the costs and stock counts on your products may be out of date.",
    button: "Sync now"
  },
  RESOLVE_PRODUCT_ISSUES: {
    body: "Some imported products need a decision from you before they're safe to sell.",
    button: "Review issues"
  },
  IMPORT_FIRST_PRODUCT: {
    body: "You're set up. Browse your supplier's catalogue and import something to sell.",
    button: "Find products"
  },
  REVIEW_DRAFTS: {
    body: "Some imports are saved as drafts and aren't visible to buyers yet.",
    button: "Review drafts"
  }
};

/**
 * Whether this action is a blocker or a suggestion.
 *
 * Not the same question as "is there an action". A store whose only outstanding
 * item is "you have drafts" is working, and giving it the same red treatment as
 * a revoked credential is how a permanent badge stops being read.
 *
 * The server answers this per supplier in `needsAttention`; this exists so a
 * screen rendering a *single* action — with no supplier row to read the flag
 * off — reaches the same verdict rather than inventing a third one.
 */
export function actionIsBlocking(action: SupplierNextAction | null): boolean {
  return (
    action === "RECONNECT_SUPPLIER" ||
    action === "CHOOSE_FULFILLMENT_SHOP" ||
    action === "RETRY_SYNC" ||
    action === "RESOLVE_PRODUCT_ISSUES"
  );
}

/**
 * The operating-mode line: who is connected, in which environment, and whether
 * real orders can leave the building.
 *
 * All three in one line and always all three, including when the answer is the
 * dull one. A line that only appears in sandbox teaches the merchant to read its
 * *absence* as production, and absence is also what a failed request looks like.
 *
 * Reads the store-wide `environment` and `realOrderSubmissionEnabled` rather
 * than the supplier row's copies of them, because they are platform-level: a
 * second supplier must not be able to appear to have different permissions from
 * the first.
 */
export function operatingMode(status: StoreSupplierStatus): {
  line: string;
  sandbox: boolean;
  realOrders: boolean;
} {
  const sandbox = status.environment !== "PRODUCTION";
  const realOrders = status.realOrderSubmissionEnabled;
  const connected = status.suppliers.filter(
    (supplier) => supplier.connectionState === "CONNECTED"
  );
  const who =
    connected.length === 0
      ? "No supplier connected"
      : connected.length === 1
        ? `${connected[0].provider.toUpperCase()} connected`
        : `${connected.length} suppliers connected`;
  return {
    line: `${who} · ${sandbox ? "Sandbox mode" : "Production mode"} · Real fulfilment ${
      realOrders ? "ON" : "OFF"
    }`,
    sandbox,
    realOrders
  };
}

/**
 * What the sandbox actually means for this merchant, in their terms.
 *
 * "Sandbox" alone is a developer's word. A merchant reading it beside a healthy
 * green row concludes the feature works and their orders ship — so the sentence
 * has to say the one thing that is different, which is that nothing ships.
 */
export const SANDBOX_EXPLANATION =
  "Products import, price and publish exactly as they will in production. No order is really placed with your supplier and nothing ships.";

/**
 * Why real fulfilment is off, when it is off in production too.
 *
 * Separate from the sandbox sentence because they are separate switches and can
 * disagree: a production connection with the platform switch closed is not in
 * sandbox and must not be described as though it were.
 */
export const REAL_FULFILMENT_OFF_EXPLANATION =
  "Sending real orders to suppliers is switched off platform-wide. Everything else — importing, pricing, publishing, selling — works normally.";

/**
 * The sync rollup, as a sentence.
 *
 * `null` is its own answer and is not "synced". A connection with no imported
 * products has no sync state at all, and the first version of this screen said
 * "Up to date" about it — a green tick over a catalogue that did not exist.
 */
export function syncCopy(syncState: string | null): { label: string; tone: "ok" | "warn" | "muted" } {
  switch (syncState) {
    case "SYNCED":
      return { label: "Up to date", tone: "ok" };
    case "PENDING":
      return { label: "Waiting to sync", tone: "muted" };
    case "STALE":
      return { label: "Out of date", tone: "warn" };
    case "REMOVED":
      return { label: "Some products were removed by your supplier", tone: "warn" };
    case "DISCONNECTED":
      return { label: "Can't reach your supplier", tone: "warn" };
    case "ERROR":
      return { label: "Last sync failed", tone: "warn" };
    case null:
      return { label: "Nothing imported yet", tone: "muted" };
    default:
      // An unrecognised state is not good news and must not be drawn as though
      // it were. It is reported as unknown rather than passed through raw,
      // because the raw word is the provider's vocabulary, not the merchant's.
      return { label: "Sync state unknown", tone: "warn" };
  }
}

/**
 * The supplier-orders tile, as a sentence and a flag.
 *
 * `null` — the server could not read the fulfilment tables — is deliberately not
 * "0 waiting". A merchant told nothing is waiting stops looking, and the sales
 * they stopped looking for are ones a buyer has already paid for. So an
 * unreadable count describes the screen instead of guessing at its contents.
 *
 * Blocked orders lead when there are any, because they are the ones that will
 * not move on their own. "3 ready to place" beside four silently stuck sales is
 * a tile that reports the easy half of the work.
 */
export function ordersCopy(orders: SupplierOrderCounts | null): {
  label: string;
  attention: boolean;
} {
  if (!orders) return { label: "Sales waiting on a supplier purchase", attention: false };
  if (orders.blocked > 0) {
    return { label: `${orders.blocked} can't be ordered yet`, attention: true };
  }
  if (orders.readyToPlace > 0) {
    return { label: `${orders.readyToPlace} ready to order`, attention: true };
  }
  if (orders.placed > 0) return { label: `${orders.placed} already ordered`, attention: false };
  return { label: "Nothing waiting", attention: false };
}

/** One line of the health summary. `tone` drives colour only; the words stand alone. */
export type HealthRow = { key: string; label: string; value: string; tone: "ok" | "warn" | "muted" };

/**
 * The supplier's operating state as rows a merchant can read top to bottom.
 *
 * Every row is a field the server sent. There is no row here computed from two
 * others, and adding one would put back the inference this endpoint removed.
 */
export function healthRows(
  supplier: SupplierStatus,
  relative: (iso: string) => string
): HealthRow[] {
  const sync = syncCopy(supplier.syncState);
  const rows: HealthRow[] = [
    {
      key: "connection",
      label: "Connection",
      value: supplier.connectionState === "CONNECTED" ? "Working" : "Needs attention",
      tone: supplier.connectionState === "CONNECTED" ? "ok" : "warn"
    },
    {
      key: "fulfilment",
      label: "Fulfilment shop",
      value: supplier.fulfillmentShopState === "BOUND" ? "Chosen" : "Not chosen yet",
      tone: supplier.fulfillmentShopState === "BOUND" ? "ok" : "warn"
    },
    { key: "sync", label: "Product sync", value: sync.label, tone: sync.tone },
    {
      key: "products",
      label: "Imported products",
      value:
        supplier.products.imported === 0
          ? "None yet"
          : `${supplier.products.imported} · ${supplier.products.published} live`,
      tone: "muted"
    }
  ];

  if (supplier.issues.products > 0) {
    rows.push({
      key: "issues",
      label: "Needs a decision",
      // Counted per product, not per problem: one product flagged for both a
      // cost jump and a stock drop is one thing to look at, and reporting it as
      // two sends the merchant hunting for a second product that is not there.
      value: `${supplier.issues.products} product${supplier.issues.products === 1 ? "" : "s"}`,
      tone: "warn"
    });
  }

  // The catalogue's own clock, not the connection's. A connection-level check
  // can succeed at 09:00 while every product row was last touched on Tuesday,
  // and showing the first as "last sync" is how stale costs look fresh.
  if (supplier.lastProductSyncAt) {
    rows.push({
      key: "last-sync",
      label: "Last product sync",
      value: relative(supplier.lastProductSyncAt),
      tone: "muted"
    });
  }

  return rows;
}
