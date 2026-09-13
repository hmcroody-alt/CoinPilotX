/**
 * Where a tapped blocker lands — §32's "tap it and go fix it".
 *
 * `listing_readiness.SECTIONS` gives every blocker a `section`, and this turns
 * that section into somewhere on the editor. It is deliberately the *only*
 * thing this module does: no labels, no counts, no opinion about whether the
 * blocker matters. The server already wrote the words; this decides the
 * destination.
 *
 * Pure and separate from the screen because the screen's half of the routing —
 * calling `.focus()` on a `TextInput` ref — is invisible to the test renderer,
 * which reports no focus handle on a host element. Left inline, five of the six
 * branches below could only be checked by hand. Out here the whole vocabulary is
 * covered, and the screen test is left with the two branches it can genuinely
 * observe (a navigation and a message).
 */

/**
 * `details` and `overview` collapse into one target on purpose: this editor is
 * a single form, so the top of it *is* the details section. They stay distinct
 * in the server's vocabulary because a twelve-section workspace would separate
 * them, and collapsing them there rather than here would lose that.
 */
export type StoreFixTarget = "camera" | "price" | "quantity" | "policy" | "details";

const TARGETS: Record<string, StoreFixTarget> = {
  media: "camera",
  pricing: "price",
  inventory: "quantity",
  policies: "policy",
  details: "details",
  overview: "details"
};

/**
 * Falls back to `details` — the top of the form — for a section this build has
 * never heard of, which is what happens the first time the engine grows a new
 * one. The seller lands somewhere real and reads the server's own label for
 * what to do, rather than tapping a row that does nothing.
 */
export function storeFixTarget(section: string): StoreFixTarget {
  return TARGETS[section] || "details";
}
