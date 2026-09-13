/**
 * Every publish problem, in the merchant's words, with what to do about it.
 *
 * Why this is a module and not a constant in a screen
 * ---------------------------------------------------
 * It used to live inside `ReviewImportedProductScreen`, which was the only place
 * a publish refusal could reach a merchant: publishing was a button on that
 * screen, so a refusal was always read there.
 *
 * "Import to Store" ended that. The importer publishes through the same gate, so
 * the same `problems` array now arrives on an import *result* — in the cart's
 * result sheet, next to the nineteen products that did go live. Two screens
 * rendering the same codes from two tables is the drift this table already
 * suffered once, one level up: as a `Record<string, …>` it fell three codes
 * behind `PUBLISH_PROBLEMS` and a merchant read the literal text
 * "SUPPLIER_VARIANT_UNBOUND". One table, imported by both.
 *
 * `Record<PublishProblem, …>` rather than `Record<string, …>` is the load-bearing
 * part. Total over the union, a code added to `PUBLISH_PROBLEMS` without copy
 * fails the typecheck. `tests/dropshipping/test_publish_problem_copy.py` closes
 * the other half — a code added on the Python side, which the union cannot learn
 * about by itself.
 *
 * `fixable: false` is not a smaller `true`
 * ----------------------------------------
 * It means the merchant cannot act, and the copy says what is true instead of
 * naming a step. Telling someone to "add a price" when their supplier delisted
 * the product wastes their afternoon, and telling them to retry something that
 * needs a supplier reconnection wastes it twice.
 */

import { PUBLISH_PROBLEMS, type PublishProblem } from "../../api/dropshipping";

export type PublishProblemCopy = {
  text: string;
  fixable: boolean;
};

export const PROBLEM_COPY: Record<PublishProblem, PublishProblemCopy> = {
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
  // Fixable, and fixable on the draft screen — see its variant chooser. Before
  // that existed this was the one problem in this table with no remedy anywhere
  // in the app, which is why it is worded as a question rather than an error.
  //
  // It is also the most likely thing a one-tap import will report, because the
  // importer only binds a variant where doing so is not a guess: one variant, or
  // one orderable variant among sold-out siblings. Three colours all in stock is
  // a real choice and the merchant makes it.
  SUPPLIER_VARIANT_UNBOUND: {
    text: "Choose which variant you're selling. A product sells one variant, and orders go to your supplier for that one.",
    fixable: true
  },
  // The read-back's two codes. Neither is anything the merchant did, and neither
  // is anything they can fix by editing the product — so both are
  // `fixable: false`, and both say what state the product is actually in rather
  // than naming a step. They exist because "published" is verified rather than
  // assumed: without the read-back the merchant would have been told the product
  // was live while it sat in their store as a draft, which is the worse failure
  // by a long way.
  PUBLISH_NOT_PERSISTED: {
    text: "We couldn't confirm this product went live, so it's still a draft. Try publishing it again.",
    fixable: false
  },
  SUPPLIER_MAPPING_MISSING: {
    text: "This product lost its link to your supplier, so orders couldn't be sent. Import it again from your supplier.",
    fixable: false
  }
};

/**
 * Copy for a code off the wire, or `null` if this build has never heard of it.
 *
 * `null` rather than a generic "something went wrong": the caller renders the raw
 * code in that case, which is ugly and is the right kind of ugly. A merchant who
 * reads `SOME_NEW_CODE` can quote it to support; a merchant who reads "this
 * product can't be published" has been told nothing and has nothing to quote.
 *
 * Guarded at runtime even though the table is total over the union, because the
 * union is this app's copy of a list the server owns — and a server ahead of this
 * build can name a code the union has never heard of.
 */
export function publishProblemCopy(problem: string): PublishProblemCopy | null {
  const code = String(problem).toUpperCase() as PublishProblem;
  return PUBLISH_PROBLEMS.includes(code) ? PROBLEM_COPY[code] : null;
}

/**
 * Whether any of these problems is the merchant's to fix.
 *
 * Drives whether a "Fix this" affordance is offered at all. A product held up by
 * a delisted supplier product gets no button, because there is no screen on which
 * pressing one would help.
 */
export function anyFixable(problems: readonly string[]): boolean {
  return problems.some((problem) => publishProblemCopy(problem)?.fixable === true);
}
